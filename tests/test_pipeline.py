import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import patch

import polars as pl

from flows.orchestrator import run_pipeline
from helpers import local_settings
from src.pipelines.config import PROJECT_ROOT
from src.pipelines.extract import extract_file
from src.pipelines.load import load_local
from src.pipelines.transform import OUTPUT_COLUMNS, transform_records


class TransformTests(unittest.TestCase):
    def test_normalizes_aliases_and_summarizes_markets(self):
        records = [
            {
                "title": "Cafe One",
                "categories": ["Coffee", "Cafe"],
                "lat": "30.2",
                "lng": "-97.7",
                "rating": "4.5",
                "reviewsCount": "12",
                "city": "Austin",
            },
            {"name": "Cafe Two", "latitude": 30.3, "longitude": -97.8, "city": "Austin"},
            {"name": "No coordinates"},
            {"name": "Invalid", "lat": 91, "lng": 0},
        ]

        locations, summary, stats = transform_records(records)

        self.assertEqual(locations.columns, list(OUTPUT_COLUMNS))
        self.assertEqual(locations.height, 2)
        self.assertEqual(locations[0, "category"], "Coffee, Cafe")
        self.assertEqual(locations[0, "review_count"], 12)
        self.assertEqual(summary.height, 2)
        self.assertEqual(stats["dropped_invalid_coordinates"], 2)

    def test_rejects_input_with_no_valid_coordinates(self):
        with self.assertRaisesRegex(ValueError, "valid latitude and longitude"):
            transform_records([{"name": "Nowhere", "lat": "bad", "lng": 0}])

    def test_normalizes_apify_locations_and_prefers_canonical_fields(self):
        records = [{
            "name": "Alias name", "business_name": "Canonical name",
            "location": {"lat": 30.2, "lng": -97.7},
            "categoryName": "Cafe", "totalScore": 4.6, "reviewsCount": "1,200",
            "postalCode": "00123",
        }]
        locations, _, _ = transform_records(records)
        self.assertEqual(locations[0, "business_name"], "Canonical name")
        self.assertEqual(locations[0, "latitude"], 30.2)
        self.assertEqual(locations[0, "category"], "Cafe")
        self.assertEqual(locations[0, "rating"], 4.6)
        self.assertEqual(locations[0, "review_count"], 1200)
        self.assertEqual(locations[0, "postal_code"], "00123")

    def test_keeps_locations_with_invalid_optional_numbers(self):
        locations, summary, _ = transform_records([
            {"lat": 30, "lng": -97, "rating": "NaN", "reviewsCount": 1e100},
            {"lat": 31, "lng": -98, "rating": 6, "reviewsCount": 1.5},
            {"lat": 32, "lng": -99, "rating": True, "reviewsCount": -1},
        ])
        self.assertEqual(locations.height, 3)
        self.assertEqual(locations["rating"].to_list(), [None, None, None])
        self.assertEqual(locations["review_count"].to_list(), [None, None, None])
        self.assertIsNone(summary[0, "average_rating"])

    def test_rejects_non_object_records(self):
        with self.assertRaisesRegex(ValueError, "record 1 must be an object"):
            transform_records([123])


class PipelineFileTests(unittest.TestCase):
    def test_reads_json_wrappers(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.json"
            path.write_text('{"items":[{"name":"Cafe","lat":30,"lng":-97}]}', encoding="utf-8")
            self.assertEqual(extract_file(path)[0]["name"], "Cafe")

    def test_reads_jsonl_and_reports_bad_line(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.jsonl"
            path.write_text('\ufeff{"name":"Cafe","lat":30,"lng":-97}\n\n', encoding="utf-8")
            self.assertEqual(len(extract_file(path)), 1)
            for invalid, message in [('123', 'line 2 must contain an object'), ('{', 'line 2')]:
                with self.subTest(invalid=invalid):
                    path.write_text('{"name":"Cafe"}\n' + invalid, encoding="utf-8")
                    with self.assertRaisesRegex(ValueError, message):
                        extract_file(path)

    def test_writes_local_outputs_and_summary(self):
        records = [{"name": "Cafe", "city": "Austin", "lat": 30, "lng": -97}]
        locations, summary, stats = transform_records(records)
        with tempfile.TemporaryDirectory() as directory:
            settings = local_settings(Path(directory))
            result = load_local(locations, summary, stats, settings)
            self.assertTrue(result["parquet_path"].is_file())
            self.assertTrue((Path(directory) / "market_locations.csv").is_file())
            self.assertTrue((Path(directory) / "market_summary.csv").is_file())
            self.assertTrue((Path(directory) / "run_summary.json").is_file())
            self.assertEqual(pl.read_parquet(result["parquet_path"]).height, 1)


class OrchestratorTests(unittest.TestCase):
    def test_runs_sample_end_to_end(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("flows.orchestrator.get_settings", return_value=local_settings(Path(directory))):
                report = run_pipeline()
            locations = pl.read_parquet(report["outputs"]["parquet"])
            self.assertEqual(report["valid_locations"], 5)
            self.assertEqual(locations["city"].unique().to_list(), ["Austin"])
            saved = json.loads((Path(directory) / "run_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(saved, report)

    def test_explicit_file_overrides_configured_actor(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = local_settings(Path(directory), apify_actor_id="vendor/actor")
            with patch("flows.orchestrator.get_settings", return_value=settings), patch(
                "flows.orchestrator.extract_apify"
            ) as apify:
                report = run_pipeline(str(PROJECT_ROOT / "examples/sample_competitors.csv"))
            self.assertEqual(report["valid_locations"], 5)
            apify.assert_not_called()

    def test_rejects_conflicting_or_unknown_sources(self):
        for source, input_path in [("typo", None), ("apify", "local.csv")]:
            with self.subTest(source=source), self.assertRaises(ValueError):
                run_pipeline(input_path=input_path, source=source)


if __name__ == "__main__":
    unittest.main()
