import csv
import json
import time
from pathlib import Path
from urllib.parse import quote

import requests


def _records_from_json(value: object) -> list[dict]:
    if isinstance(value, list):
        records = value
    elif isinstance(value, dict):
        records = next(
            (value[key] for key in ("items", "results", "data") if isinstance(value.get(key), list)),
            [value],
        )
    else:
        raise ValueError("JSON input must contain an object or a list of objects.")

    if not all(isinstance(record, dict) for record in records):
        raise ValueError("Every JSON input record must be an object.")
    return records


def extract_file(path: Path) -> list[dict]:
    if not path.is_file():
        raise FileNotFoundError(f"Input file not found: {path}")

    suffix = path.suffix.lower()
    if suffix == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as file:
            return list(csv.DictReader(file))
    if suffix in {".json", ".jsonl", ".ndjson"}:
        with path.open(encoding="utf-8-sig") as file:
            if suffix in {".jsonl", ".ndjson"}:
                records = []
                for line_number, line in enumerate(file, start=1):
                    if not line.strip():
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError as error:
                        raise ValueError(f"Invalid JSON on line {line_number} of {path.name}.") from error
                    if not isinstance(record, dict):
                        raise ValueError(f"JSONL line {line_number} must contain an object.")
                    records.append(record)
                return records
            return _records_from_json(json.load(file))
    raise ValueError(f"Unsupported input format '{suffix}'. Use CSV, JSON, or JSONL.")


def extract_apify(
    token: str, actor_id: str, actor_input: str, *, wait_timeout_seconds: float = 1800,
) -> list[dict]:
    if not token or not actor_id:
        raise ValueError("APIFY_TOKEN and APIFY_ACTOR_ID are both required for Apify extraction.")
    try:
        parsed_input = json.loads(actor_input or "{}")
    except json.JSONDecodeError as error:
        raise ValueError("APIFY_INPUT_JSON must be valid JSON.") from error
    if not isinstance(parsed_input, dict):
        raise ValueError("APIFY_INPUT_JSON must be a JSON object.")
    if wait_timeout_seconds <= 0:
        raise ValueError("The Apify wait timeout must be positive.")

    actor_path = quote(actor_id.replace("/", "~"), safe="~")
    headers = {"Authorization": f"Bearer {token}"}
    deadline = time.monotonic() + wait_timeout_seconds
    wait_seconds = min(60, wait_timeout_seconds)
    response = requests.post(
        f"https://api.apify.com/v2/actors/{actor_path}/runs",
        headers=headers,
        params={"waitForFinish": wait_seconds},
        json=parsed_input,
        timeout=wait_seconds + 15,
    )
    response.raise_for_status()
    run = response.json().get("data", {})
    run_id = run.get("id")
    pending_statuses = {"READY", "RUNNING", "TIMING-OUT", "ABORTING"}
    while run.get("status") in pending_statuses:
        if not run_id:
            raise RuntimeError("Apify response did not return a run ID.")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(
                f"Timed out waiting for Apify run {run_id}. "
                "The actor may still be running; check its status in Apify Console."
            )
        wait_seconds = min(60, remaining)
        response = requests.get(
            f"https://api.apify.com/v2/actor-runs/{quote(run_id, safe='')}",
            headers=headers,
            params={"waitForFinish": wait_seconds},
            timeout=wait_seconds + 15,
        )
        response.raise_for_status()
        run = response.json().get("data", {})
    if run.get("status") != "SUCCEEDED":
        raise RuntimeError(f"Apify run {run_id or '(unknown)'} ended with status {run.get('status')}.")

    dataset_id = run.get("defaultDatasetId")
    if not dataset_id:
        raise RuntimeError("Apify run completed without returning a default dataset.")

    records = []
    offset = 0
    while True:
        items_response = requests.get(
            f"https://api.apify.com/v2/datasets/{quote(dataset_id, safe='')}/items",
            headers=headers,
            # Preserve empty records so pagination offsets match the dataset.
            params={"format": "json", "skipHidden": "true", "offset": offset, "limit": 1000},
            timeout=120,
        )
        items_response.raise_for_status()
        page = _records_from_json(items_response.json())
        records.extend(page)
        offset += len(page)
        total = items_response.headers.get("X-Apify-Pagination-Total")
        if not page or (total is not None and offset >= int(total)):
            return records
