#!/usr/bin/env python3
"""Living Slack queue status board (post or update one channel message)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from messages import format_queue_status
from queue_meta import build_meta_map
from queue_ops import read_queue


def _queue_data_dir() -> Path:
    return Path(os.environ.get("MERGE_QUEUE_DIR", Path(__file__).resolve().parent))


def _count_nonempty_lines(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text().splitlines() if line.strip() and not line.strip().startswith("#"))


def build_queue_status_text(
    *,
    finished_url: str = "",
    finished_label: str = "done",
) -> str:
    base = _queue_data_dir()
    queue_file = Path(os.environ.get("PR_QUEUE_FILE", base / "prs.txt"))
    processing_file = Path(os.environ.get("PR_PROCESSING_FILE", base / "processing.txt"))
    failed_file = Path(os.environ.get("PR_FAILED_FILE", base / "prs-failed.txt"))
    skipped_file = Path(os.environ.get("PR_SKIPPED_FILE", base / "prs-skipped.txt"))
    threads_file = Path(os.environ.get("PR_THREADS_FILE", base / "prs-threads.json"))

    queue = read_queue(queue_file)
    processing = processing_file.read_text().strip() if processing_file.exists() else ""
    urls = list(queue)
    if processing:
        urls.append(processing)
    if finished_url:
        urls.append(finished_url)
    meta = build_meta_map(threads_file, urls)

    return format_queue_status(
        queue,
        _count_nonempty_lines(failed_file),
        _count_nonempty_lines(skipped_file),
        processing,
        finished_url=finished_url,
        finished_label=finished_label,
        meta=meta,
    )


def load_status_anchor(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def save_status_anchor(path: Path, channel_id: str, message_ts: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"channel_id": channel_id, "message_ts": message_ts}, indent=2) + "\n"
    )


def _create_slack_client(token: str):
    from slack_sdk import WebClient

    return WebClient(token=token)


def refresh_queue_status(
    *,
    channel_id: str,
    anchor_file: Path,
    finished_url: str = "",
    finished_label: str = "done",
    token: str | None = None,
) -> bool:
    channel_id = (channel_id or "").strip()
    if not channel_id:
        print("WARN: SLACK_CHANNEL_ID unset — skipping queue status update", file=sys.stderr)
        return False

    bot_token = token or os.environ.get("SLACK_BOT_TOKEN", "")
    if not bot_token:
        print("WARN: SLACK_BOT_TOKEN unset — skipping queue status update", file=sys.stderr)
        return False

    text = build_queue_status_text(finished_url=finished_url, finished_label=finished_label)
    client = _create_slack_client(bot_token)
    anchor = load_status_anchor(anchor_file)

    if (
        anchor.get("channel_id") == channel_id
        and anchor.get("message_ts")
    ):
        try:
            client.chat_update(
                channel=channel_id,
                ts=anchor["message_ts"],
                text=text,
            )
            return True
        except Exception as exc:
            print(f"WARN: chat_update failed, posting new status message: {exc}", file=sys.stderr)

    try:
        result = client.chat_postMessage(channel=channel_id, text=text)
        message_ts = result.get("ts", "")
        if message_ts:
            save_status_anchor(anchor_file, channel_id, message_ts)
        return True
    except Exception as exc:
        print(f"WARN: chat_postMessage failed for queue status: {exc}", file=sys.stderr)
        return False


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] != "refresh":
        sys.exit(2)

    finished_url = sys.argv[2] if len(sys.argv) > 2 else ""
    finished_label = sys.argv[3] if len(sys.argv) > 3 else "done"
    channel_id = os.environ.get("SLACK_CHANNEL_ID", "")
    anchor_file = Path(
        os.environ.get("QUEUE_STATUS_FILE", _queue_data_dir() / "queue-status.json")
    )
    ok = refresh_queue_status(
        channel_id=channel_id,
        anchor_file=anchor_file,
        finished_url=finished_url,
        finished_label=finished_label,
    )
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
