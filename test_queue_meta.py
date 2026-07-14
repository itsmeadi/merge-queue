#!/usr/bin/env python3
"""Tests for queue metadata (threads + requesters)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from queue_meta import (
    build_meta_map,
    build_meta_map_with_fallback,
    clear_thread,
    lookup_pr_meta,
    lookup_user_id,
    save_pr_meta,
    save_requester,
    save_thread,
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

    def test_save_and_lookup_pr_meta(self) -> None:
        save_pr_meta(self.path, URL, "Add feeds translation", "aditya")
        title, author = lookup_pr_meta(self.path, URL)
        self.assertEqual(title, "Add feeds translation")
        self.assertEqual(author, "aditya")

    def test_clear_thread_preserves_pr_meta(self) -> None:
        save_thread(self.path, URL, "C456", "1111.2222", user_id="U123")
        save_pr_meta(self.path, URL, "Add feeds translation", "aditya")
        clear_thread(self.path, URL)
        self.assertEqual(lookup_user_id(self.path, URL), "")
        title, author = lookup_pr_meta(self.path, URL)
        self.assertEqual(title, "Add feeds translation")
        self.assertEqual(author, "aditya")

    def test_build_meta_map(self) -> None:
        save_pr_meta(self.path, URL, "Add feeds translation", "aditya")
        meta = build_meta_map(self.path, [URL])
        self.assertEqual(meta[URL], ("Add feeds translation", "aditya"))

    @patch("pr_preflight.fetch_pr_meta")
    def test_build_meta_map_with_fallback_fetches_missing(self, fetch_mock: unittest.mock.Mock) -> None:
        fetch_mock.return_value = ("Fetched title", "octocat")
        meta = build_meta_map_with_fallback(self.path, [URL])
        self.assertEqual(meta[URL], ("Fetched title", "octocat"))
        fetch_mock.assert_called_once_with(URL)
        title, author = lookup_pr_meta(self.path, URL)
        self.assertEqual(title, "Fetched title")
        self.assertEqual(author, "octocat")

    @patch("pr_preflight.fetch_pr_meta")
    def test_build_meta_map_with_fallback_skips_cached(self, fetch_mock: unittest.mock.Mock) -> None:
        save_pr_meta(self.path, URL, "Cached title", "aditya")
        meta = build_meta_map_with_fallback(self.path, [URL])
        self.assertEqual(meta[URL], ("Cached title", "aditya"))
        fetch_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
