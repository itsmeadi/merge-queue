#!/usr/bin/env python3
"""Slack DM notifications for merge-queue outcomes."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from messages import format_dm_update
from queue_meta import lookup_user_id


def dm_user(user_id: str, text: str, *, token: str | None = None) -> bool:
    user_id = (user_id or "").strip()
    if not user_id:
        return False

    bot_token = token or os.environ.get("SLACK_BOT_TOKEN", "")
    if not bot_token:
        print("WARN: SLACK_BOT_TOKEN unset — skipping DM", file=sys.stderr)
        return False

    client = WebClient(token=bot_token)
    message = format_dm_update(text)
    try:
        opened = client.conversations_open(users=user_id)
        channel_id = opened.get("channel", {}).get("id", "")
        if not channel_id:
            print(f"WARN: conversations_open returned no channel for {user_id}", file=sys.stderr)
            return False
        client.chat_postMessage(channel=channel_id, text=message)
        return True
    except SlackApiError as exc:
        print(f"WARN: failed to DM {user_id}: {exc}", file=sys.stderr)
        return False


def dm_requester_for_pr(threads_file: Path, url: str, text: str) -> bool:
    user_id = lookup_user_id(threads_file, url)
    if not user_id:
        return False
    return dm_user(user_id, text)


def main() -> None:
    if len(sys.argv) < 3 or sys.argv[1] != "dm-for-pr":
        sys.exit(2)

    threads_file = Path(os.environ.get("PR_THREADS_FILE", "prs-threads.json"))
    url = sys.argv[2]
    text = os.environ.get("MERGE_NOTIFY_TEXT", "")
    if not text and len(sys.argv) > 3:
        text = sys.argv[3]
    if not text:
        sys.exit(2)
    sys.exit(0 if dm_requester_for_pr(threads_file, url, text) else 1)


if __name__ == "__main__":
    main()
