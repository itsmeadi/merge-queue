#!/usr/bin/env python3
"""Tests for Slack message formatters."""

from __future__ import annotations

import unittest

from messages import (
    format_ci_failed,
    format_ci_rerun,
    format_merged,
    format_preflight_reject,
    format_queued,
    format_queue_status,
    pr_link,
    reason_emoji,
)


URL = "https://github.com/GetStream/chat/pull/14699"
URL2 = "https://github.com/GetStream/chat/pull/14701"
URL3 = "https://github.com/GetStream/chat/pull/14705"


class FormatQueuedTest(unittest.TestCase):
    def test_single_pr_up_next_has_link_no_duplicate(self) -> None:
        text = format_queued(URL, 1, True, [URL])
        self.assertIn(pr_link(URL), text)
        self.assertIn("up next", text)
        self.assertNotIn("Who's in line?", text)
        self.assertEqual(text.count("#14699"), 1)

    def test_multi_pr_shows_queue_with_you_marker(self) -> None:
        queue = [URL, URL2, URL3]
        text = format_queued(URL2, 2, True, queue)
        self.assertIn("spot 2 of 3", text)
        self.assertIn(":point_right:", text)
        self.assertIn("· you", text)
        self.assertIn(pr_link(URL), text)
        self.assertIn(pr_link(URL2), text)
        self.assertIn(pr_link(URL3), text)
        self.assertNotIn("Who's in line?", text)

    def test_already_queued(self) -> None:
        text = format_queued(URL, 2, False, [URL, URL2])
        self.assertIn("already queued", text)
        self.assertIn(pr_link(URL), text)

    def test_title_on_single_pr(self) -> None:
        text = format_queued(URL, 1, True, [URL], title="Add feeds translation")
        self.assertIn("Add feeds translation", text)
        self.assertIn(pr_link(URL), text)


class FormatQueueStatusTest(unittest.TestCase):
    def test_status_uses_links(self) -> None:
        text = format_queue_status([URL, URL2], 0, 0)
        self.assertIn(pr_link(URL), text)
        self.assertIn(pr_link(URL2), text)

    def test_status_header_emoji(self) -> None:
        text = format_queue_status([URL], 0, 0)
        self.assertIn(":hourglass_flowing_sand:", text)
        self.assertIn("Who's in line?", text)


class FormatPreflightRejectTest(unittest.TestCase):
    def test_includes_link_and_cute_reason(self) -> None:
        text = format_preflight_reject(URL, "missing approval")
        self.assertIn(":no_entry:", text)
        self.assertIn(pr_link(URL), text)
        self.assertIn("not queued", text)
        self.assertIn("still needs a thumbs-up", text)


class FormatMergedTest(unittest.TestCase):
    def test_merged_uses_checkmark(self) -> None:
        text = format_merged(URL)
        self.assertIn(":white_check_mark:", text)
        self.assertIn("merged and done", text)

    def test_reason_emoji_merged(self) -> None:
        self.assertEqual(reason_emoji("merged"), ":white_check_mark:")


class FormatCiTest(unittest.TestCase):
    def test_ci_rerun_with_summary(self) -> None:
        summary = {
            "failed_checks": ["Chat CI / Unit (default-1)"],
            "excerpt": "--- FAIL: TestFoo\n    foo_test.go:42: boom",
        }
        text = format_ci_rerun(URL, 1, 3, summary)
        self.assertIn("retry 1/3", text)
        self.assertIn("Chat CI / Unit (default-1)", text)
        self.assertIn("TestFoo", text)
        self.assertIn(pr_link(URL), text)

    def test_ci_failed_with_summary(self) -> None:
        summary = {"failed_checks": ["Ready to merge"], "excerpt": ""}
        text = format_ci_failed(URL, 3, summary)
        self.assertIn("after 3 retries", text)
        self.assertIn("Ready to merge", text)


if __name__ == "__main__":
    unittest.main()
