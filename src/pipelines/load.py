import json
from datetime import datetime, timezone

import boto3
import duckdb
import polars as pl

from src.pipelines.config import Settings, project_path


def load_local(locations, summary, stats: dict, settings: Settings) -> dict:
    output_dir = project_path(settings.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    parquet_path = output_dir / "market_locations.parquet"
    csv_path = output_dir / "market_locations.csv"
    summary_path = output_dir / "market_summary.csv"
    brand_summary_path = output_dir / "brand_summary.csv"
    report_path = output_dir / "run_summary.json"

    locations.write_parquet(parquet_path)
    locations.write_csv(csv_path)
    summary.write_csv(summary_path)
    summary.group_by("brand").agg(
        pl.col("location_count").sum(),
    ).sort("location_count", descending=True).write_csv(brand_summary_path)
    report = {
        **stats,
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "outputs": {
            "parquet": str(parquet_path),
            "csv": str(csv_path),
            "summary_csv": str(summary_path),
            "brand_summary_csv": str(brand_summary_path),
        },
    }
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return {"report": report, "parquet_path": parquet_path}


def load_motherduck(parquet_path, settings: Settings) -> None:
    if not settings.motherduck_database:
        return
    if not settings.motherduck_token:
        raise ValueError("MOTHERDUCK_TOKEN is required when MOTHERDUCK_DATABASE is set.")
    connection = duckdb.connect(
        f"md:{settings.motherduck_database}",
        config={"motherduck_token": settings.motherduck_token},
    )
    try:
        connection.execute(
            "CREATE OR REPLACE TABLE market_locations AS SELECT * FROM read_parquet(?)",
            [str(parquet_path)],
        )
    finally:
        connection.close()


def load_r2(parquet_path, settings: Settings) -> None:
    configured = (
        settings.r2_bucket,
        settings.r2_account_id,
        settings.r2_access_key_id,
        settings.r2_secret_access_key,
    )
    if not any(configured):
        return
    if not all(configured):
        raise ValueError(
            "R2 upload requires R2_BUCKET, R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, "
            "and R2_SECRET_ACCESS_KEY."
        )
    client = boto3.client(
        "s3",
        endpoint_url=settings.r2_endpoint_url,
        aws_access_key_id=settings.r2_access_key_id,
        aws_secret_access_key=settings.r2_secret_access_key,
        region_name="auto",
    )
    client.upload_file(str(parquet_path), settings.r2_bucket, "market_locations.parquet")
