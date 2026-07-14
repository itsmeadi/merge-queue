#!/usr/bin/env python3
"""Tests for merge history deduplication."""

from __future__ import annotations

import unittest
from datetime import datetime

from history import HistoryEntry, dedupe_history_by_url

URL = "https://github.com/GetStream/chat/pull/14699"
URL2 = "https://github.com/GetStream/chat/pull/14701"


class DedupeHistoryTest(unittest.TestCase):
    def test_keeps_newest_per_url(self) -> None:
        entries = [
            HistoryEntry(datetime(2026, 7, 14, 15, 12), URL, "merged", "merged"),
            HistoryEntry(datetime(2026, 7, 14, 15, 12), URL, "merged", "merged"),
            HistoryEntry(datetime(2026, 7, 14, 12, 55), URL2, "merged", "merged"),
        ]
        deduped = dedupe_history_by_url(entries)
        self.assertEqual(len(deduped), 2)
        self.assertEqual(deduped[0].url, URL)
        self.assertEqual(deduped[1].url, URL2)


if __name__ == "__main__":
    unittest.main()
