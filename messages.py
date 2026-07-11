#!/usr/bin/env python3
"""Slack message formatters for the merge queue bot and worker."""

from __future__ import annotations

import re
import sys
from datetime import datetime

PR_NUMBER_RE = re.compile(r"/pull/(\d+)")


def pr_link(url: str) -> str:
    """Slack link: <url|#12345>"""
    match = PR_NUMBER_RE.search(url)
    if match:
        return f"<{url}|#{match.group(1)}>"
    return url


def format_time(dt: datetime) -> str:
    hour = dt.strftime("%I").lstrip("0") or "12"
    minute = dt.strftime("%M")
    ampm = dt.strftime("%p").lower()
    return f"{dt.strftime('%b')} {dt.day}, {hour}:{minute}{ampm}"


def friendly_reason(raw: str, outcome: str = "") -> str:
    text = (raw or "").strip().lower()
    if not text:
        if outcome == "merged":
            return "merged"
        return outcome or "unknown"

    rules = [
        (("merge conflict", "conflicting"), "merge conflict"),
        (("missing approval", "review_required"), "missing approval"),
        (("changes requested",), "changes requested"),
        (("merge blocked", "blocked"), "merge blocked"),
        (("malformed url",), "malformed URL"),
        (("gh pr view", "could not fetch"), "PR not found"),
        (("ci failed and could not rerun",), "CI failed, could not rerun"),
        (("ci failed",), "CI failed"),
        (("merge failed",), "merge failed"),
        (("merged",), "merged"),
    ]
    for needles, label in rules:
        if any(n in text for n in needles):
            return label
    return raw.strip()


def format_history_entry(
    timestamp: datetime,
    url: str,
    outcome: str,
    reason: str = "",
) -> str:
    detail = friendly_reason(reason, outcome)
    when = format_time(timestamp)
    return f"• {pr_link(url)} · {detail} · {when}"


def format_history_lines(entries: list[tuple[datetime, str, str, str]], n: int) -> str:
    lines = [f"*Merge queue history* (last {n})"]
    if not entries:
        lines.append("_No completed PRs yet_")
        return "\n".join(lines)

    for timestamp, url, outcome, reason in entries:
        lines.append(format_history_entry(timestamp, url, outcome, reason))
    return "\n".join(lines)


def format_queue_status(queue: list[str], failed_count: int, skipped_count: int) -> str:
    lines = ["*Merge queue*"]
    if not queue:
        lines.append("_Queue is empty_")
    else:
        for i, url in enumerate(queue):
            if i == 0:
                lines.append(f"{pr_link(url)} · up next")
            else:
                lines.append(f"{i + 1}. {pr_link(url)}")
    lines.append(f"\n_{len(queue)} queued · {failed_count} failed · {skipped_count} skipped_")
    return "\n".join(lines)


def format_queued(url: str, position: int, added: bool) -> str:
    link = pr_link(url)
    if added:
        if position == 1:
            return f"Queued {link} · up next"
        return f"Queued {link} · position {position}"
    return f"Already queued {link} · position {position}"


# --- Worker notification formatters ---

def format_skip(url: str, reason: str) -> str:
    return f"Skipped {pr_link(url)} · {friendly_reason(reason, 'skipped')}"


def format_merged(url: str) -> str:
    return f"Merged {pr_link(url)}"


def format_failed(url: str, reason: str) -> str:
    return f"Failed {pr_link(url)} · {friendly_reason(reason, 'failed')}"


def format_processing(url: str) -> str:
    return f"Processing {pr_link(url)} · syncing with base..."


def format_already_done(url: str, state: str) -> str:
    return f"{pr_link(url)} · already {state.lower()}, removed from queue"


def format_ci_rerun(url: str, attempt: int, max_attempts: int) -> str:
    return f"{pr_link(url)} · CI failed, retry {attempt}/{max_attempts}..."


def format_worker_started(poll_interval: int) -> str:
    return f"Merge queue worker started · polling every {poll_interval}s"


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(2)

    cmd = sys.argv[1]
    args = sys.argv[2:]

    messages = {
        "skip": lambda: format_skip(args[0], args[1] if len(args) > 1 else ""),
        "merged": lambda: format_merged(args[0]),
        "failed": lambda: format_failed(args[0], args[1] if len(args) > 1 else ""),
        "processing": lambda: format_processing(args[0]),
        "already_done": lambda: format_already_done(args[0], args[1] if len(args) > 1 else "done"),
        "ci_rerun": lambda: format_ci_rerun(args[0], int(args[1]), int(args[2])),
        "worker_started": lambda: format_worker_started(int(args[0])),
    }

    if cmd not in messages:
        sys.exit(2)

    print(messages[cmd]())


if __name__ == "__main__":
    main()
