import math

import polars as pl
import plotly.graph_objects as go

BRAND_COLORS = {"Jollibee": "#e31837", "McDonald's": "#f6b900", "KFC": "#111827"}


def map_view(locations: pl.DataFrame) -> dict:
    """Frame locations in Mercator coordinates, including across the date line."""
    longitudes = sorted((value + 180) % 360 for value in locations["longitude"])
    gaps = [
        (longitudes[(index + 1) % len(longitudes)] - value) % 360
        for index, value in enumerate(longitudes)
    ]
    # A single point (or identical longitudes) needs no longitude span.
    if longitudes[0] == longitudes[-1]:
        span = 0
        west = longitudes[0]
    else:
        gap_index = max(range(len(gaps)), key=gaps.__getitem__)
        span = 360 - gaps[gap_index]
        west = longitudes[(gap_index + 1) % len(longitudes)]
    center_lon = (west + span / 2) % 360 - 180

    projected_latitudes = [
        math.asinh(math.tan(math.radians(max(-85.051, min(85.051, value)))))
        for value in locations["latitude"]
    ]
    south, north = min(projected_latitudes), max(projected_latitudes)
    center_lat = math.degrees(math.atan(math.sinh((south + north) / 2)))
    fraction = max(span / 360, (north - south) / (2 * math.pi), 1e-9)
    # Padding leaves room for markers and the rating legend in narrow windows.
    zoom = min(14, max(0, math.log2(1 / fraction) - 1.5))
    return {"center": {"lat": center_lat, "lon": center_lon}, "zoom": zoom}


def location_map(locations: pl.DataFrame, color_by: str = "Brand") -> go.Figure:
    figure = go.Figure()
    if color_by == "Brand":
        for brand in sorted(locations["brand"].unique().to_list()):
            group = locations.filter(pl.col("brand") == brand)
            figure.add_trace(go.Scattermap(
                lat=group["latitude"].to_list(), lon=group["longitude"].to_list(),
                text=group["business_name"].to_list(), name=brand or "Unspecified",
                customdata=group.select("city", "address").rows(), mode="markers",
                marker={"size": 9, "color": BRAND_COLORS.get(brand, "#64748b")},
                cluster={"enabled": len(locations) > 250, "maxzoom": 11},
                hovertemplate="%{text}<br>%{customdata[0]}<br>%{customdata[1]}<extra></extra>",
            ))
    else:
        for has_rating in (True, False):
            group = locations.filter(pl.col("rating").is_not_null() if has_rating else pl.col("rating").is_null())
            if group.is_empty():
                continue
            marker = {"size": 9, "color": "#64748b"}
            if has_rating:
                marker.update(
                    color=group["rating"].to_list(), colorscale="Viridis", cmin=0, cmax=5,
                    showscale=True, colorbar={"title": "Rating"},
                )
            figure.add_trace(go.Scattermap(
                lat=group["latitude"].to_list(), lon=group["longitude"].to_list(),
                text=group["business_name"].to_list(), mode="markers", marker=marker,
                name="Rated locations" if has_rating else "No rating",
                customdata=group["rating"].to_list() if has_rating else None,
                hovertemplate="%{text}<br>Rating: %{customdata:.1f}<extra></extra>" if has_rating else "%{text}<br>No rating<extra></extra>",
            ))
    figure.update_layout(
        map={"style": "open-street-map", **map_view(locations)},
        height=600, margin={"l": 0, "r": 0, "t": 30, "b": 0},
        legend={"orientation": "h", "y": 1.02},
    )
    return figure
