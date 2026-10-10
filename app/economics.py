"""Dashboard views for sourced menu prices and company financial disclosures."""

import json
from pathlib import Path

import polars as pl
import streamlit as st

from src.pipelines.economics import PRICE_SCHEMA, load_financial_reference, price_status


def render_economics(output_dir: Path, brands: list[str]) -> None:
    st.caption("Prices are Philippine public references. Place filters select brands with matching branches; company totals and menu prices are not measurements for those branches.")
    st.subheader("Menu prices")
    path = output_dir / "menu_prices.csv"
    if path.is_file():
        try:
            prices = pl.read_csv(path, schema_overrides=PRICE_SCHEMA).filter(pl.col("brand").is_in(brands))
            prices = prices.with_columns(pl.Series("status", [price_status(row) for row in prices.to_dicts()]))
            channels = sorted(prices["order_channel"].unique().to_list())
            channel = st.selectbox("Order channel", ["All channels", *channels], key="price_channel")
            search = st.text_input("Find a menu item", placeholder="Chicken, burger, fries…", key="price_search")
            include_inactive = st.checkbox("Include expired or upcoming promos", value=False, key="price_include_inactive")
            if not include_inactive:
                prices = prices.filter(~pl.col("status").is_in(["Expired promo", "Upcoming promo"]))
            if channel != "All channels":
                prices = prices.filter(pl.col("order_channel") == channel)
            if search.strip():
                prices = prices.filter(pl.col("item_name").str.to_lowercase().str.contains(search.strip().lower(), literal=True))
            if prices.is_empty():
                st.info("No published price references match these filters.")
            else:
                st.caption(f"{prices.height:,} price references. Starting prices, base prices and promotions have different conditions; upgrades and fees may cost extra.")
                st.dataframe(prices.select(
                    "brand", "item_name", "price_php", "price_basis", "order_channel", "status",
                    "valid_from", "valid_to", "scope", "observed_at_utc", "source_url", "notes",
                ), width="stretch", hide_index=True, column_config={
                    "brand": "Brand", "item_name": "Item", "price_php": st.column_config.NumberColumn("Price (PHP)", format="₱%.2f"),
                    "price_basis": "Price basis", "order_channel": "Channel", "status": "Availability",
                    "valid_from": "Promo starts", "valid_to": "Promo ends", "scope": "Scope",
                    "observed_at_utc": "Observed (UTC)", "notes": "Conditions",
                    "source_url": st.column_config.LinkColumn("Official source", display_text="View source"),
                })
                st.download_button("Download price references CSV", prices.write_csv(), "menu_prices.csv", "text/csv")
        except (OSError, ValueError, pl.exceptions.PolarsError) as error:
            st.warning(f"Could not read price references: {error}")
    else:
        st.info("Collect menu prices with `python -m src.pipelines.economics`.")
    report_path = output_dir / "economics_summary.json"
    if report_path.is_file():
        try:
            metadata = json.loads(report_path.read_text(encoding="utf-8"))
            st.caption(metadata.get("coverage_note", ""))
        except (OSError, ValueError):
            pass

    st.subheader("Published company financials")
    st.caption("Reported figures in Philippine pesos, rounded as in the sources. Revenue, systemwide sales and profit are different measures; scopes and reporting years differ.")
    try:
        financials, reference = load_financial_reference()
        financials = financials.filter(pl.col("brand").is_in(brands))
        for brand in brands:
            revenues = financials.filter(
                (pl.col("brand") == brand) & pl.col("metric").str.to_lowercase().str.contains("revenue")
            )
            if not revenues.is_empty():
                row = revenues.row(0, named=True)
                label = "Jollibee Group" if brand == "Jollibee" else "McDonald's PH"
                st.metric(f"{label} · {row['period']} revenue", f"₱{row['amount_php'] / 1e9:,.1f} billion")
                st.caption(f"{row['entity']} · {row['scope']}")
            if brand in reference["gaps"]:
                st.write(reference["gaps"][brand])
        if not financials.is_empty():
            st.dataframe(financials.with_columns(
                (pl.col("amount_php") / 1e9).alias("amount_php_billions")
            ).select("entity", "metric", "amount_php_billions", "period", "scope", "source_url", "notes"),
                width="stretch", hide_index=True, column_config={
                    "entity": "Reporting entity", "metric": "Measure", "period": "Period", "scope": "Scope",
                    "amount_php_billions": st.column_config.NumberColumn("PHP billions", format="₱%.1f bn"),
                    "source_url": st.column_config.LinkColumn("Published source", display_text="View disclosure"), "notes": "Notes",
                })
            st.download_button("Download financial references CSV", financials.write_csv(), "financial_benchmarks.csv", "text/csv")
        st.caption(f"Disclosures checked on {reference['checked_on']}. {reference['coverage_note']}")
    except (OSError, ValueError, pl.exceptions.PolarsError) as error:
        st.warning(f"Could not read financial references: {error}")
