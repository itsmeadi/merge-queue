#!/usr/bin/env python3
"""Tests for CI log excerpt parsing and rerun run selection."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from ci_summary import (
    extract_run_id,
    find_rerun_run_id,
    parse_excerpt,
)


GO_LOG = """
some noise
--- FAIL: TestGetOrCreateFeed (1.23s)
    activity_state_test.go:891: expected 200, got 500
--- PASS: TestOther (0.01s)
"""

GENERIC_LOG = """
Step finished
Error: something went wrong
exit code 1
"""

URL = "https://github.com/GetStream/chat/pull/14793"


class ParseExcerptTest(unittest.TestCase):
    def test_go_test_failure(self) -> None:
        excerpt = parse_excerpt(GO_LOG)
        self.assertIn("--- FAIL: TestGetOrCreateFeed", excerpt)
        self.assertIn("activity_state_test.go:891", excerpt)

    def test_generic_failure(self) -> None:
        excerpt = parse_excerpt(GENERIC_LOG)
        self.assertIn("Error:", excerpt)

    def test_empty_log(self) -> None:
        self.assertEqual(parse_excerpt(""), "")
        self.assertEqual(parse_excerpt("   \n  "), "")


class ExtractRunIdTest(unittest.TestCase):
    def test_from_actions_link(self) -> None:
        link = "https://github.com/GetStream/chat/actions/runs/29444227721/job/123"
        self.assertEqual(extract_run_id(link), "29444227721")


class FindRerunRunIdTest(unittest.TestCase):
    @patch("ci_summary.list_failed_run_id")
    @patch("ci_summary.fetch_head_ref")
    @patch("ci_summary.fetch_failed_checks")
    def test_skips_ready_to_merge_and_uses_failed_check_link(
        self,
        checks_mock: unittest.mock.Mock,
        head_mock: unittest.mock.Mock,
        list_mock: unittest.mock.Mock,
    ) -> None:
        checks_mock.return_value = [
            {
                "name": "Ready to merge",
                "bucket": "fail",
                "link": "https://github.com/GetStream/chat/actions/runs/29444227721",
            },
            {
                "name": "Chat CI / Unit (default-1)",
                "bucket": "fail",
                "link": "https://github.com/GetStream/chat/actions/runs/11111111111/job/1",
            },
        ]
        run_id = find_rerun_run_id(URL)
        self.assertEqual(run_id, "11111111111")
        head_mock.assert_not_called()
        list_mock.assert_not_called()

    @patch("ci_summary.list_failed_run_id")
    @patch("ci_summary.fetch_head_ref")
    @patch("ci_summary.fetch_failed_checks")
    def test_falls_back_to_failed_run_list(
        self,
        checks_mock: unittest.mock.Mock,
        head_mock: unittest.mock.Mock,
        list_mock: unittest.mock.Mock,
    ) -> None:
        checks_mock.return_value = [
            {
                "name": "Ready to merge",
                "bucket": "fail",
                "link": "",
            },
        ]
        head_mock.return_value = ("my-branch", "abc123")
        list_mock.return_value = "22222222222"
        run_id = find_rerun_run_id(URL)
        self.assertEqual(run_id, "22222222222")
        list_mock.assert_called_once_with("GetStream/chat", commit="abc123")


if __name__ == "__main__":
    unittest.main()
