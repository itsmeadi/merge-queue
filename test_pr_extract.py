#!/usr/bin/env python3
"""Tests for PR URL extraction from Slack messages."""

from __future__ import annotations

import unittest

from pr_extract import extract_pr_urls, message_text_parts

URL = "https://github.com/GetStream/chat/pull/14699"
OTHER = "https://github.com/other/repo/pull/1"


class MessageTextPartsTest(unittest.TestCase):
    def test_github_attachment(self) -> None:
        msg = {
            "text": "Pull request opened by aditya",
            "attachments": [
                {
                    "title": "[CHA-1234] Add feeds translation",
                    "title_link": URL,
                    "text": URL,
                    "fallback": "[GetStream/chat] Pull Request #14699",
                }
            ],
        }
        parts = message_text_parts(msg)
        self.assertIn(URL, parts)
        self.assertIn("Pull request opened by aditya", parts)


class ExtractPrUrlsTest(unittest.TestCase):
    def test_slack_mrkdwn_link(self) -> None:
        msg = {"text": f"Queued · <{URL}|#14699> · up next"}
        self.assertEqual(extract_pr_urls(msg), [URL])

    def test_plain_url(self) -> None:
        msg = {"text": f"Please review {URL}"}
        self.assertEqual(extract_pr_urls(msg), [URL])

    def test_filters_by_default_repo(self) -> None:
        msg = {"text": f"{URL} and {OTHER}"}
        self.assertEqual(
            extract_pr_urls(msg, default_repo="GetStream/chat"),
            [URL],
        )

    def test_no_urls(self) -> None:
        msg = {"text": "no pr here"}
        self.assertEqual(extract_pr_urls(msg), [])

    def test_dedupes(self) -> None:
        msg = {
            "attachments": [{"title_link": URL, "text": URL}],
        }
        self.assertEqual(extract_pr_urls(msg), [URL])

    def test_strips_query_string(self) -> None:
        msg = {"text": f"{URL}?foo=bar"}
        self.assertEqual(extract_pr_urls(msg), [URL])


if __name__ == "__main__":
    unittest.main()
