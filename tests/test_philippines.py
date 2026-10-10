import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.pipelines.philippines import (
    BoundaryLookup, collect_philippines_fastfood, enrich_places,
    parse_jollibee, parse_kfc, parse_mcdonalds,
)


def boundaries(name):
    return {"type": "FeatureCollection", "features": [{
        "type": "Feature", "properties": {"shapeName": name},
        "geometry": {"type": "Polygon", "coordinates": [[[120, 14], [122, 14], [122, 16], [120, 16], [120, 14]]]},
    }]}


def mcdo_html():
    stores = [{
        "id": 1, "name": "McDonald's Test", "latitude": "15", "longitude": "121",
        "city_name": "Source City", "province_name": "Source Province", "address": "Test Street",
        "status": 1, "description": None,
    }, {"id": 2, "status": 0}]
    import html
    return '<store-locator :stores="' + html.escape(json.dumps(stores), quote=True) + '"></store-locator>'


def kfc_html():
    store = {
        "id": "test", "title": {"en_US": "KFC Test"},
        "address": {"city": "Santa Monica, CA, USA", "countryCode": "US", "formatted": "Test Avenue", "latLng": {"lat": 15, "lng": 121}},
        "contact": {"phone": "test-phone"}, "openingHours": [{"en": [["Open All Day"]] * 7}],
    }
    payload = {"props": {"pageProps": {"data": {"chainStores": {"msg": [store]}}}}}
    return '<script id="__NEXT_DATA__" type="application/json">' + json.dumps(payload) + '</script>'


class PhilippinesParserTests(unittest.TestCase):
    def test_jollibee_requires_complete_list_and_preserves_branch_link(self):
        payload = {"count": 1, "stores": [{"id": 1, "title": "Jollibee Test", "lat": 15, "lng": 121, "url": "/stores/test"}]}
        records = parse_jollibee(payload)
        self.assertEqual(records[0]["source_url"], "https://www.jollibee.com.ph/stores/test")
        self.assertEqual(records[0]["brand"], "Jollibee")
        payload["count"] = 2
        with self.assertRaisesRegex(ValueError, "incomplete"):
            parse_jollibee(payload)

    def test_mcdonalds_handles_null_hours_and_disabled_records(self):
        records = parse_mcdonalds(mcdo_html())
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["opening_hours"], "")
        self.assertEqual(records[0]["address"], "Test Street")
        with self.assertRaisesRegex(ValueError, "no longer exposes"):
            parse_mcdonalds("<html>Changed page</html>")

    def test_kfc_does_not_trust_overseas_address_labels(self):
        records = parse_kfc(kfc_html())
        self.assertEqual(len(records), 1)
        self.assertNotIn("city", records[0])
        self.assertEqual(records[0]["phone"], "test-phone")
        self.assertIn("Mon: Open All Day", records[0]["opening_hours"])

    def test_geography_disambiguates_manila_and_rejects_bad_coordinates(self):
        municipality = BoundaryLookup(boundaries("Binondo"))
        province = BoundaryLookup(boundaries("NCR, City of Manila, First District"))
        accepted, excluded = enrich_places([
            {"source_id": "a", "latitude": 15, "longitude": 121},
            {"source_id": "a", "latitude": 15, "longitude": 121},
            {"source_id": "b", "latitude": 0, "longitude": 0},
            {"source_id": "c", "latitude": "NaN", "longitude": 121},
        ], municipality, province)
        self.assertEqual(len(accepted), 1)
        self.assertEqual(len(excluded), 2)
        self.assertEqual(accepted[0]["city"], "Manila City")
        self.assertEqual(accepted[0]["region"], "Metro Manila")
        self.assertEqual(accepted[0]["locality"], "Binondo")

    @patch("src.pipelines.philippines.requests.Session")
    def test_cached_collection_is_offline_and_retains_provenance(self, session):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            files = {
                "jollibee.html": '<div data-store-map-src="/stores/map-data"></div>',
                "jollibee_stores.json": json.dumps({"count": 1, "stores": [{"id": 1, "title": "Jollibee Test", "lat": 15, "lng": 121, "url": "/stores/test"}]}),
                "mcdo_stores.html": mcdo_html(), "kfc.html": kfc_html(),
            }
            for level, name in (("ADM2", "Test Province"), ("ADM3", "Test City")):
                files[f"{level}_metadata.json"] = json.dumps({"simplifiedGeometryGeoJSON": "https://example.com/boundary", "boundaryYearRepresented": "2020", "boundaryLicense": "test", "boundarySource": "test"})
                files[f"{level}.geojson"] = json.dumps(boundaries(name))
            for filename, contents in files.items():
                (cache / filename).write_text(contents, encoding="utf-8")
            records, metadata = collect_philippines_fastfood(cache)
            self.assertEqual(len(records), 3)
            self.assertEqual({record["city"] for record in records}, {"Test City"})
            self.assertTrue(all(record["source_url"] and record["collected_at_utc"] for record in records))
            self.assertFalse(any("rating" in record for record in records))
            self.assertEqual(metadata["included_counts"], {"Jollibee": 1, "McDonald's": 1, "KFC": 1})
            self.assertTrue((cache / "source_manifest.json").is_file())
            session.return_value.get.assert_not_called()
            session.return_value.close.assert_called_once()
