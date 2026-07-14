#!/usr/bin/env python3
"""Tests for queue metadata (threads + requesters)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from queue_meta import (
    clear_thread,
    lookup_user_id,
    save_requester,
    save_thread,
    upsert_queue_meta,
)

URL = "https://github.com/GetStream/chat/pull/14699"


class QueueMetaTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "prs-threads.json"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_save_and_lookup_requester(self) -> None:
        save_requester(self.path, URL, "U123")
        self.assertEqual(lookup_user_id(self.path, URL), "U123")

    def test_thread_save_preserves_requester(self) -> None:
        save_requester(self.path, URL, "U123")
        save_thread(self.path, URL, "C456", "1111.2222")
        self.assertEqual(lookup_user_id(self.path, URL), "U123")

    def test_clear_removes_requester(self) -> None:
        save_thread(self.path, URL, "C456", "1111.2222", user_id="U123")
        clear_thread(self.path, URL)
        self.assertEqual(lookup_user_id(self.path, URL), "")


if __name__ == "__main__":
    unittest.main()
