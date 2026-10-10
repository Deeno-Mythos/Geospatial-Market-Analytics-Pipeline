import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from helpers import local_settings
from src.pipelines.load import load_motherduck, load_r2
from src.pipelines.transform import transform_records


class CloudLoadTests(unittest.TestCase):
    @patch("src.pipelines.load.duckdb.connect")
    @patch("src.pipelines.load.boto3.client")
    def test_local_configuration_does_not_contact_cloud_services(self, client, connect):
        settings = local_settings(Path("unused"))
        load_motherduck(Path("locations.parquet"), settings)
        load_r2(Path("locations.parquet"), settings)
        connect.assert_not_called()
        client.assert_not_called()

    @patch("src.pipelines.load.boto3.client")
    def test_partial_r2_configuration_fails_before_upload(self, client):
        settings = local_settings(Path("unused"), r2_bucket="markets")
        with self.assertRaisesRegex(ValueError, "R2 upload requires"):
            load_r2(Path("locations.parquet"), settings)
        client.assert_not_called()

    @patch("src.pipelines.load.boto3.client")
    def test_uploads_parquet_to_configured_bucket(self, client):
        settings = local_settings(
            Path("unused"), r2_account_id="account", r2_bucket="markets",
            r2_access_key_id="access", r2_secret_access_key="secret",
            r2_endpoint_url="https://account.r2.cloudflarestorage.com",
        )
        path = Path("locations.parquet")
        load_r2(path, settings)
        client.return_value.upload_file.assert_called_once_with(
            str(path), "markets", "market_locations.parquet",
        )
        self.assertEqual(client.call_args.kwargs["endpoint_url"], settings.r2_endpoint_url)

    @patch("src.pipelines.load.duckdb.connect")
    def test_database_requires_token(self, connect):
        settings = local_settings(Path("unused"), motherduck_database="markets")
        with self.assertRaisesRegex(ValueError, "MOTHERDUCK_TOKEN is required"):
            load_motherduck(Path("locations.parquet"), settings)
        connect.assert_not_called()

    @patch("src.pipelines.load.duckdb.connect")
    def test_database_connection_closes_after_load_error(self, connect):
        connect.return_value.execute.side_effect = RuntimeError("database unavailable")
        settings = local_settings(
            Path("unused"), motherduck_database="markets", motherduck_token="secret",
        )
        with self.assertRaisesRegex(RuntimeError, "database unavailable"):
            load_motherduck(Path("locations.parquet"), settings)
        connect.return_value.close.assert_called_once()

    def test_database_load_sql_reads_real_parquet(self):
        import duckdb

        locations, _, _ = transform_records([{"name": "Cafe", "lat": 30, "lng": -97}])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "locations.parquet"
            locations.write_parquet(path)
            connection = duckdb.connect()
            # Keep the real SQL engine while replacing the remote connection only.
            proxy = Mock(wraps=connection)
            proxy.close = Mock()
            settings = local_settings(
                Path(directory), motherduck_database="markets", motherduck_token="secret",
            )
            try:
                with patch("src.pipelines.load.duckdb.connect", return_value=proxy):
                    load_motherduck(path, settings)
                result = connection.execute("SELECT business_name FROM market_locations").fetchall()
                self.assertEqual(result, [("Cafe",)])
                proxy.close.assert_called_once()
            finally:
                connection.close()
