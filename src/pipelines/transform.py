import math
import re

import polars as pl

ALIASES = {
    "business_name": ("business_name", "name", "title", "company", "store_name"),
    "category": ("category", "category_name", "categories", "type", "industry", "primary_type"),
    "address": ("address", "street", "full_address", "formatted_address"),
    "city": ("city", "locality"),
    "region": ("region", "state", "province", "state_code"),
    "postal_code": ("postal_code", "postalcode", "zip", "postcode"),
    "country": ("country", "country_code"),
    "latitude": ("latitude", "lat"),
    "longitude": ("longitude", "lon", "lng"),
    "rating": ("rating", "total_score", "stars", "score"),
    "review_count": ("review_count", "reviews_count", "reviewscount", "user_ratings_total"),
    "website": ("website", "website_url"),
    "source_url": ("source_url", "url", "place_url"),
}

REQUIRED_COLUMNS = tuple(ALIASES)
ALIASES.update({
    "brand": ("brand", "chain"),
    "locality": ("locality", "district", "locality_detail"),
    "phone": ("phone", "phone_number"),
    "opening_hours": ("opening_hours", "hours"),
    "source_name": ("source_name", "data_source"),
    "source_id": ("source_id",),
    "collected_at_utc": ("collected_at_utc",),
    "city_source": ("city_source",),
    "coordinate_quality": ("coordinate_quality",),
})
OUTPUT_COLUMNS = tuple(ALIASES)
OUTPUT_SCHEMA = {field: pl.String for field in OUTPUT_COLUMNS}
OUTPUT_SCHEMA.update(
    latitude=pl.Float64, longitude=pl.Float64, rating=pl.Float64, review_count=pl.Int64,
)


def _key(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def _number(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _text(value: object, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, (list, tuple, set)):
        value = ", ".join(str(part) for part in value if part is not None)
    return str(value).strip() or default


def transform_records(records: list[dict]) -> tuple[pl.DataFrame, pl.DataFrame, dict]:
    normalized = []
    dropped_coordinates = 0

    for index, record in enumerate(records, start=1):
        if not isinstance(record, dict):
            raise ValueError(f"Input record {index} must be an object.")
        keyed = {_key(key): value for key, value in record.items()}
        values = {}
        for field, names in ALIASES.items():
            for name in names:
                value = keyed.get(_key(name))
                if value is not None and not (isinstance(value, str) and not value.strip()):
                    values[field] = value
                    break

        location = keyed.get("location")
        if isinstance(location, dict):
            coordinates = {_key(key): value for key, value in location.items()}
            for field in ("latitude", "longitude"):
                if field not in values:
                    for name in ALIASES[field]:
                        if coordinates.get(_key(name)) is not None:
                            values[field] = coordinates[_key(name)]
                            break

        latitude = _number(values.get("latitude"))
        longitude = _number(values.get("longitude"))
        if (
            latitude is None
            or longitude is None
            or not -90 <= latitude <= 90
            or not -180 <= longitude <= 180
        ):
            dropped_coordinates += 1
            continue

        rating = _number(values.get("rating"))
        reviews = _number(values.get("review_count"))
        normalized.append(
            {
                "business_name": _text(values.get("business_name"), "Unknown location"),
                "category": _text(values.get("category"), "Uncategorized"),
                "address": _text(values.get("address")),
                "city": _text(values.get("city"), "Unknown"),
                "region": _text(values.get("region")),
                "postal_code": _text(values.get("postal_code")),
                "country": _text(values.get("country")),
                "latitude": latitude,
                "longitude": longitude,
                "rating": rating if rating is not None and 0 <= rating <= 5 else None,
                "review_count": (
                    int(reviews)
                    if reviews is not None and reviews.is_integer() and 0 <= reviews < 2**63
                    else None
                ),
                "website": _text(values.get("website")),
                "source_url": _text(values.get("source_url")),
                **{field: _text(values.get(field)) for field in OUTPUT_COLUMNS if field not in REQUIRED_COLUMNS},
            }
        )

    if not normalized:
        raise ValueError("No locations with valid latitude and longitude were found.")

    locations = pl.DataFrame(normalized, schema=OUTPUT_SCHEMA).select(OUTPUT_COLUMNS)
    summary = (
        locations.group_by(["city", "region", "category", "brand"])
        .agg(
            pl.len().alias("location_count"),
            pl.col("rating").mean().round(2).alias("average_rating"),
        )
        .sort(["city", "region", "category"])
    )
    stats = {
        "input_records": len(records),
        "valid_locations": len(normalized),
        "dropped_invalid_coordinates": dropped_coordinates,
    }
    return locations, summary, stats
