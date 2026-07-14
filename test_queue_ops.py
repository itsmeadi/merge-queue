#!/usr/bin/env python3
"""Tests for queue file operations."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from queue_meta import save_thread
from queue_ops import append_to_queue, read_queue, remove_from_queue

URL = "https://github.com/GetStream/chat/pull/14699"
URL2 = "https://github.com/GetStream/chat/pull/14701"


class QueueOpsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.queue_file = base / "prs.txt"
        self.threads_file = base / "prs-threads.json"
        self.processing_file = base / "processing.txt"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_remove_from_queue(self) -> None:
        append_to_queue(self.queue_file, URL)
        append_to_queue(self.queue_file, URL2)
        status, position = remove_from_queue(
            self.queue_file, self.threads_file, self.processing_file, URL
        )
        self.assertEqual(status, "removed")
        self.assertEqual(position, 1)
        self.assertEqual(read_queue(self.queue_file), [URL2])

    def test_remove_not_found(self) -> None:
        status, position = remove_from_queue(
            self.queue_file, self.threads_file, self.processing_file, URL
        )
        self.assertEqual(status, "not_found")
        self.assertEqual(position, 0)

    def test_remove_processing_blocked(self) -> None:
        append_to_queue(self.queue_file, URL)
        self.processing_file.write_text(URL)
        status, position = remove_from_queue(
            self.queue_file, self.threads_file, self.processing_file, URL
        )
        self.assertEqual(status, "processing")
        self.assertEqual(read_queue(self.queue_file), [URL])

    def test_remove_clears_thread_meta(self) -> None:
        append_to_queue(self.queue_file, URL)
        save_thread(self.threads_file, URL, "C123", "1234.5678")
        remove_from_queue(self.queue_file, self.threads_file, self.processing_file, URL)
        self.assertNotIn(URL, self.threads_file.read_text())


if __name__ == "__main__":
    unittest.main()
