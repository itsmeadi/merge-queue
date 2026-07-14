#!/usr/bin/env python3
"""Queue a PR for the merge bot (CLI for agents, SSH, or manual use)."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from pr_preflight import check_pr_preflight
from queue_meta import save_pr_meta

DEFAULT_REPO = os.environ.get("DEFAULT_REPO", "GetStream/chat")
INSTALL_DIR = Path(__file__).resolve().parent
QUEUE_DATA_DIR = Path(os.environ.get("MERGE_QUEUE_DIR", str(INSTALL_DIR)))
PR_QUEUE_FILE = Path(os.environ.get("PR_QUEUE_FILE", QUEUE_DATA_DIR / "prs.txt"))
PR_THREADS_FILE = Path(os.environ.get("PR_THREADS_FILE", QUEUE_DATA_DIR / "prs-threads.json"))

PR_URL_RE = re.compile(r"github\.com/[^/]+/[^/]+/pull/\d+")


def ensure_queue_files() -> None:
    PR_QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
    PR_QUEUE_FILE.touch(exist_ok=True)


def read_queue() -> list[str]:
    ensure_queue_files()
    lines = PR_QUEUE_FILE.read_text().splitlines()
    return [line.strip() for line in lines if line.strip() and not line.strip().startswith("#")]


def normalize_pr_input(text: str) -> str | None:
    raw = (text or "").strip()
    if not raw:
        return None
    if raw.isdigit():
        return f"https://github.com/{DEFAULT_REPO}/pull/{raw}"
    url = raw if raw.startswith(("http://", "https://")) else f"https://{raw}"
    return url if PR_URL_RE.search(url) else None


def append_to_queue(url: str) -> tuple[bool, int]:
    queue = read_queue()
    if url in queue:
        return False, queue.index(url) + 1
    with PR_QUEUE_FILE.open("a") as f:
        f.write(f"{url}\n")
    return True, len(queue) + 1


def enqueue(text: str) -> dict[str, object]:
    url = normalize_pr_input(text)
    if not url:
        return {"ok": False, "reason": "malformed url", "url": text}

    preflight = check_pr_preflight(url)
    if not preflight.ok:
        return {
            "ok": False,
            "reason": preflight.reason,
            "url": url,
            "title": preflight.title,
        }

    added, position = append_to_queue(url)
    save_pr_meta(PR_THREADS_FILE, url, preflight.title, preflight.author)
    return {
        "ok": True,
        "url": url,
        "title": preflight.title,
        "author": preflight.author,
        "queued": added,
        "position": position,
        "queue_length": len(read_queue()),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Queue a PR for the merge bot")
    parser.add_argument("pr", help="PR number or GitHub pull URL")
    parser.add_argument("--json", action="store_true", help="Print JSON result")
    args = parser.parse_args()

    result = enqueue(args.pr)
    if args.json:
        print(json.dumps(result))
    elif not result.get("ok"):
        print(f"not queued: {result.get('reason', 'unknown')}", file=sys.stderr)
    elif result.get("queued"):
        print(f"queued at position {result['position']}: {result['url']}")
    else:
        print(f"already queued at position {result['position']}: {result['url']}")

    sys.exit(0 if result.get("ok") else 1)


if __name__ == "__main__":
    main()
