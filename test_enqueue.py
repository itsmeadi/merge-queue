#!/usr/bin/env python3
"""Tests for enqueue.py CLI."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import enqueue
from pr_preflight import PreflightResult

URL = "https://github.com/GetStream/chat/pull/14699"


class EnqueueTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.queue_file = Path(self.tmp.name) / "prs.txt"
        enqueue.PR_QUEUE_FILE = self.queue_file
        enqueue.QUEUE_DATA_DIR = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    @patch("enqueue.check_pr_preflight")
    def test_queues_on_success(self, preflight_mock: unittest.mock.Mock) -> None:
        preflight_mock.return_value = PreflightResult(ok=True, title="My PR")
        result = enqueue.enqueue(URL)
        self.assertTrue(result["ok"])
        self.assertTrue(result["queued"])
        self.assertEqual(result["position"], 1)
        self.assertEqual(self.queue_file.read_text().strip(), URL)

    @patch("enqueue.check_pr_preflight")
    def test_rejects_preflight_failure(self, preflight_mock: unittest.mock.Mock) -> None:
        preflight_mock.return_value = PreflightResult(ok=False, reason="missing approval")
        result = enqueue.enqueue(URL)
        self.assertFalse(result["ok"])
        self.assertFalse(self.queue_file.exists())

    @patch("enqueue.check_pr_preflight")
    def test_idempotent_when_already_queued(self, preflight_mock: unittest.mock.Mock) -> None:
        preflight_mock.return_value = PreflightResult(ok=True, title="My PR")
        enqueue.enqueue(URL)
        result = enqueue.enqueue(URL)
        self.assertTrue(result["ok"])
        self.assertFalse(result["queued"])
        self.assertEqual(result["position"], 1)
        self.assertEqual(self.queue_file.read_text().count(URL), 1)

    def test_malformed_input(self) -> None:
        result = enqueue.enqueue("not-a-pr")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "malformed url")


class EnqueueCliTest(unittest.TestCase):
    @patch("enqueue.enqueue")
    def test_json_output(self, enqueue_mock: unittest.mock.Mock) -> None:
        import io

        enqueue_mock.return_value = {"ok": True, "url": URL, "queued": True, "position": 1}
        buffer = io.StringIO()
        with patch("sys.argv", ["enqueue.py", URL, "--json"]):
            with patch("sys.stdout", buffer):
                with self.assertRaises(SystemExit) as ctx:
                    enqueue.main()
        self.assertEqual(ctx.exception.code, 0)
        payload = json.loads(buffer.getvalue())
        self.assertTrue(payload["ok"])


if __name__ == "__main__":
    unittest.main()
