"""Read the public Philippine store locators and retain their source snapshots."""

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from shapely.geometry import Point, shape
from shapely.strtree import STRtree

from src.pipelines.config import PROJECT_ROOT

JOLLIBEE_URL = "https://www.jollibee.com.ph/stores/"
MCDONALDS_URL = "https://www.mcdonalds.com.ph/store"
KFC_URL = "https://www.kfc.com.ph/en/store-locator"
BOUNDARY_API = "https://www.geoboundaries.org/api/current/gbOpen/PHL/{level}/"
DEFAULT_CACHE = PROJECT_ROOT / "data/raw/philippines_fastfood"


def parse_jollibee(payload: dict) -> list[dict]:
    stores = payload.get("stores")
    if not isinstance(stores, list) or not stores or payload.get("count") != len(stores):
        raise ValueError("Jollibee returned an empty or incomplete store list.")
    return [{
        "business_name": store["title"], "brand": "Jollibee",
        "latitude": store.get("lat"), "longitude": store.get("lng"),
        "source_id": f"jollibee:{store['id']}",
        "source_url": urljoin(JOLLIBEE_URL, store["url"]),
        "website": "https://www.jollibee.com.ph/",
    } for store in stores]


def parse_mcdonalds(html: str) -> list[dict]:
    element = BeautifulSoup(html, "html.parser").find("store-locator")
    if element is None or not element.get(":stores"):
        raise ValueError("McDonald's store locator no longer exposes its store list.")
    stores = json.loads(element[":stores"])
    if not isinstance(stores, list) or not stores:
        raise ValueError("McDonald's returned an empty store list.")
    return [{
        "business_name": store["name"], "brand": "McDonald's",
        "latitude": store.get("latitude"), "longitude": store.get("longitude"),
        "address": store.get("address", ""),
        "city": store.get("city_name", ""), "region": store.get("province_name", ""),
        "opening_hours": (store.get("description") or "").removeprefix("Operating Hours: "),
        "source_id": f"mcdonalds:{store['id']}",
        "source_url": MCDONALDS_URL,
        "website": "https://www.mcdonalds.com.ph/",
    } for store in stores if store.get("status") == 1]


def parse_kfc(html: str) -> list[dict]:
    element = BeautifulSoup(html, "html.parser").find("script", id="__NEXT_DATA__")
    if element is None:
        raise ValueError("KFC store locator no longer exposes its store list.")
    try:
        stores = json.loads(element.get_text())["props"]["pageProps"]["data"]["chainStores"]["msg"]
    except (KeyError, TypeError) as error:
        raise ValueError("KFC store locator data format has changed.") from error
    if not isinstance(stores, list) or not stores:
        raise ValueError("KFC returned an empty store list.")
    records = []
    for store in stores:
        address = store.get("address", {})
        coordinates = address.get("latLng", {})
        opening_hours = store.get("openingHours") or []
        days = opening_hours[0].get("en", []) if opening_hours else []
        hours = "; ".join(
            f"{day}: {', '.join(periods)}"
            for day, periods in zip(("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"), days)
        )
        records.append({
            "business_name": store["title"]["en_US"], "brand": "KFC",
            "latitude": coordinates.get("lat"), "longitude": coordinates.get("lng"),
            "address": address.get("formatted", ""),
            # The locator's city field can contain unrelated overseas cities.
            "phone": store.get("contact", {}).get("phone", ""),
            "opening_hours": hours,
            "source_id": f"kfc:{store['id']}", "source_url": KFC_URL,
            "website": "https://www.kfc.com.ph/",
        })
    return records


class BoundaryLookup:
    def __init__(self, geojson: dict):
        features = geojson["features"]
        if not features:
            raise ValueError("The reference boundary file is empty.")
        self.names = [feature["properties"]["shapeName"] for feature in features]
        self.tree = STRtree([shape(feature["geometry"]) for feature in features])

    def name_at(self, longitude: float, latitude: float) -> str:
        matches = self.tree.query(Point(longitude, latitude), predicate="intersects")
        return self.names[int(matches[0])] if len(matches) else ""


