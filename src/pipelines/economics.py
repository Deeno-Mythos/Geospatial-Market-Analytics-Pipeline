"""Public Philippine menu prices and explicitly scoped financial references."""

import argparse
import json
import math
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import polars as pl
import requests
from bs4 import BeautifulSoup

from src.pipelines.config import PROJECT_ROOT, get_settings, project_path

DEFAULT_CACHE = PROJECT_ROOT / "data/raw/economics"
FINANCIAL_REFERENCE = PROJECT_ROOT / "reference_data/philippines_financials.json"
JOLLIBEE_HOME = "https://www.jollibee.com.ph/"
KFC_MENU = "https://www.kfc.com.ph/en/menu"
MCDO_PROMO = "https://www.mcdonalds.com.ph/press-center/mcsavers-snack-and-match"
PRICE_SCHEMA = {
    "brand": pl.String, "item_name": pl.String, "menu_category": pl.String,
    "price_php": pl.Float64, "price_basis": pl.String, "order_channel": pl.String,
    "scope": pl.String, "valid_from": pl.String, "valid_to": pl.String,
    "observed_at_utc": pl.String, "source_url": pl.String, "source_id": pl.String,
    "notes": pl.String,
}
FINANCIAL_SCHEMA = {
    "brand": pl.String, "entity": pl.String, "metric": pl.String,
    "amount_php": pl.Float64, "period": pl.String, "scope": pl.String,
    "source_url": pl.String, "source_published_on": pl.String,
    "checked_on": pl.String, "notes": pl.String,
}


def _money(text: str) -> float:
    match = re.search(r"(?:₱|PHP|P)\s*([\d,]+(?:\.\d+)?)", text, re.IGNORECASE)
    if not match:
        raise ValueError(f"No Philippine peso price found: {text!r}")
    amount = float(match[1].replace(",", ""))
    if not math.isfinite(amount) or amount <= 0:
        raise ValueError("Menu prices must be positive finite amounts.")
    return amount


def _price(**values) -> dict:
    return {**{field: "" for field in PRICE_SCHEMA}, **values}


