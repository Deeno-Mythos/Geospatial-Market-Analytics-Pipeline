import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import polars as pl

from app.data import BUNDLED_DATA, DEFAULT_OUTPUT, dashboard_data_dir
from src.pipelines.config import PROJECT_ROOT
from src.pipelines.snapshot import export_public_snapshot


class DeploymentTests(unittest.TestCase):
    def test_default_missing_output_uses_public_bundle_but_custom_output_does_not(self):
        with tempfile.TemporaryDirectory() as directory:
            empty = Path(directory)
            from unittest.mock import patch
            with patch("app.data.DEFAULT_OUTPUT", empty):
                self.assertEqual(dashboard_data_dir(empty), BUNDLED_DATA)
            self.assertEqual(dashboard_data_dir(empty), empty)
            (empty / "market_locations.csv").write_text("local data", encoding="utf-8")
            self.assertEqual(dashboard_data_dir(empty), empty)

    def test_bundle_has_real_public_data_and_no_workstation_output_paths(self):
        report = json.loads((BUNDLED_DATA / "run_summary.json").read_text(encoding="utf-8"))
        self.assertNotIn("outputs", report)
        locations = pl.read_parquet(BUNDLED_DATA / "market_locations.parquet")
        self.assertEqual(locations.height, report["valid_locations"])
        self.assertEqual(locations.height, 2247)
        self.assertEqual(set(locations["country"].to_list()), {"PH"})
        self.assertEqual(set(locations["brand"].to_list()), {"Jollibee", "McDonald's", "KFC"})
        self.assertEqual(pl.read_csv(BUNDLED_DATA / "menu_prices.csv").height, 630)

    def test_export_refuses_rows_without_official_source_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            source, destination = Path(directory) / "source", Path(directory) / "bundle"
            shutil.copytree(BUNDLED_DATA, source)
            locations = pl.read_parquet(source / "market_locations.parquet")
            locations.with_columns(pl.lit("https://example.com/private").alias("source_url")).write_parquet(source / "market_locations.parquet")
            with self.assertRaisesRegex(ValueError, "official Philippine source URLs"):
                export_public_snapshot(source, destination)
            self.assertFalse(destination.exists())

    def test_fresh_checkout_starts_with_nested_entrypoint_in_isolated_python(self):
        # Exclude local data, .env and implicit cwd imports to reproduce Cloud's
        # nested-entrypoint startup rather than testing an already imported app.
        with tempfile.TemporaryDirectory() as directory:
            checkout = Path(directory)
            for folder in ("app", "src", "reference_data"):
                shutil.copytree(PROJECT_ROOT / folder, checkout / folder, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            env = os.environ.copy()
            for name in ("PYTHONPATH", "PIPELINE_OUTPUT_DIR", "PIPELINE_SOURCE", "PIPELINE_INPUT"):
                env.pop(name, None)
            worker = '''
import json, sys
from pathlib import Path
from streamlit.testing.v1 import AppTest
entrypoint = Path(sys.argv[1]).resolve()
app = AppTest.from_file(str(entrypoint)).run(timeout=30)
assert not app.exception, [item.value for item in app.exception]
assert not app.error, [item.value for item in app.error]
assert app.metric[0].value == "2,247"
assert "Prices & revenue" in [tab.label for tab in app.tabs]
assert len(app.dataframe[2].value) >= 618
assert app.metric[3].value == "₱305.1 billion"
assert app.metric[4].value == "₱47.9 billion"
assert sys.modules["src.pipelines.config"].PROJECT_ROOT == entrypoint.parents[1]
app.sidebar.multiselect[2].set_value(["KFC"]).run()
assert not app.exception, [item.value for item in app.exception]
assert app.metric[0].value == "359"
assert len(app.dataframe[2].value) == 298
assert set(app.dataframe[2].value["order_channel"]) == {"Pickup", "Delivery"}
print(json.dumps({"locations": 2247, "kfc_price_rows": 298, "cloud_startup": "ok"}))
'''
            result = subprocess.run(
                [sys.executable, "-I", "-X", "utf8", "-c", worker, str(checkout / "app/dashboard.py")],
                cwd=checkout, env=env, capture_output=True, text=True, encoding="utf-8", timeout=60,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('"cloud_startup": "ok"', result.stdout)
