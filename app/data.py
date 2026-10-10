"""Choose local pipeline output or the public snapshot shipped with the app."""

from pathlib import Path

from src.pipelines.config import PROJECT_ROOT

BUNDLED_DATA = PROJECT_ROOT / "reference_data/philippines_snapshot"
DEFAULT_OUTPUT = PROJECT_ROOT / "data/processed"


def dashboard_data_dir(output_dir: Path) -> Path:
    if any((output_dir / name).is_file() for name in ("market_locations.parquet", "market_locations.csv")):
        return output_dir
    # Respect custom output directories: missing or invalid custom data should
    # not silently display a different dataset.
    if output_dir.resolve() == DEFAULT_OUTPUT.resolve() and (BUNDLED_DATA / "market_locations.parquet").is_file():
        return BUNDLED_DATA
    return output_dir
