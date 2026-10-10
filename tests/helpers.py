from dataclasses import replace
from pathlib import Path

from src.pipelines.config import Settings


def local_settings(output_dir: Path, **overrides) -> Settings:
    return replace(
        Settings(
            input_file=None,
            output_dir=output_dir,
            apify_token="",
            apify_actor_id="",
            apify_input_json="{}",
            motherduck_token="",
            motherduck_database="",
            r2_account_id="",
            r2_access_key_id="",
            r2_secret_access_key="",
            r2_bucket="",
            r2_endpoint_url="",
        ),
        **overrides,
    )
