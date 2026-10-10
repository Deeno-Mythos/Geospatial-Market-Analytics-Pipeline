import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    input_file: Path | None
    output_dir: Path
    apify_token: str
    apify_actor_id: str
    apify_input_json: str
    motherduck_token: str
    motherduck_database: str
    r2_account_id: str
    r2_access_key_id: str
    r2_secret_access_key: str
    r2_bucket: str
    r2_endpoint_url: str
    pipeline_source: str = "auto"


def get_settings() -> Settings:
    load_dotenv(PROJECT_ROOT / ".env")
    input_value = os.getenv("PIPELINE_INPUT", "").strip()
    output_value = os.getenv("PIPELINE_OUTPUT_DIR", "data/processed").strip() or "data/processed"
    account_id = os.getenv("R2_ACCOUNT_ID", "").strip()
    default_endpoint = f"https://{account_id}.r2.cloudflarestorage.com" if account_id else ""

    return Settings(
        input_file=Path(input_value).expanduser() if input_value else None,
        output_dir=Path(output_value).expanduser(),
        apify_token=os.getenv("APIFY_TOKEN", "").strip(),
        apify_actor_id=os.getenv("APIFY_ACTOR_ID", "").strip(),
        apify_input_json=os.getenv("APIFY_INPUT_JSON", "{}").strip() or "{}",
        motherduck_token=os.getenv("MOTHERDUCK_TOKEN", "").strip(),
        motherduck_database=os.getenv("MOTHERDUCK_DATABASE", "").strip(),
        r2_account_id=account_id,
        r2_access_key_id=os.getenv("R2_ACCESS_KEY_ID", "").strip(),
        r2_secret_access_key=os.getenv("R2_SECRET_ACCESS_KEY", "").strip(),
        r2_bucket=os.getenv("R2_BUCKET", "").strip(),
        r2_endpoint_url=os.getenv("R2_ENDPOINT_URL", "").strip() or default_endpoint,
        pipeline_source=os.getenv("PIPELINE_SOURCE", "auto").strip() or "auto",
    )


def project_path(path: Path) -> Path:
    return path if path.is_absolute() else PROJECT_ROOT / path
