#!/usr/bin/env python3
"""Tests for living queue status board."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from slack_status import (
    build_queue_status_text,
    load_status_anchor,
    refresh_queue_status,
    save_status_anchor,
)


URL = "https://github.com/GetStream/chat/pull/14699"


class QueueStatusAnchorTest(unittest.TestCase):
    def test_save_and_load_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "queue-status.json"
            save_status_anchor(path, "C123", "1234.5678")
            self.assertEqual(load_status_anchor(path), {"channel_id": "C123", "message_ts": "1234.5678"})

    def test_load_missing_returns_empty(self) -> None:
        self.assertEqual(load_status_anchor(Path("/nonexistent/queue-status.json")), {})


class BuildQueueStatusTextTest(unittest.TestCase):
    def test_build_from_queue_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            queue_file = base / "prs.txt"
            processing_file = base / "processing.txt"
            threads_file = base / "prs-threads.json"
            queue_file.write_text(f"{URL}\n")
            processing_file.write_text(URL)
            threads_file.write_text(
                json.dumps(
                    {
                        URL: {
                            "title": "Add feeds translation",
                            "author": "aditya",
                        }
                    }
                )
                + "\n"
            )

            with patch.dict(
                "os.environ",
                {
                    "MERGE_QUEUE_DIR": str(base),
                    "PR_QUEUE_FILE": str(queue_file),
                    "PR_PROCESSING_FILE": str(processing_file),
                    "PR_THREADS_FILE": str(threads_file),
                },
                clear=False,
            ):
                text = build_queue_status_text()
            self.assertIn("Who's in line?", text)
            self.assertIn(":loading:", text)
            self.assertIn("processing", text)
            self.assertIn("@aditya", text)
            self.assertIn("Add feeds translation", text)


class RefreshQueueStatusTest(unittest.TestCase):
    def test_posts_new_message_when_no_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            anchor = Path(tmp) / "queue-status.json"
            client = MagicMock()
            client.chat_postMessage.return_value = {"ts": "9999.0001"}

            with patch("slack_status._create_slack_client", return_value=client), patch(
                "slack_status.build_queue_status_text", return_value="board text"
            ):
                ok = refresh_queue_status(
                    channel_id="C1",
                    anchor_file=anchor,
                    token="xoxb-test",
                )

            self.assertTrue(ok)
            client.chat_postMessage.assert_called_once_with(channel="C1", text="board text")
            self.assertEqual(load_status_anchor(anchor)["message_ts"], "9999.0001")

    def test_updates_existing_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            anchor = Path(tmp) / "queue-status.json"
            anchor.write_text(json.dumps({"channel_id": "C1", "message_ts": "1111.2222"}) + "\n")
            client = MagicMock()

            with patch("slack_status._create_slack_client", return_value=client), patch(
                "slack_status.build_queue_status_text",
                return_value="done board",
            ):
                ok = refresh_queue_status(
                    channel_id="C1",
                    anchor_file=anchor,
                    finished_url=URL,
                    finished_label="done",
                    token="xoxb-test",
                )

            self.assertTrue(ok)
            client.chat_update.assert_called_once_with(
                channel="C1",
                ts="1111.2222",
                text="done board",
            )
            client.chat_postMessage.assert_not_called()


if __name__ == "__main__":
    unittest.main()
