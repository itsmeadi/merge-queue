#!/usr/bin/env python3
"""Tests for Slack message formatters."""

from __future__ import annotations

import unittest

from messages import (
    format_ci_failed,
    format_ci_rerun,
    format_history_entry,
    format_merged,
    format_preflight_reject,
    format_pr_descriptor,
    format_queued,
    format_queue_status,
    format_removed,
    format_remove_not_found,
    format_remove_processing,
    pr_link,
    reason_emoji,
    truncate_title,
)


URL = "https://github.com/GetStream/chat/pull/14699"
URL2 = "https://github.com/GetStream/chat/pull/14701"
URL3 = "https://github.com/GetStream/chat/pull/14705"
META = {
    URL: ("Add feeds translation", "aditya"),
    URL2: ("Fix channel state bug", "jane"),
}


class TruncateTitleTest(unittest.TestCase):
    def test_short_title_unchanged(self) -> None:
        self.assertEqual(truncate_title("Short title", max_len=50), "Short title")

    def test_long_title_truncated(self) -> None:
        title = "A" * 60
        self.assertEqual(truncate_title(title, max_len=50), "A" * 49 + "…")


class FormatPrDescriptorTest(unittest.TestCase):
    def test_full_descriptor(self) -> None:
        text = format_pr_descriptor(URL, "Add feeds translation", "aditya")
        self.assertIn(pr_link(URL), text)
        self.assertIn("@aditya", text)
        self.assertIn("Add feeds translation", text)

    def test_missing_meta_falls_back_to_link(self) -> None:
        self.assertEqual(format_pr_descriptor(URL), pr_link(URL))

    def test_title_only(self) -> None:
        text = format_pr_descriptor(URL, title="Add feeds translation")
        self.assertIn(pr_link(URL), text)
        self.assertIn("Add feeds translation", text)
        self.assertNotIn("@", text)


class FormatQueuedTest(unittest.TestCase):
    def test_single_pr_up_next_has_descriptor(self) -> None:
        text = format_queued(
            URL,
            1,
            True,
            [URL],
            title="Add feeds translation",
            author="aditya",
        )
        self.assertIn(pr_link(URL), text)
        self.assertIn("@aditya", text)
        self.assertIn("Add feeds translation", text)
        self.assertIn("up next", text)
        self.assertNotIn("Who's in line?", text)

    def test_multi_pr_shows_queue_with_you_marker(self) -> None:
        queue = [URL, URL2, URL3]
        text = format_queued(URL2, 2, True, queue, meta=META)
        self.assertIn("spot 2 of 3", text)
        self.assertIn(":point_right:", text)
        self.assertIn("· you", text)
        self.assertIn("@aditya", text)
        self.assertIn("@jane", text)
        self.assertNotIn("Who's in line?", text)

    def test_already_queued(self) -> None:
        text = format_queued(URL, 2, False, [URL, URL2], title="Add feeds", author="aditya")
        self.assertIn("already queued", text)
        self.assertIn("@aditya", text)

    def test_title_on_single_pr(self) -> None:
        text = format_queued(URL, 1, True, [URL], title="Add feeds translation", author="aditya")
        self.assertIn("Add feeds translation", text)
        self.assertIn("@aditya", text)


class FormatQueueStatusTest(unittest.TestCase):
    def test_status_uses_links(self) -> None:
        text = format_queue_status([URL, URL2], 0, 0, meta=META)
        self.assertIn(pr_link(URL), text)
        self.assertIn("@aditya", text)
        self.assertIn("@jane", text)

    def test_status_header_emoji(self) -> None:
        text = format_queue_status([URL], 0, 0)
        self.assertIn(":hourglass_flowing_sand:", text)
        self.assertIn("Who's in line?", text)

    def test_processing_shows_loading(self) -> None:
        text = format_queue_status([URL2], 0, 0, processing_url=URL, meta=META)
        self.assertIn(":loading:", text)
        self.assertIn("processing", text)
        self.assertIn("@aditya", text)

    def test_finished_shows_merged(self) -> None:
        text = format_queue_status(
            [URL2],
            0,
            0,
            finished_url=URL,
            finished_label="merged",
            meta=META,
        )
        self.assertIn(":white_check_mark:", text)
        self.assertIn("· merged", text)
        self.assertIn("@aditya", text)
        self.assertNotIn(":loading:", text)

    def test_finished_skipped_emoji(self) -> None:
        text = format_queue_status([], 0, 1, finished_url=URL, finished_label="skipped", meta=META)
        self.assertIn(":rabbit2:", text)
        self.assertIn("· skipped", text)


class FormatPreflightRejectTest(unittest.TestCase):
    def test_includes_link_and_cute_reason(self) -> None:
        text = format_preflight_reject(URL, "missing approval", title="Needs review", author="aditya")
        self.assertIn(":no_entry:", text)
        self.assertIn(pr_link(URL), text)
        self.assertIn("@aditya", text)
        self.assertIn("not queued", text)
        self.assertIn("still needs a thumbs-up", text)


class FormatRemoveTest(unittest.TestCase):
    def test_removed(self) -> None:
        text = format_removed(URL, 2, title="Add feeds", author="aditya")
        self.assertIn(":wastebasket:", text)
        self.assertIn("@aditya", text)
        self.assertIn("was spot 2", text)

    def test_not_found(self) -> None:
        text = format_remove_not_found(URL, title="Add feeds", author="aditya")
        self.assertIn("not in queue", text)
        self.assertIn("@aditya", text)

    def test_processing(self) -> None:
        text = format_remove_processing(URL, title="Add feeds", author="aditya")
        self.assertIn("already merging", text)
        self.assertIn("@aditya", text)


class FormatMergedTest(unittest.TestCase):
    def test_merged_uses_checkmark(self) -> None:
        text = format_merged(URL, title="Add feeds", author="aditya")
        self.assertIn(":white_check_mark:", text)
        self.assertIn("merged", text)
        self.assertNotIn("merged and done", text)
        self.assertIn("@aditya", text)

    def test_reason_emoji_merged(self) -> None:
        self.assertEqual(reason_emoji("merged"), ":white_check_mark:")


class FormatHistoryTest(unittest.TestCase):
    def test_history_entry_includes_meta(self) -> None:
        from datetime import datetime

        text = format_history_entry(
            datetime(2026, 7, 14, 15, 12),
            URL,
            "merged",
            "merged",
            title="Add feeds translation",
            author="aditya",
        )
        self.assertIn("@aditya", text)
        self.assertIn("Add feeds translation", text)
        self.assertIn("merged", text)
        self.assertNotIn("merged and done", text)


class FormatCiTest(unittest.TestCase):
    def test_ci_rerun_with_summary(self) -> None:
        summary = {
            "failed_checks": ["Chat CI / Unit (default-1)"],
            "excerpt": "--- FAIL: TestFoo\n    foo_test.go:42: boom",
        }
        text = format_ci_rerun(URL, 1, 3, summary, title="Fix tests", author="aditya")
        self.assertIn("retry 1/3", text)
        self.assertIn("Chat CI / Unit (default-1)", text)
        self.assertIn("TestFoo", text)
        self.assertIn("@aditya", text)

    def test_ci_failed_with_summary(self) -> None:
        summary = {"failed_checks": ["Ready to merge"], "excerpt": ""}
        text = format_ci_failed(URL, 3, summary, title="Fix tests", author="aditya")
        self.assertIn("after 3 retries", text)
        self.assertIn("Ready to merge", text)
        self.assertIn("@aditya", text)


if __name__ == "__main__":
    unittest.main()
