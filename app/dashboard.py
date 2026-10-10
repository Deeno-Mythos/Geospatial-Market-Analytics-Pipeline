import json

import polars as pl
import plotly.express as px
import streamlit as st

from app.maps import BRAND_COLORS, location_map
from app.economics import render_economics
from src.pipelines.config import get_settings, project_path
from src.pipelines.transform import OUTPUT_COLUMNS, OUTPUT_SCHEMA, REQUIRED_COLUMNS

st.set_page_config(page_title="Market Analytics", layout="wide")
st.title("Geospatial Market Analytics")
st.caption("Explore normalized business locations and compare market coverage.")

settings = get_settings()
output_dir = project_path(settings.output_dir)
parquet_path = output_dir / "market_locations.parquet"
csv_path = output_dir / "market_locations.csv"

try:
    if parquet_path.is_file():
        locations = pl.read_parquet(parquet_path)
    elif csv_path.is_file():
        locations = pl.read_csv(csv_path, schema_overrides=OUTPUT_SCHEMA)
    else:
        st.error("No pipeline output found. Run `python -m flows.orchestrator` first.")
        st.stop()
    missing_columns = set(REQUIRED_COLUMNS) - set(locations.columns)
    if missing_columns:
        raise ValueError(f"Missing columns: {', '.join(sorted(missing_columns))}. Rerun the pipeline.")
    locations = locations.with_columns(
        pl.lit("").alias(field) for field in OUTPUT_COLUMNS if field not in locations.columns
    ).with_columns(pl.col("brand").fill_null("").replace("", "Unspecified"))
except Exception as error:
    st.error(f"Could not read pipeline output: {error}")
    st.stop()

if locations.is_empty():
    st.warning("The current pipeline output contains no locations.")
    st.stop()

locations = locations.with_columns(
    pl.when(pl.col("region").fill_null("") != "")
    .then(pl.concat_str([pl.col("city"), pl.col("region")], separator=" · "))
    .otherwise(pl.col("city")).alias("_place")
)
cities = sorted(locations.get_column("_place").drop_nulls().unique().to_list())
categories = sorted(locations.get_column("category").drop_nulls().unique().to_list())
brands = sorted(locations.get_column("brand").unique().to_list())
known_brands = any(brand != "Unspecified" for brand in brands)
regions = sorted(value for value in locations.get_column("region").drop_nulls().unique().to_list() if value)
selected_cities = st.sidebar.multiselect("Cities / municipalities", cities, placeholder="All places")
selected_categories = st.sidebar.multiselect("Categories", categories, default=categories)
selected_brands = st.sidebar.multiselect("Brands", brands, default=brands)
selected_regions = st.sidebar.multiselect("Provinces / areas", regions, placeholder="All provinces and areas")
search = st.sidebar.text_input("Find a branch", placeholder="Name or address")
filtered = locations.filter(
    pl.col("_place").is_in(selected_cities or cities)
    & pl.col("category").is_in(selected_categories)
    & pl.col("brand").is_in(selected_brands)
    & pl.col("region").fill_null("").is_in(selected_regions or [*regions, ""])
)
if search.strip():
    filtered = filtered.filter(
        pl.concat_str(["business_name", "address", "city"], separator=" ", ignore_nulls=True)
        .str.to_lowercase().str.contains(search.strip().lower(), literal=True)
    )

if filtered.is_empty():
    st.info("No locations match the selected filters.")
    st.stop()

metric_columns = st.columns(3)
metric_columns[0].metric("Locations", f"{len(filtered):,}")
metric_columns[1].metric("Cities / municipalities", str(filtered.get_column("_place").n_unique()))
average_rating = filtered.get_column("rating").mean()
if average_rating is None and known_brands:
    metric_columns[2].metric("Brands", str(filtered.get_column("brand").n_unique()))
else:
    metric_columns[2].metric(
        "Average rating", f"{average_rating:.2f}" if average_rating is not None else "N/A"
    )

ph_brands = sorted(set(filtered.filter(
    pl.col("country").fill_null("").str.to_uppercase().is_in(["PH", "PHILIPPINES"])
)["brand"].to_list()) & {"Jollibee", "McDonald's", "KFC"})
tabs = st.tabs(["Map", "Market summary", "Locations", *(["Prices & revenue"] if ph_brands else [])])
tab_map, tab_categories, tab_data = tabs[:3]
with tab_map:
    color_by = st.radio("Color locations by", ["Brand", "Rating"], horizontal=True) if known_brands else "Rating"
    st.plotly_chart(location_map(filtered, color_by), width="stretch")
    if len(filtered) > 250 and color_by == "Brand":
        st.caption("Zoom in to see individual branches.")

with tab_categories:
    category_summary = (
        filtered.group_by("category")
        .agg(pl.len().alias("Locations"))
        .sort("Locations", descending=True)
    )
    chart = px.bar(
        category_summary.to_dict(as_series=False),
        x="category",
        y="Locations",
        labels={"category": "Category"},
    )
    st.plotly_chart(chart, width="stretch")
    if known_brands:
        brand_summary = filtered.group_by("brand").agg(pl.len().alias("Locations")).sort("Locations", descending=True)
        st.plotly_chart(px.bar(
            brand_summary.to_dict(as_series=False), x="brand", y="Locations", color="brand",
            color_discrete_map=BRAND_COLORS, labels={"brand": "Brand"},
        ), width="stretch")
        city_summary = filtered.group_by(["city", "region", "brand"]).agg(pl.len().alias("Locations")).sort(["region", "city", "brand"])
        st.dataframe(city_summary, width="stretch", hide_index=True)
        st.caption("Counts describe the collected dataset and may not include every branch.")

with tab_data:
    displayed = filtered.select(OUTPUT_COLUMNS)
    st.dataframe(displayed, width="stretch", hide_index=True, column_config={
        "source_url": st.column_config.LinkColumn("Source / branch details", display_text="Open source"),
        "website": st.column_config.LinkColumn("Website"),
    })
    st.download_button(
        "Download filtered CSV",
        data=displayed.write_csv(),
        file_name="market_locations.csv",
        mime="text/csv",
    )

if ph_brands:
    with tabs[3]:
        render_economics(output_dir, ph_brands)

report_path = output_dir / "run_summary.json"
try:
    metadata = json.loads(report_path.read_text(encoding="utf-8")).get("dataset_metadata", {}) if report_path.is_file() else {}
except (OSError, ValueError):
    metadata = {}
if metadata:
    with st.expander("Data sources and coverage", expanded=True):
        st.write(metadata.get("coverage_note", ""))
        st.markdown("[Jollibee](https://www.jollibee.com.ph/stores/) · [McDonald's](https://www.mcdonalds.com.ph/store) · [KFC](https://www.kfc.com.ph/en/store-locator)")
        st.caption("Source retrieval times (UTC): " + "; ".join(f"{brand}: {date}" for brand, date in metadata.get("retrieved_at_utc", {}).items()))
        st.markdown("Geographic labels: [geoBoundaries](https://www.geoboundaries.org/), from NAMRIA, PSA and OCHA Philippines, [CC BY 3.0 IGO](https://creativecommons.org/licenses/by/3.0/igo/).")
