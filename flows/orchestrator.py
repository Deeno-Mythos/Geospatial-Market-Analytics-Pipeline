import argparse
import json
from dataclasses import replace
from pathlib import Path

from src.pipelines.config import get_settings, project_path
from src.pipelines.extract import extract_apify, extract_file
from src.pipelines.load import load_local, load_motherduck, load_r2
from src.pipelines.transform import transform_records


def run_pipeline(
    input_path: str | None = None,
    source: str = "auto",
    output_dir: str | None = None,
    refresh: bool = False,
) -> dict:
    if source not in {"auto", "file", "apify", "ph-fastfood"}:
        raise ValueError("Source must be auto, file, apify, or ph-fastfood.")
    if source in {"apify", "ph-fastfood"} and input_path:
        raise ValueError("A local input file cannot be combined with a remote source.")
    if refresh and source != "ph-fastfood":
        raise ValueError("Refresh is supported only with source=ph-fastfood.")
    settings = get_settings()
    if output_dir:
        settings = replace(settings, output_dir=Path(output_dir))

    selected_source = source
    if selected_source == "auto":
        if input_path:
            selected_source = "file"
        else:
            selected_source = settings.pipeline_source
        if selected_source == "auto":
            selected_source = "apify" if settings.apify_actor_id else "file"
    if selected_source not in {"file", "apify", "ph-fastfood"}:
        raise ValueError("PIPELINE_SOURCE must be auto, file, apify, or ph-fastfood.")
    dataset_metadata = None
    if selected_source == "ph-fastfood":
        from src.pipelines.philippines import collect_philippines_fastfood

        records, dataset_metadata = collect_philippines_fastfood(refresh=refresh)
    elif selected_source == "apify":
        records = extract_apify(
            settings.apify_token,
            settings.apify_actor_id,
            settings.apify_input_json,
        )
    else:
        source_path = (
            Path(input_path).expanduser()
            if input_path
            else settings.input_file or Path("examples/sample_competitors.csv")
        )
        records = extract_file(project_path(source_path))

    locations, summary, stats = transform_records(records)
    if dataset_metadata:
        stats["dataset_metadata"] = dataset_metadata
    result = load_local(locations, summary, stats, settings)
    load_motherduck(result["parquet_path"], settings)
    load_r2(result["parquet_path"], settings)
    return result["report"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the geospatial market data pipeline.")
    parser.add_argument("--input", help="Local CSV, JSON, or JSONL file.")
    parser.add_argument(
        "--source", choices=("auto", "file", "apify", "ph-fastfood"), default="auto",
        help="Select local files, Apify, or Philippine fast food locators (default: infer from configuration).",
    )
    parser.add_argument("--output-dir", help="Override the local output directory.")
    parser.add_argument("--tracked", action="store_true", help="Track the run with Prefect.")
    parser.add_argument("--refresh", action="store_true", help="Refresh cached Philippine fast food source data.")
    args = parser.parse_args()
    if args.source in {"apify", "ph-fastfood"} and args.input:
        parser.error("--input cannot be combined with a remote source.")
    if args.refresh and args.source != "ph-fastfood":
        parser.error("--refresh requires --source ph-fastfood.")
    runner = run_pipeline
    if args.tracked:
        from prefect import flow

        runner = flow(name="geospatial-market-analytics")(run_pipeline)
    report = runner(args.input, args.source, args.output_dir, args.refresh)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
