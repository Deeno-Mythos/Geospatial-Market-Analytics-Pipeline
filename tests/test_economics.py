import json
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import polars as pl
from streamlit.testing.v1 import AppTest

from helpers import local_settings
from src.pipelines.config import PROJECT_ROOT
from src.pipelines.economics import (
    PRICE_SCHEMA, collect_prices, load_financial_reference,
    parse_jollibee_price, parse_kfc_prices, parse_mcdo_prices, price_status,
)
from src.pipelines.load import load_local
from src.pipelines.transform import transform_records


def jollibee_html():
    return '<h1 class="menu-item-title">Chickenjoy</h1><p class="menu-item-price">Starts at <span class="menu-item-price__amount">₱111.00</span></p>'


def kfc_html():
    payload = {"props": {"pageProps": {"data": {
        "currency": "PHP", "countryCode": "PH",
        "items": {
            "delivery": {"title": {"en_US": "Chicken Meal"}, "price": 14000, "sectionItem": True, "orderTypes": ["delivery"]},
            "pickup": {"title": {"en_US": "Chicken Meal"}, "price": 13000, "sectionItem": True, "orderTypes": ["peakup"]},
            "addon": {"title": {"en_US": "Upgrade"}, "price": 500, "sectionItem": False},
        },
        "sections": [
            {"title": {"en_US": "Deals"}, "itemIds": ["delivery", "addon"], "orderTypes": ["delivery"]},
            {"title": {"en_US": "Favorites"}, "itemIds": ["delivery", "pickup"]},
        ],
    }}}}
    return '<script id="__NEXT_DATA__">' + json.dumps(payload) + '</script>'


def mcdo_html():
    return '''<table><tr><td>Main Offers</td><td>Hours</td><td>Price</td></tr>
        <tr><td>NEW: Burger</td><td>All day</td><td rowspan="2">₱59</td></tr>
        <tr><td>Spaghetti</td><td>Regular hours</td></tr></table>
        <table><tr><td>Optional Side Offers</td></tr><tr><td>Sundae</td><td>₱79 (main + side)</td></tr></table>
        <p>Promo runs from October 1-November 11, 2026.</p>'''


