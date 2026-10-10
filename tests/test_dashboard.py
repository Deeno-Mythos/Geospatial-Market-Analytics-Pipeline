import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import plotly.io as pio
import polars as pl
from streamlit.testing.v1 import AppTest

from helpers import local_settings
from app.maps import map_view
from src.pipelines.config import PROJECT_ROOT
from src.pipelines.load import load_local
from src.pipelines.transform import transform_records


class DashboardTests(unittest.TestCase):
    def test_rated_and_unrated_locations_and_filters(self):
        records = [
            {"name": "Rated", "lat": 30, "lng": -97, "rating": 4.5, "city": "Austin", "category": "Cafe"},
            {"name": "Unrated", "lat": 31, "lng": -98, "city": "Dallas", "category": "Grocery"},
        ]
        locations, summary, stats = transform_records(records)
        with tempfile.TemporaryDirectory() as directory:
            load_local(locations, summary, stats, local_settings(Path(directory)))
            with patch.dict(os.environ, {"PIPELINE_OUTPUT_DIR": directory}):
                app = AppTest.from_file(str(PROJECT_ROOT / "app/dashboard.py")).run(timeout=30)
                self.assertEqual(len(app.exception), 0)
                self.assertEqual(app.metric[0].value, "2")
                figure = pio.from_json(app.get("plotly_chart")[0].proto.spec)
                self.assertEqual(sum(len(trace.lat) for trace in figure.data), 2)
                self.assertEqual(figure.data[1].name, "No rating")
                self.assertEqual(figure.data[0].type, "scattermap")
                app.sidebar.multiselect[0].set_value(["Dallas"]).run()
                self.assertEqual(len(app.exception), 0)
                self.assertEqual(app.metric[0].value, "1")
                self.assertEqual(app.metric[2].value, "N/A")
                app.sidebar.multiselect[1].set_value([]).run()
                self.assertEqual(len(app.exception), 0)
                self.assertEqual(app.info[0].value, "No locations match the selected filters.")

    def test_csv_fallback_handles_null_ratings_and_postal_codes(self):
        locations, summary, stats = transform_records([
            {"name": "Unrated", "lat": 30, "lng": -97, "postal_code": "00123"},
        ])
        with tempfile.TemporaryDirectory() as directory:
            result = load_local(locations, summary, stats, local_settings(Path(directory)))
            result["parquet_path"].unlink()
            with patch.dict(os.environ, {"PIPELINE_OUTPUT_DIR": directory}):
                app = AppTest.from_file(str(PROJECT_ROOT / "app/dashboard.py")).run(timeout=30)
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(app.metric[2].value, "N/A")
            self.assertEqual(app.dataframe[0].value.loc[0, "postal_code"], "00123")

    def test_missing_outputs_show_setup_message(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"PIPELINE_OUTPUT_DIR": directory}):
                app = AppTest.from_file(str(PROJECT_ROOT / "app/dashboard.py")).run(timeout=30)
            self.assertEqual(len(app.exception), 0)
            self.assertIn("No pipeline output found", app.error[0].value)

    def test_brand_and_province_filters_keep_same_named_places_distinct(self):
        records = [
            {"name": "Jollibee A", "brand": "Jollibee", "city": "San Fernando", "region": "Pampanga", "lat": 15, "lng": 121},
            {"name": "KFC B", "brand": "KFC", "city": "San Fernando", "region": "La Union", "lat": 16, "lng": 120},
            {"name": "McDonald's C", "brand": "McDonald's", "city": "San Fernando", "region": "Pampanga", "lat": 15.1, "lng": 121.1},
        ]
        locations, summary, stats = transform_records(records)
        with tempfile.TemporaryDirectory() as directory:
            load_local(locations, summary, stats, local_settings(Path(directory)))
            with patch.dict(os.environ, {"PIPELINE_OUTPUT_DIR": directory}):
                app = AppTest.from_file(str(PROJECT_ROOT / "app/dashboard.py")).run(timeout=30)
                self.assertEqual(len(app.exception), 0)
                self.assertEqual(app.metric[1].value, "2")
                self.assertEqual(app.metric[2].value, "3")
                app.sidebar.multiselect[3].set_value(["Pampanga"]).run()
                self.assertEqual(app.metric[0].value, "2")
                app.sidebar.multiselect[2].set_value(["Jollibee"]).run()
                self.assertEqual(app.metric[0].value, "1")
                figure = pio.from_json(app.get("plotly_chart")[0].proto.spec)
                self.assertEqual(figure.data[0].name, "Jollibee")
                app.sidebar.text_input[0].set_value("does not exist").run()
                self.assertEqual(len(app.exception), 0)
                self.assertIn("No locations match", app.info[0].value)


class MapViewTests(unittest.TestCase):
    def test_date_line_locations_center_on_the_pacific(self):
        view = map_view(pl.DataFrame({"latitude": [10, 10], "longitude": [179, -179]}))
        self.assertAlmostEqual(abs(view["center"]["lon"]), 180)
        self.assertGreater(view["zoom"], 5)

    def test_single_location_gets_finite_close_view(self):
        view = map_view(pl.DataFrame({"latitude": [90], "longitude": [0]}))
        self.assertLess(view["center"]["lat"], 90)
        self.assertEqual(view["zoom"], 14)

    def test_broader_market_zooms_out(self):
        local = map_view(pl.DataFrame({"latitude": [30.25, 30.27], "longitude": [-97.75, -97.74]}))
        national = map_view(pl.DataFrame({"latitude": [30, 40], "longitude": [-97, -73]}))
        self.assertGreater(local["zoom"], national["zoom"])
