# Geospatial Market Analytics Pipeline

A small, runnable pipeline for turning business-location data into clean, mappable market summaries. It uses Polars for local ETL, Prefect for an optional tracked run, Streamlit for the dashboard, and optional Apify, MotherDuck, and Cloudflare R2 integrations.

You can run the included sample end to end without an account, API key, or cloud service.

## Philippine fast food data

Collect Jollibee, McDonald's (McDo), and KFC locations across the Philippines from their official public store locators:

```powershell
python -m flows.orchestrator --source ph-fastfood
streamlit run app/dashboard.py
```

The first run downloads the source pages and map data. Later runs reuse those snapshots under `data/raw/philippines_fastfood/`, so they work offline. To fetch new snapshots, run:

```powershell
python -m flows.orchestrator --source ph-fastfood --refresh
```

Set `PIPELINE_SOURCE=ph-fastfood` in `.env` to keep this dataset as the default. An explicit `--input` or `--source file` still selects a local file. `PIPELINE_SOURCE=auto` uses the sample unless an Apify actor is configured.

Each branch retains its brand, official source URL and record ID, retrieval time, coordinates, available address, phone number and operating hours. The dashboard filters by brand, city/municipality and province/area, searches branch names and addresses, colors the map by brand, and compares location counts across places.

The locators do not supply ratings or review counts, so those fields remain blank. Jollibee's map feed supplies branch names and coordinates; street addresses and operating hours are not supplied by that feed. Official locator coverage may be incomplete, and a listed branch is not a confirmation that it is currently operating.

