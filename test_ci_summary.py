#!/usr/bin/env python3
"""Tests for CI log excerpt parsing."""

from __future__ import annotations

import unittest

from ci_summary import parse_excerpt


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


if __name__ == "__main__":
    unittest.main()
