"""Package the public Philippine data for deployments without local ETL output."""

import argparse
import json
import shutil
from pathlib import Path
from urllib.parse import urlparse

import polars as pl

from src.pipelines.config import PROJECT_ROOT, get_settings, project_path
from src.pipelines.economics import PRICE_SCHEMA
from src.pipelines.transform import REQUIRED_COLUMNS

BUNDLE_DIR = PROJECT_ROOT / "reference_data/philippines_snapshot"
OFFICIAL_DOMAINS = {"www.jollibee.com.ph", "www.mcdonalds.com.ph", "www.kfc.com.ph"}


def export_public_snapshot(output_dir: Path, destination: Path = BUNDLE_DIR) -> dict:
    report = json.loads((output_dir / "run_summary.json").read_text(encoding="utf-8"))
    metadata = report.get("dataset_metadata", {})
    locations = pl.read_parquet(output_dir / "market_locations.parquet")
    prices = pl.read_csv(output_dir / "menu_prices.csv", schema_overrides=PRICE_SCHEMA)
    economics = json.loads((output_dir / "economics_summary.json").read_text(encoding="utf-8"))
    if (
        metadata.get("dataset") != "Philippine fast food locations"
        or not set(REQUIRED_COLUMNS).issubset(locations.columns)
        or locations.is_empty() or prices.is_empty()
        or set(locations["country"].to_list()) != {"PH"}
        or not set(locations["brand"].to_list()).issubset({"Jollibee", "McDonald's", "KFC"})
        or locations.height != report.get("valid_locations")
    ):
        raise ValueError("Only the collected public Philippine fast food dataset can be bundled.")
    urls = [*locations["source_url"].to_list(), *prices["source_url"].to_list()]
    if any(not url or urlparse(url).scheme != "https" or urlparse(url).hostname not in OFFICIAL_DOMAINS for url in urls):
        raise ValueError("Bundled rows must retain their official Philippine source URLs.")
    if output_dir.resolve() == destination.resolve():
        raise ValueError("Snapshot destination must differ from the pipeline output directory.")
    destination.mkdir(parents=True, exist_ok=True)
    for filename in ("market_locations.parquet", "menu_prices.csv"):
        shutil.copy2(output_dir / filename, destination / filename)
    # Do not publish workstation paths from the local run report.
    public_report = {key: report[key] for key in (
        "input_records", "valid_locations", "dropped_invalid_coordinates", "dataset_metadata", "completed_at_utc",
    ) if key in report}
    (destination / "run_summary.json").write_text(json.dumps(public_report, ensure_ascii=False, indent=2), encoding="utf-8")
    (destination / "economics_summary.json").write_text(json.dumps(economics, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"locations": locations.height, "price_references": prices.height, "destination": str(destination)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Bundle the public Philippine snapshot for Streamlit Community Cloud.")
    parser.add_argument("--output-dir", help="Source directory (default: PIPELINE_OUTPUT_DIR).")
    args = parser.parse_args()
    source = project_path(Path(args.output_dir) if args.output_dir else get_settings().output_dir)
    print(json.dumps(export_public_snapshot(source), indent=2))


if __name__ == "__main__":
    main()
