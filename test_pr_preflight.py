#!/usr/bin/env python3
"""Tests for GitHub PR preflight checks."""

from __future__ import annotations

import json
import subprocess
import unittest
from unittest.mock import patch

from pr_preflight import PreflightResult, _evaluate_pr, check_pr_preflight

URL = "https://github.com/GetStream/chat/pull/14699"


def _completed(stdout: str, returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["gh", "pr", "view", URL],
        returncode=returncode,
        stdout=stdout,
        stderr="",
    )


class EvaluatePrTest(unittest.TestCase):
    def test_open_approved_ok(self) -> None:
        result = _evaluate_pr(
            {
                "state": "OPEN",
                "mergeable": "MERGEABLE",
                "reviewDecision": "APPROVED",
                "title": "Add feeds translation",
                "author": {"login": "aditya"},
            }
        )
        self.assertEqual(
            result,
            PreflightResult(ok=True, title="Add feeds translation", author="aditya"),
        )

    def test_behind_allowed(self) -> None:
        result = _evaluate_pr(
            {
                "state": "OPEN",
                "mergeable": "UNKNOWN",
                "reviewDecision": "APPROVED",
                "title": "Behind base",
            }
        )
        self.assertTrue(result.ok)

    def test_merged_rejected(self) -> None:
        result = _evaluate_pr(
            {
                "state": "MERGED",
                "mergeable": "UNKNOWN",
                "reviewDecision": "APPROVED",
                "title": "Done",
            }
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "already MERGED")

    def test_closed_rejected(self) -> None:
        result = _evaluate_pr(
            {
                "state": "CLOSED",
                "mergeable": "UNKNOWN",
                "reviewDecision": "",
                "title": "Closed",
            }
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "already CLOSED")

    def test_conflicting_rejected(self) -> None:
        result = _evaluate_pr(
            {
                "state": "OPEN",
                "mergeable": "CONFLICTING",
                "reviewDecision": "APPROVED",
                "title": "Conflict",
            }
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "merge conflict")

    def test_review_required_rejected(self) -> None:
        result = _evaluate_pr(
            {
                "state": "OPEN",
                "mergeable": "MERGEABLE",
                "reviewDecision": "REVIEW_REQUIRED",
                "title": "Needs review",
            }
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "missing approval")

    def test_changes_requested_rejected(self) -> None:
        result = _evaluate_pr(
            {
                "state": "OPEN",
                "mergeable": "MERGEABLE",
                "reviewDecision": "CHANGES_REQUESTED",
                "title": "Fix tests",
            }
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "changes requested")


class CheckPrPreflightTest(unittest.TestCase):
    @patch("pr_preflight.subprocess.run")
    def test_not_found(self, run_mock: unittest.mock.Mock) -> None:
        run_mock.return_value = _completed("", returncode=1)
        result = check_pr_preflight(URL)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "gh pr view failed")

    @patch("pr_preflight.subprocess.run")
    def test_open_approved_ok(self, run_mock: unittest.mock.Mock) -> None:
        run_mock.return_value = _completed(
            json.dumps(
                {
                    "state": "OPEN",
                    "mergeable": "MERGEABLE",
                    "reviewDecision": "APPROVED",
                    "title": "Add feeds translation",
                    "author": {"login": "aditya"},
                }
            )
        )
        result = check_pr_preflight(URL)
        self.assertTrue(result.ok)
        self.assertEqual(result.title, "Add feeds translation")
        self.assertEqual(result.author, "aditya")


if __name__ == "__main__":
    unittest.main()