City and province labels are assigned by point-in-polygon lookup using [geoBoundaries' 2020 Philippine municipality and province boundaries](https://www.geoboundaries.org/), sourced from NAMRIA, PSA and OCHA Philippines under [CC BY 3.0 IGO](https://creativecommons.org/licenses/by/3.0/igo/). Manila districts are grouped as Manila City; NCR districts are grouped as Metro Manila. These are reference geography labels, which may differ from current administrative divisions. KFC's embedded city labels are ignored because some point to unrelated overseas cities. Records with invalid coordinates or coordinates outside the Philippine extent are saved for review in `excluded_locations.json` and excluded from the map. Source snapshots and `source_manifest.json` retain provenance and collection counts.

Sources: [Jollibee store directory](https://www.jollibee.com.ph/stores/), [McDonald's store locator](https://www.mcdonalds.com.ph/store), [KFC store locator](https://www.kfc.com.ph/en/store-locator).

## Menu prices and published financials

Add public Philippine menu price references to the dashboard:

```powershell
python -m src.pipelines.economics
```

The **Prices & revenue** tab appears for Philippine Jollibee, McDonald's and KFC locations. Prices follow the selected brands and can be searched by item and filtered by ordering channel. Each row retains its official source, observation time, price basis, scope and conditions. Prices are public reference snapshots; they are not verified quotes for the filtered branches.

Jollibee product pages supply advertised **starting prices**. The collector visits products linked from the first page of each menu category, so coverage may be incomplete. KFC's public menu supplies separate **pickup and delivery base prices**, converted from centavos to pesos; add-ons and delivery fees are excluded. McDonald's prices cover the official **McSavers Snack & Match** promotion, including its participating-store restriction, ordering channels and advertised dates. The combo price covers one main plus a side; it is not the price of the side alone. Expired and upcoming promotions are hidden by default and can be shown explicitly.

Source pages are cached under `data/raw/economics/`. Repeated runs use those snapshots without network calls. Refresh menu pages with `python -m src.pipelines.economics --refresh`; `--output-dir` overrides the configured destination. This is a separate collection command; rerunning the location pipeline preserves these files.

Financial disclosures are dated, manually verified records in `reference_data/philippines_financials.json`. Menu refresh does not refresh financial disclosures. The tab displays **Jollibee Group FY2025 consolidated revenue of ₱305.1 billion** with worldwide, all-brand scope ([official ASM release](https://www.jollibeegroup.com/news/jollibee-group-reports-record-2025-performance-outlines-disciplined-growth-priorities-at-2026-annual-stockholders-meeting/)), and **GADC / McDonald's Philippines FY2024 sales revenue of ₱47.9 billion** ([AGI presentation, page 37](https://allianceglobalinc.com/sites/default/files/2025-05/AGI%20FY2024%20Presentation%20050225.pdf#page=37)). Systemwide sales and profit are shown as separate measures. Periods and scopes differ, so the tab does not rank these amounts as comparable Philippine brand revenues.

Branch revenue, Philippine Jollibee brand revenue and KFC Philippines revenue have not been verified in the public sources reviewed. Unknown amounts remain unassigned. AGI's 2025 GADC segment contribution covers only part of the year following [deconsolidation in March 2025](https://allianceglobalinc.com/news/agi-1q-core-net-income-grows-14-p75b); it is not used as full-year McDonald's Philippines revenue. Company totals are never divided by the collected branch counts to create branch estimates.

## Quick start

Use Python 3.11 or newer. From the project root:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m flows.orchestrator
streamlit run app/dashboard.py
```

The pipeline reads `examples/sample_competitors.csv` by default and writes cleaned data and summaries under `data/processed/`. The dashboard reads those files. To process another local CSV or JSON file:

```powershell
python -m flows.orchestrator --input C:\path\to\locations.csv
```

Add `--tracked` to either pipeline command to track the run with Prefect. The default command runs directly without starting a Prefect server.

Input rows need valid latitude and longitude. Missing location names become `Unknown location`. Common source names such as `title`, `name`, `lat`, `lng`, `reviewsCount`, and `postalCode` are recognized, along with Apify's nested `location` coordinates, `categoryName`, and `totalScore`. Rows without valid coordinates are excluded. The normalized output includes business name, category, address, city, region, postal code, country, coordinates, rating, review count, website, and source URL. Invalid or missing ratings remain blank, and the map shows those locations in gray.

## Manual configuration

1. Copy `.env.example` to `.env` in the project root. Keep `.env` private; it is ignored by Git.
2. Set only the options you need. Local CSV/JSON processing and the dashboard require no secrets.
3. Run the pipeline again after changing source or destination settings.

| Variable | Purpose |
| --- | --- |
| `PIPELINE_INPUT` | Local CSV, JSON, or JSONL input. Leave empty to use the included sample. |
| `PIPELINE_SOURCE` | Default source: `auto`, `file`, `apify`, or `ph-fastfood`. Explicit CLI source selection takes precedence. |
| `PIPELINE_OUTPUT_DIR` | Local output directory; defaults to `data/processed`. |
| `APIFY_TOKEN` | Apify API token, needed only for remote extraction. |
| `APIFY_ACTOR_ID` | Actor ID (for example, `vendor/actor` or `vendor~actor`). When set, automatic source selection uses Apify unless `--input` is supplied. |
| `APIFY_INPUT_JSON` | Optional JSON object passed to the actor, such as `{"searchStringsArray":["coffee shops in Austin"]}`. |
| `MOTHERDUCK_TOKEN` | MotherDuck token, needed only when configuring a database. |
| `MOTHERDUCK_DATABASE` | MotherDuck database name. Writes the normalized data to the `market_locations` table. |
| `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET` | Cloudflare R2 upload settings. When all are set, the normalized Parquet file is uploaded. |
| `R2_ENDPOINT_URL` | Optional custom S3-compatible endpoint; defaults to the account's Cloudflare R2 endpoint. |

To run Apify explicitly, configure `APIFY_TOKEN` and `APIFY_ACTOR_ID`, then run `python -m flows.orchestrator --source apify`. To keep data local, leave the cloud variables blank. Missing or incomplete cloud settings produce a clear error rather than silently skipping the requested upload.

Apify extraction waits up to 30 minutes for the actor to succeed and reads all dataset pages. Failed or unfinished runs raise an error before publishing local outputs. A wait timeout leaves the remote actor running; check it in Apify Console before retrying. Authentication and polling follow the [Apify API reference](https://docs.apify.com/api/v2).

## Outputs

- `data/processed/market_locations.parquet` — normalized location data.
- `data/processed/market_locations.csv` — spreadsheet-friendly copy.
- `data/processed/market_summary.csv` — location counts and average ratings by city, region, category, and brand.
- `data/processed/brand_summary.csv` — location counts by brand.
- `data/processed/run_summary.json` — counts and output paths for the run.
- `data/processed/menu_prices.csv` — optional public price references with conditions and source dates.
- `data/processed/financial_benchmarks.csv` — optional export of scoped, dated company disclosures.
- `data/processed/economics_summary.json` — optional price coverage and financial disclosure notes.

## Dashboard and Docker

The dashboard can be started after a pipeline run with `streamlit run app/dashboard.py`. It supports place, province/area, brand and category filters, branch search, a street map that frames the selected locations, summary metrics, comparisons by brand and place, and a downloadable filtered CSV. The OpenStreetMap basemap requires internet access; cached data processing and data tables work locally.

For a containerized dashboard, configure `.env` if needed, then generate the local outputs and start the dashboard:

```powershell
docker compose run --rm dashboard python -m flows.orchestrator
docker compose up --build
```

Open `http://localhost:8501`. The local `data` directory is mounted into the container so pipeline outputs are visible to the dashboard.

The Docker build excludes `.env`, local data, and Git history. Compose supplies the configured cloud credentials at runtime.

## Development

Run the focused test suite with:

```powershell
python -m unittest discover -s tests -v
```