class EconomicsTests(unittest.TestCase):
    def test_jollibee_preserves_starting_price_and_scope(self):
        record = parse_jollibee_price(jollibee_html(), "https://www.jollibee.com.ph/menu/chickenjoy/1-test", "2026-10-11")
        self.assertEqual(record["price_php"], 111)
        self.assertEqual(record["price_basis"], "Starting price")
        self.assertIn("may vary", record["notes"])
        with self.assertRaisesRegex(ValueError, "no published price"):
            parse_jollibee_price("<h1>Changed</h1>", record["source_url"], "2026-10-11")

    def test_kfc_converts_centavos_separates_channels_and_skips_addons(self):
        records = parse_kfc_prices(kfc_html(), "2026-10-11")
        self.assertEqual(len(records), 2)
        self.assertEqual({r["order_channel"]: r["price_php"] for r in records}, {"Delivery": 140, "Pickup": 130})
        self.assertTrue(all("no branch selected" in r["scope"] for r in records))
        with self.assertRaisesRegex(ValueError, "Philippine"):
            parse_kfc_prices(kfc_html().replace('"PHP"', '"USD"'), "2026-10-11")

    def test_mcdo_rowspan_applies_to_mains_and_combo_is_not_side_price(self):
        records = parse_mcdo_prices(mcdo_html(), "2026-10-11")
        self.assertEqual([r["price_php"] for r in records], [59, 59, 79])
        self.assertEqual(records[0]["item_name"], "Burger")
        self.assertIn("one main + one eligible side", records[-1]["item_name"])
        self.assertEqual(records[0]["valid_to"], "2026-11-11")
        self.assertIn("Select participating", records[0]["scope"])
        with self.assertRaisesRegex(ValueError, "promo dates"):
            parse_mcdo_prices(mcdo_html().replace("Promo runs from", "No dates:"), "2026-10-11")

    def test_promo_status_respects_start_end_and_unverified_snapshots(self):
        record = {"valid_from": "2026-10-01", "valid_to": "2026-11-11"}
        self.assertEqual(price_status(record, date(2026, 9, 30)), "Upcoming promo")
        self.assertEqual(price_status(record, date(2026, 11, 11)), "Within advertised dates")
        self.assertEqual(price_status(record, date(2026, 11, 12)), "Expired promo")
        self.assertIn("unverified", price_status({}, date(2026, 11, 12)))

    @patch("src.pipelines.economics.requests.get")
    def test_cached_collection_needs_no_network_and_keeps_retrieval_dates(self, request):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            files = {
                "kfc_menu.html": kfc_html(), "mcdo_snack_match.html": mcdo_html(),
                "jollibee_home.html": '<a href="/menu/chickenjoy">Chickenjoy</a>',
                "jollibee_category_chickenjoy.html": '<a href="/menu/chickenjoy/1-test">View item</a>',
                "jollibee_product_1-test.html": jollibee_html(),
            }
            for filename, contents in files.items():
                (cache / filename).write_text(contents, encoding="utf-8")
            prices, metadata = collect_prices(cache)
            self.assertEqual(prices.height, 6)
            self.assertEqual(metadata["price_rows_by_brand"], {"Jollibee": 1, "KFC": 2, "McDonald's": 3})
            self.assertTrue(all(prices["observed_at_utc"].to_list()))
            request.assert_not_called()

    def test_financial_reference_keeps_reporting_scope_and_gaps(self):
        financials, reference = load_financial_reference()
        revenue = financials.filter(pl.col("metric").str.to_lowercase().str.contains("revenue"))
        self.assertEqual(revenue.height, 2)
        jollibee = revenue.filter(pl.col("brand") == "Jollibee").row(0, named=True)
        self.assertEqual(jollibee["amount_php"], 305.1e9)
        self.assertEqual(jollibee["scope"], "Worldwide; all group brands")
        mcdo = revenue.filter(pl.col("brand") == "McDonald's").row(0, named=True)
        self.assertEqual(mcdo["period"], "FY2024")
        self.assertEqual(financials.filter(pl.col("brand") == "KFC").height, 0)
        self.assertIn("no amount is assigned", reference["gaps"]["KFC"])

    def test_dashboard_brand_filter_and_expired_promos(self):
        records = [
            {"name": "Jollibee Test", "brand": "Jollibee", "country": "PH", "lat": 15, "lng": 121},
            {"name": "McDo Test", "brand": "McDonald's", "country": "PH", "lat": 15, "lng": 121},
        ]
        locations, summary, stats = transform_records(records)
        prices = [
            parse_jollibee_price(jollibee_html(), "https://www.jollibee.com.ph/menu/chickenjoy/1-test", "2026-10-11"),
            *parse_mcdo_prices(mcdo_html(), "2026-10-11"),
        ]
        for row in prices[1:]:
            row["valid_from"], row["valid_to"] = "2000-01-01", "2000-01-02"
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            load_local(locations, summary, stats, local_settings(output_dir))
            pl.DataFrame(prices, schema=PRICE_SCHEMA).write_csv(output_dir / "menu_prices.csv")
            with patch.dict(os.environ, {"PIPELINE_OUTPUT_DIR": directory}):
                app = AppTest.from_file(str(PROJECT_ROOT / "app/dashboard.py")).run(timeout=30)
                self.assertEqual(len(app.exception), 0)
                self.assertIn("Prices & revenue", [tab.label for tab in app.tabs])
                prices_table = app.dataframe[2].value
                self.assertEqual(len(prices_table), 1)
                self.assertEqual(prices_table.iloc[0]["price_basis"], "Starting price")
                app.checkbox(key="price_include_inactive").check().run()
                self.assertEqual(len(app.dataframe[2].value), 4)
                app.sidebar.multiselect[2].set_value(["McDonald's"]).run()
                self.assertEqual(len(app.exception), 0)
                self.assertEqual(set(app.dataframe[2].value["brand"]), {"McDonald's"})
                self.assertEqual(app.metric[3].value, "₱47.9 billion")
