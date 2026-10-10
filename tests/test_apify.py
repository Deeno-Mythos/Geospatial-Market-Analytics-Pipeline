import unittest
from unittest.mock import Mock, patch

from src.pipelines.extract import extract_apify


def response(payload, total=None):
    result = Mock()
    result.json.return_value = payload
    result.headers = {} if total is None else {"X-Apify-Pagination-Total": str(total)}
    return result


class ApifyTests(unittest.TestCase):
    @patch("src.pipelines.extract.requests.get")
    @patch("src.pipelines.extract.requests.post")
    def test_waits_for_success_and_reads_all_pages(self, post, get):
        post.return_value = response({"data": {
            "id": "run-123", "status": "RUNNING", "defaultDatasetId": "dataset-123",
        }})
        get.side_effect = [
            response({"data": {"id": "run-123", "status": "RUNNING"}}),
            response({"data": {
                "id": "run-123", "status": "SUCCEEDED", "defaultDatasetId": "dataset-123",
            }}),
            response([{"name": "A"}, {}], total=3),
            response([{"name": "B"}], total=3),
        ]
        records = extract_apify("secret", "vendor/actor", '{"search":"cafes"}')
        self.assertEqual(records, [{"name": "A"}, {}, {"name": "B"}])
        self.assertTrue(post.call_args.args[0].endswith("/actors/vendor~actor/runs"))
        self.assertEqual(post.call_args.kwargs["json"], {"search": "cafes"})
        for call in [post.call_args, *get.call_args_list]:
            self.assertEqual(call.kwargs["headers"]["Authorization"], "Bearer secret")
            self.assertNotIn("token", call.kwargs["params"])
            if "waitForFinish" in call.kwargs["params"]:
                self.assertLessEqual(call.kwargs["params"]["waitForFinish"], 60)
        self.assertEqual(get.call_args_list[-1].kwargs["params"]["offset"], 2)

    @patch("src.pipelines.extract.requests.get")
    @patch("src.pipelines.extract.requests.post")
    def test_failed_run_does_not_read_partial_dataset(self, post, get):
        for status in ("FAILED", "TIMED-OUT", "ABORTED"):
            with self.subTest(status=status):
                post.return_value = response({"data": {
                    "id": "run-123", "status": status, "defaultDatasetId": "partial",
                }})
                with self.assertRaisesRegex(RuntimeError, status):
                    extract_apify("secret", "vendor/actor", "{}")
        get.assert_not_called()

    @patch("src.pipelines.extract.time.monotonic", side_effect=[0, 2])
    @patch("src.pipelines.extract.requests.get")
    @patch("src.pipelines.extract.requests.post")
    def test_wait_timeout_explains_remote_run_state(self, post, get, monotonic):
        post.return_value = response({"data": {"id": "run-123", "status": "RUNNING"}})
        with self.assertRaisesRegex(TimeoutError, "actor may still be running"):
            extract_apify("secret", "vendor/actor", "{}", wait_timeout_seconds=1)
        get.assert_not_called()

    @patch("src.pipelines.extract.requests.get")
    @patch("src.pipelines.extract.requests.post")
    def test_paginates_without_total_header(self, post, get):
        post.return_value = response({"data": {
            "id": "run-123", "status": "SUCCEEDED", "defaultDatasetId": "dataset-123",
        }})
        get.side_effect = [response([{"name": "A"}]), response([{"name": "B"}]), response([])]
        self.assertEqual(len(extract_apify("secret", "vendor/actor", "{}")), 2)

    @patch("src.pipelines.extract.requests.post")
    def test_invalid_actor_input_does_not_start_run(self, post):
        for actor_input in ("[]", "{broken"):
            with self.subTest(actor_input=actor_input), self.assertRaises(ValueError):
                extract_apify("secret", "vendor/actor", actor_input)
        post.assert_not_called()