def parse_jollibee_price(html: str, url: str, observed_at: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    title = soup.select_one(".menu-item-title")
    price = soup.select_one(".menu-item-price__amount")
    if title is None or price is None:
        raise ValueError(f"Jollibee product page has no published price: {url}")
    category = urlparse(url).path.split("/")[2].replace("-", " ").title()
    return _price(
        brand="Jollibee", item_name=title.get_text(" ", strip=True), menu_category=category,
        price_php=_money(price.get_text()), price_basis="Starting price",
        order_channel="Not specified on product page", scope="Philippine public product page",
        observed_at_utc=observed_at, source_url=url, source_id=urlparse(url).path,
        notes="Advertised starting price; branch, platform, upgrades and availability may vary.",
    )


def parse_kfc_prices(html: str, observed_at: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    node = soup.find("script", id="__NEXT_DATA__")
    try:
        menu = json.loads(node.get_text())["props"]["pageProps"]["data"]
        if menu["currency"] != "PHP" or menu["countryCode"] != "PH":
            raise ValueError("KFC menu is not a Philippine peso menu.")
        items, sections = menu["items"], menu["sections"]
    except (AttributeError, KeyError, TypeError) as error:
        raise ValueError("KFC public menu data format has changed.") from error
    records, seen = [], set()
    channels = {"delivery": "Delivery", "peakup": "Pickup", "pickup": "Pickup"}
    for section in sections:
        for item_id in section.get("itemIds", []):
            item = items[item_id]
            # Only top-level products linked from public menu sections, not add-ons.
            if item.get("sectionItem") is not True:
                continue
            raw_price = item.get("price")
            if isinstance(raw_price, bool) or not isinstance(raw_price, (int, float)):
                raise ValueError(f"Invalid KFC price for {item_id}.")
            amount = raw_price / 100  # The public payload stores prices in centavos.
            if not math.isfinite(amount) or amount <= 0:
                raise ValueError(f"Invalid KFC price for {item_id}.")
            for order_type in item.get("orderTypes", section.get("orderTypes", [])):
                if order_type not in channels:
                    raise ValueError(f"Unrecognized KFC order channel: {order_type}")
                identity = (item_id, order_type)
                if identity in seen:
                    continue
                seen.add(identity)
                records.append(_price(
                    brand="KFC", item_name=item["title"]["en_US"],
                    menu_category=section["title"]["en_US"], price_php=amount,
                    price_basis="Menu base price", order_channel=channels[order_type],
                    scope="Philippine public web/app menu; no branch selected",
                    observed_at_utc=observed_at, source_url=KFC_MENU,
                    source_id=f"{item_id}:{order_type}",
                    notes="Base price before upgrades or delivery fees; branch availability is unverified.",
                ))
    if not records:
        raise ValueError("KFC returned no priced public menu products.")
    return records


def parse_mcdo_prices(html: str, observed_at: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")
    main_table = next((table for table in tables if "Main Offers" in table.get_text()), None)
    side_table = next((table for table in tables if "Optional Side Offers" in table.get_text()), None)
    period = re.search(
        r"Promo runs from (\w+) (\d{1,2})\s*[-–]\s*(\w+) (\d{1,2}),? (\d{4})",
        soup.get_text(" ", strip=True), re.IGNORECASE,
    )
    if main_table is None or side_table is None or period is None:
        raise ValueError("McDo Snack & Match prices or advertised promo dates have changed.")
    start_month, start_day, end_month, end_day, year = period.groups()
    valid_from = datetime.strptime(f"{start_month} {start_day} {year}", "%B %d %Y").date().isoformat()
    valid_to = datetime.strptime(f"{end_month} {end_day} {year}", "%B %d %Y").date().isoformat()
    main_price, combo_price = _money(main_table.get_text()), _money(side_table.get_text())
    common = dict(
        brand="McDonald's", menu_category="McSavers Snack & Match", price_basis="Advertised promo",
        order_channel="Dine-in / takeout / drive-thru", scope="Select participating Philippine stores",
        valid_from=valid_from, valid_to=valid_to, observed_at_utc=observed_at, source_url=MCDO_PROMO,
    )
    records = []
    for row in main_table.find_all("tr")[1:]:
        cells = row.find_all(["td", "th"])
        if len(cells) < 2:
            continue
        name = re.sub(r"^NEW:\s*", "", cells[0].get_text(" ", strip=True))
        records.append(_price(
            **common, item_name=name, price_php=main_price, source_id=f"snack-match:{name}",
            notes=f"Main snack only. Hours: {cells[1].get_text(' ', strip=True)}. Excludes delivery; see source for channel restrictions.",
        ))
    if not records:
        raise ValueError("McDo promo returned no main snacks.")
    records.append(_price(
        **common, item_name="Snack & Match combo (one main + one eligible side)",
        price_php=combo_price, source_id="snack-match:combo",
        notes="Total combo price, not a side price. Select stores and eligible combinations only; excludes delivery.",
    ))
    return records


def price_status(record: dict, today: date | None = None) -> str:
    today = today or date.today()
    if record.get("valid_from") and today < date.fromisoformat(record["valid_from"]):
        return "Upcoming promo"
    if record.get("valid_to") and today > date.fromisoformat(record["valid_to"]):
        return "Expired promo"
    if record.get("valid_to"):
        return "Within advertised dates"
    return "Snapshot; availability unverified"


def load_financial_reference() -> tuple[pl.DataFrame, dict]:
    reference = json.loads(FINANCIAL_REFERENCE.read_text(encoding="utf-8"))
    records = [{**record, "checked_on": reference["checked_on"]} for record in reference["records"]]
    return pl.DataFrame(records, schema=FINANCIAL_SCHEMA), reference


def collect_prices(cache_dir: Path = DEFAULT_CACHE, *, refresh: bool = False) -> tuple[pl.DataFrame, dict]:
    cache_dir.mkdir(parents=True, exist_ok=True)

    def cached(filename: str, url: str) -> tuple[str, str]:
        path = cache_dir / filename
        if refresh or not path.is_file():
            response = requests.get(url, timeout=(15, 60), headers={"User-Agent": "Geospatial-Market-Analytics-Pipeline/1.0"})
            response.raise_for_status()
            temporary = path.with_suffix(".tmp")
            temporary.write_text(response.text, encoding="utf-8")
            temporary.replace(path)
        observed_at = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
        return path.read_text(encoding="utf-8"), observed_at

    records = []
    kfc_html, observed_at = cached("kfc_menu.html", KFC_MENU)
    records.extend(parse_kfc_prices(kfc_html, observed_at))
    mcdo_html, observed_at = cached("mcdo_snack_match.html", MCDO_PROMO)
    records.extend(parse_mcdo_prices(mcdo_html, observed_at))
    home, _ = cached("jollibee_home.html", JOLLIBEE_HOME)
    category_paths = sorted({
        a["href"] for a in BeautifulSoup(home, "html.parser").select('a[href^="/menu/"]')
        if len(a["href"].split("/")) == 3
    })
    if not category_paths:
        raise ValueError("Jollibee home page no longer links to menu categories.")
    detail_paths = set()
    for path in category_paths:
        html, _ = cached(f"jollibee_category_{path.rsplit('/', 1)[-1]}.html", urljoin(JOLLIBEE_HOME, path))
        detail_paths.update(
            a["href"] for a in BeautifulSoup(html, "html.parser").select("a[href]")
            if a.get_text(" ", strip=True) == "View item" and a["href"].startswith("/menu/")
        )
    if not detail_paths:
        raise ValueError("Jollibee menu categories no longer link to products.")

    def product(path: str) -> dict:
        filename = "jollibee_product_" + path.rsplit("/", 1)[-1] + ".html"
        url = urljoin(JOLLIBEE_HOME, path)
        html, observed_at = cached(filename, url)
        return parse_jollibee_price(html, url, observed_at)

    # Limit concurrency while collecting independent public product pages.
    with ThreadPoolExecutor(max_workers=3) as pool:
        records.extend(pool.map(product, sorted(detail_paths)))
    prices = pl.DataFrame(records, schema=PRICE_SCHEMA).sort(["brand", "menu_category", "item_name", "order_channel"])
    metadata = {
        "price_rows": prices.height,
        "price_rows_by_brand": {brand: prices.filter(pl.col("brand") == brand).height for brand in prices["brand"].unique()},
        "coverage_note": "Public reference prices, not verified branch quotes. Jollibee collection uses the first page of each menu category and may be incomplete. KFC keeps pickup and delivery prices separate. McDo prices cover the sourced Snack & Match promotion only.",
        "sources": [JOLLIBEE_HOME + "menu", KFC_MENU, MCDO_PROMO],
    }
    return prices, metadata


def run_economics(output_dir: Path, cache_dir: Path = DEFAULT_CACHE, *, refresh: bool = False) -> dict:
    prices, metadata = collect_prices(cache_dir, refresh=refresh)
    financials, reference = load_financial_reference()
    output_dir.mkdir(parents=True, exist_ok=True)
    prices.write_csv(output_dir / "menu_prices.csv")
    financials.write_csv(output_dir / "financial_benchmarks.csv")
    metadata.update({
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "financial_checked_on": reference["checked_on"],
        "financial_note": reference["coverage_note"],
        "financial_gaps": reference["gaps"],
    })
    (output_dir / "economics_summary.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect public Philippine fast food prices and export sourced financial references.")
    parser.add_argument("--refresh", action="store_true", help="Fetch new public menu snapshots. Financial references remain dated, manually verified disclosures.")
    parser.add_argument("--output-dir", help="Override PIPELINE_OUTPUT_DIR.")
    args = parser.parse_args()
    output_dir = project_path(Path(args.output_dir) if args.output_dir else get_settings().output_dir)
    print(json.dumps(run_economics(output_dir, refresh=args.refresh), indent=2))


if __name__ == "__main__":
    main()
