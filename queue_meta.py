#!/usr/bin/env python3
"""Persist Slack thread anchors for queued PRs (shared by bot and worker)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def load_threads(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def save_threads(path: Path, data: dict[str, dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def save_thread(path: Path, url: str, channel: str, thread_ts: str) -> None:
    data = load_threads(path)
    data[url] = {"channel": channel, "thread_ts": thread_ts}
    save_threads(path, data)


def clear_thread(path: Path, url: str) -> None:
    data = load_threads(path)
    if url not in data:
        return
    del data[url]
    save_threads(path, data)


def lookup_thread(path: Path, url: str) -> tuple[str, str] | None:
    entry = load_threads(path).get(url)
    if not entry:
        return None
    channel = entry.get("channel", "")
    thread_ts = entry.get("thread_ts", "")
    if channel and thread_ts:
        return channel, thread_ts
    return None


def main() -> None:
    if len(sys.argv) < 3:
        sys.exit(2)

    cmd = sys.argv[1]
    path = Path(sys.argv[2])

    if cmd == "lookup":
        if len(sys.argv) != 4:
            sys.exit(2)
        result = lookup_thread(path, sys.argv[3])
        if result:
            print(result[0], result[1])
        sys.exit(0)

    if cmd == "clear":
        if len(sys.argv) != 4:
            sys.exit(2)
        clear_thread(path, sys.argv[3])
        sys.exit(0)

    sys.exit(2)


if __name__ == "__main__":
    main()
