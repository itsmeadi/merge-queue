#!/usr/bin/env python3
"""Cute Slack message formatters for the merge queue bot and worker."""

from __future__ import annotations

import re
import sys
from datetime import datetime

PR_NUMBER_RE = re.compile(r"/pull/(\d+)")


def pr_label(url: str) -> str:
    match = PR_NUMBER_RE.search(url)
    return f"#{match.group(1)}" if match else url


def format_time(dt: datetime) -> str:
    hour = dt.strftime("%I").lstrip("0") or "12"
    minute = dt.strftime("%M")
    ampm = dt.strftime("%p").lower()
    return f"{dt.strftime('%b')} {dt.day}, {hour}:{minute}{ampm}"


def cute_reason(raw: str, outcome: str = "") -> str:
    text = (raw or "").strip().lower()
    if not text:
        if outcome == "merged":
            return "merged and done"
        return outcome or "unknown"

    rules = [
        (("merge conflict", "conflicting"), "bumped into a conflict"),
        (("missing approval", "review_required"), "still needs a thumbs-up"),
        (("changes requested",), "reviewer wants changes"),
        (("merge blocked", "blocked"), "merge is blocked"),
        (("malformed url",), "that URL looks funny"),
        (("gh pr view", "could not fetch"), "couldn't find that PR"),
        (("ci failed and could not rerun",), "CI failed and couldn't rerun"),
        (("ci failed",), "CI said nope"),
        (("merge failed",), "merge didn't work out"),
        (("merged",), "merged and done"),
    ]
    for needles, label in rules:
        if any(n in text for n in needles):
            return label
    return raw.strip()


def reason_emoji(outcome: str, reason: str = "") -> str:
    text = (reason or "").lower()
    if outcome == "merged" or "merged" in text:
        return ":tada:"
    if any(x in text for x in ("merge conflict", "conflicting", "conflict")):
        return ":collision:"
    if any(x in text for x in ("missing approval", "review_required")):
        return ":wave:"
    if "changes requested" in text:
        return ":memo:"
    if "ci failed" in text or "ci said" in text:
        return ":repeat:"
    if any(x in text for x in ("malformed", "gh pr view", "could not fetch", "couldn't find")):
        return ":ghost:"
    if outcome == "skipped":
        return ":rabbit2:"
    if outcome == "failed":
        return ":sweat_smile:"
    return ":grey_question:"


def format_history_entry(
    timestamp: datetime,
    url: str,
    outcome: str,
    reason: str = "",
) -> str:
    emoji = reason_emoji(outcome, reason)
    label = pr_label(url)
    detail = cute_reason(reason, outcome)
    when = format_time(timestamp)
    return f"• {emoji} {label} · {detail} · {when}"


def format_history_lines(entries: list[tuple[datetime, str, str, str]], n: int) -> str:
    lines = [f":bunny: *Queue diary* · last {n}"]
    if not entries:
        lines.append("_Nothing finished yet — queue is waiting for its first story_")
        return "\n".join(lines)

    for timestamp, url, outcome, reason in entries:
        lines.append(format_history_entry(timestamp, url, outcome, reason))
    return "\n".join(lines)


def format_queue_status(queue: list[str], failed_count: int, skipped_count: int) -> str:
    lines = [":clipboard: *Who's in line?*"]
    if not queue:
        lines.append("_Queue is empty — all quiet_")
    else:
        for i, url in enumerate(queue):
            label = pr_label(url)
            if i == 0:
                lines.append(f":rabbit2: {label} · up next!")
            else:
                lines.append(f"   {i + 1}. {label}")
    lines.append(f"\n_{len(queue)} waiting · {failed_count} failed · {skipped_count} skipped_")
    return "\n".join(lines)


def format_queued(url: str, position: int, added: bool) -> str:
    label = pr_label(url)
    if added:
        if position == 1:
            return f":rabbit2: {label} · you're up next!"
        return f":rabbit2: {label} · spot {position} in line"
    return f":rabbit2: {label} · already in line (spot {position})"


# --- Worker notification formatters ---

def format_skip(url: str, reason: str) -> str:
    emoji = reason_emoji("skipped", reason)
    return f"{emoji} {pr_label(url)} · {cute_reason(reason, 'skipped')}"


def format_merged(url: str) -> str:
    return f":tada: {pr_label(url)} · merged and done!"


def format_failed(url: str, reason: str) -> str:
    emoji = reason_emoji("failed", reason)
    return f"{emoji} {pr_label(url)} · {cute_reason(reason, 'failed')}"


def format_processing(url: str) -> str:
    return f":hourglass_flowing_sand: {pr_label(url)} · syncing with base..."


def format_already_done(url: str, state: str) -> str:
    return f":information_source: {pr_label(url)} · already {state.lower()}, removed from queue"


def format_ci_rerun(url: str, attempt: int, max_attempts: int) -> str:
    return f":repeat: {pr_label(url)} · CI hiccup, retry {attempt}/{max_attempts}..."


def format_worker_started(poll_interval: int) -> str:
    return f":robot_face: Merge bot is awake · checking every {poll_interval}s"


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