def enrich_places(
    records: list[dict], municipalities: BoundaryLookup, provinces: BoundaryLookup,
    *, boundary_year: str = "2020",
) -> tuple[list[dict], list[dict]]:
    accepted, excluded = [], []
    seen = set()
    for record in records:
        if record["source_id"] in seen:
            continue
        seen.add(record["source_id"])
        try:
            latitude, longitude = float(record["latitude"]), float(record["longitude"])
        except (TypeError, ValueError):
            excluded.append({**record, "exclusion_reason": "Invalid coordinates"})
            continue
        if not (math.isfinite(latitude) and math.isfinite(longitude) and 4 <= latitude <= 22 and 116 <= longitude <= 127):
            excluded.append({**record, "exclusion_reason": "Coordinates outside Philippine extent"})
            continue
        province = provinces.name_at(longitude, latitude)
        locality = municipalities.name_at(longitude, latitude)
        city = locality
        if province.startswith("NCR, City of Manila"):
            city = "Manila City"
        if province.startswith("NCR,"):
            province = "Metro Manila"
        accepted.append({
            **record, "latitude": latitude, "longitude": longitude,
            "city": city or record.get("city", "Unknown"),
            "region": province or record.get("region", ""),
            "locality": locality,
            "city_source": f"geoBoundaries ({boundary_year})" if city else (
                "Official locator" if record.get("city") else "Unassigned"
            ),
            "coordinate_quality": "Within reference boundary" if locality else "Unmatched reference boundary",
        })
    return accepted, excluded


def collect_philippines_fastfood(cache_dir: Path = DEFAULT_CACHE, *, refresh: bool = False) -> tuple[list[dict], dict]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers.update({"User-Agent": "Geospatial-Market-Analytics-Pipeline/1.0"})

    def cached_text(filename: str, url: str) -> str:
        path = cache_dir / filename
        if path.is_file() and not refresh:
            return path.read_text(encoding="utf-8")
        response = session.get(url, timeout=(15, 60))
        response.raise_for_status()
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(response.text, encoding="utf-8")
        temporary.replace(path)
        return response.text

    try:
        jollibee_page = cached_text("jollibee.html", JOLLIBEE_URL)
        map_element = BeautifulSoup(jollibee_page, "html.parser").find(attrs={"data-store-map-src": True})
        if map_element is None:
            raise ValueError("Jollibee store locator no longer exposes its map data URL.")
        map_url = urljoin(JOLLIBEE_URL, map_element["data-store-map-src"])
        if not map_url.startswith("https://www.jollibee.com.ph/"):
            raise ValueError("Jollibee map data URL points outside its official domain.")
        groups = [
            ("Jollibee", "jollibee_stores.json", parse_jollibee(json.loads(cached_text("jollibee_stores.json", map_url)))),
            ("McDonald's", "mcdo_stores.html", parse_mcdonalds(cached_text("mcdo_stores.html", MCDONALDS_URL))),
            ("KFC", "kfc.html", parse_kfc(cached_text("kfc.html", KFC_URL))),
        ]
        lookups, boundary_sources = {}, []
        for level in ("ADM2", "ADM3"):
            api_url = BOUNDARY_API.format(level=level)
            metadata = json.loads(cached_text(f"{level}_metadata.json", api_url))
            geometry = json.loads(cached_text(f"{level}.geojson", metadata["simplifiedGeometryGeoJSON"]))
            lookups[level] = BoundaryLookup(geometry)
            boundary_sources.append({
                "url": api_url, "geometry_url": metadata["simplifiedGeometryGeoJSON"],
                "year": metadata["boundaryYearRepresented"], "license": metadata["boundaryLicense"],
                "source": metadata["boundarySource"],
            })
        records, source_counts, retrieved_dates = [], {}, {}
        for brand, filename, group in groups:
            if not group:
                raise ValueError(f"{brand} returned no usable source records.")
            collected_at = datetime.fromtimestamp((cache_dir / filename).stat().st_mtime, timezone.utc).isoformat()
            retrieved_dates[brand] = collected_at
            source_counts[brand] = len(group)
            records.extend({
                **record, "category": "Fast food", "country": "PH",
                "source_name": f"{brand} official store locator", "collected_at_utc": collected_at,
            } for record in group)
        boundary_year = boundary_sources[-1]["year"]
        accepted, excluded = enrich_places(
            records, lookups["ADM3"], lookups["ADM2"], boundary_year=boundary_year,
        )
        metadata = {
            "dataset": "Philippine fast food locations", "source_counts": source_counts,
            "retrieved_at_utc": retrieved_dates, "excluded_coordinates": len(excluded),
            "included_counts": {brand: sum(record["brand"] == brand for record in accepted) for brand, _, _ in groups},
            "boundary_sources": boundary_sources,
            "coverage_note": f"Official locator entries with usable coordinates; coverage may be incomplete. Ratings and review counts are unavailable. Geographic labels use {boundary_year} reference boundaries.",
        }
        (cache_dir / "excluded_locations.json").write_text(json.dumps(excluded, ensure_ascii=False, indent=2), encoding="utf-8")
        (cache_dir / "collected_locations.json").write_text(json.dumps(accepted, ensure_ascii=False, indent=2), encoding="utf-8")
        (cache_dir / "source_manifest.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        return accepted, metadata
    finally:
        session.close()
