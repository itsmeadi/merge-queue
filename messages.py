#!/usr/bin/env python3
"""Cute Slack message formatters for the merge queue bot and worker."""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime

PR_NUMBER_RE = re.compile(r"/pull/(\d+)")


def pr_label(url: str) -> str:
    match = PR_NUMBER_RE.search(url)
    return f"#{match.group(1)}" if match else url


def pr_link(url: str) -> str:
    return f"<{url}|{pr_label(url)}>"


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
        (("already merged",), "already merged"),
        (("already closed",), "already closed"),
    ]
    for needles, label in rules:
        if any(n in text for n in needles):
            return label
    return raw.strip()


def reason_emoji(outcome: str, reason: str = "") -> str:
    text = (reason or "").lower()
    if outcome == "merged" or "merged" in text:
        return ":white_check_mark:"
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
    detail = cute_reason(reason, outcome)
    when = format_time(timestamp)
    return f"• {emoji} {pr_link(url)} · {detail} · {when}"


def format_history_lines(entries: list[tuple[datetime, str, str, str]], n: int) -> str:
    lines = [f":scroll: *Queue diary* · last {n}"]
    if not entries:
        lines.append("_Nothing finished yet — queue is waiting for its first story_")
        return "\n".join(lines)

    for timestamp, url, outcome, reason in entries:
        lines.append(format_history_entry(timestamp, url, outcome, reason))
    return "\n".join(lines)


def _format_queue_lines(
    queue: list[str],
    processing_url: str = "",
    highlight_url: str = "",
) -> list[str]:
    lines: list[str] = []
    position = 0

    if processing_url:
        position += 1
        lines.append(f"{position}. :loading: {pr_link(processing_url)} · processing")

    for url in queue:
        if url == processing_url:
            continue
        position += 1
        if url == highlight_url:
            lines.append(f"{position}. :point_right: {pr_link(url)} · you")
        else:
            lines.append(f"{position}. {pr_link(url)}")

    return lines


def _finished_emoji(label: str) -> str:
    text = (label or "done").strip().lower()
    if text in ("done", "removed", "merged"):
        return ":white_check_mark:"
    if text == "skipped":
        return ":rabbit2:"
    if text == "failed":
        return ":x:"
    return ":white_check_mark:"


def format_queue_status(
    queue: list[str],
    failed_count: int,
    skipped_count: int,
    processing_url: str = "",
    finished_url: str = "",
    finished_label: str = "done",
) -> str:
    lines = [":hourglass_flowing_sand: *Who's in line?*"]
    excluded = {finished_url} if finished_url else set()
    active_processing = processing_url if processing_url not in excluded else ""

    entries: list[str] = []
    if finished_url:
        entries.append(
            f"{_finished_emoji(finished_label)} {pr_link(finished_url)} · {finished_label}"
        )
    if active_processing:
        entries.append(f":loading: {pr_link(active_processing)} · processing")
    for url in queue:
        if url in excluded or url == active_processing:
            continue
        entries.append(f"{pr_link(url)}")

    if not entries:
        lines.append("_Queue is empty — all quiet_")
    else:
        for index, entry in enumerate(entries, start=1):
            lines.append(f"{index}. {entry}")

    lines.append(f"\n_{len(queue)} waiting · {failed_count} failed · {skipped_count} skipped_")
    return "\n".join(lines)


def format_queued(
    url: str,
    position: int,
    added: bool,
    queue: list[str] | None = None,
    processing_url: str = "",
    title: str = "",
) -> str:
    link = pr_link(url)
    queue = queue or []
    total = len(queue)
    title_suffix = f" — {title.strip()}" if title and title.strip() else ""

    if not added:
        return f":information_source: {link} · already queued (spot {position})"

    if total <= 1 and not processing_url:
        if title_suffix:
            return f":inbox_tray: *Queued* · {link}{title_suffix}\nspot {position} · up next"
        return f":inbox_tray: *Queued* · {link} · up next"

    headline = f":inbox_tray: *Queued* · {link}{title_suffix} · spot {position} of {total}"
    lines = [headline, "", "*Queue:*"]
    lines.extend(_format_queue_lines(queue, processing_url, highlight_url=url))
    return "\n".join(lines)


def _format_ci_summary_lines(summary: dict | None) -> list[str]:
    if not summary:
        return []

    lines: list[str] = []
    failed_checks = summary.get("failed_checks") or []
    if failed_checks:
        shown = ", ".join(str(name) for name in failed_checks[:5])
        lines.append(f"Failed: {shown}")

    excerpt = (summary.get("excerpt") or "").strip()
    if excerpt:
        lines.append(f"```{excerpt[:400]}```")
    return lines


# --- Worker notification formatters ---

def format_skip(url: str, reason: str) -> str:
    emoji = reason_emoji("skipped", reason)
    return f"{emoji} {pr_link(url)} · {cute_reason(reason, 'skipped')}"


def format_merged(url: str) -> str:
    return f":white_check_mark: {pr_link(url)} · merged and done!"


def format_preflight_reject(url: str, reason: str) -> str:
    return f":no_entry: {pr_link(url)} · not queued · {cute_reason(reason)}"


def format_reaction_no_pr() -> str:
    return ":ghost: couldn't find a PR link in this message — use `/merge 14699`"


def format_removed(url: str, position: int) -> str:
    return f":wastebasket: {pr_link(url)} · removed from queue (was spot {position})"


def format_remove_not_found(url: str) -> str:
    return f":information_source: {pr_link(url)} · not in queue"


def format_remove_processing(url: str) -> str:
    return f":hourglass_flowing_sand: {pr_link(url)} · already merging — can't remove"


def format_failed(url: str, reason: str) -> str:
    emoji = reason_emoji("failed", reason)
    return f"{emoji} {pr_link(url)} · {cute_reason(reason, 'failed')}"


def format_processing(url: str) -> str:
    return f":hourglass_flowing_sand: {pr_link(url)} · syncing with base..."


def format_already_done(url: str, state: str) -> str:
    return f":information_source: {pr_link(url)} · already {state.lower()}, removed from queue"


def format_ci_rerun(
    url: str,
    attempt: int,
    max_attempts: int,
    summary: dict | None = None,
) -> str:
    lines = [
        f":arrows_counterclockwise: {pr_link(url)} · CI failed, retry {attempt}/{max_attempts}",
    ]
    lines.extend(_format_ci_summary_lines(summary))
    lines.append("Rerunning failed jobs...")
    return "\n".join(lines)


def format_ci_failed(
    url: str,
    max_attempts: int,
    summary: dict | None = None,
) -> str:
    lines = [
        f":x: {pr_link(url)} · CI failed after {max_attempts} retries — giving up",
    ]
    lines.extend(_format_ci_summary_lines(summary))
    return "\n".join(lines)


def format_worker_started(poll_interval: int) -> str:
    return f":robot_face: Merge bot is awake · checking every {poll_interval}s"


def format_dm_update(body: str) -> str:
    text = (body or "").strip()
    if not text:
        return "Merge queue update"
    return f"*Merge queue update*\n{text}"


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(2)

    cmd = sys.argv[1]
    args = sys.argv[2:]

    summary: dict | None = None
    if "--summary-stdin" in args:
        idx = args.index("--summary-stdin")
        args = args[:idx]
        try:
            summary = json.load(sys.stdin)
        except json.JSONDecodeError:
            summary = None

    messages = {
        "skip": lambda: format_skip(args[0], args[1] if len(args) > 1 else ""),
        "merged": lambda: format_merged(args[0]),
        "failed": lambda: format_failed(args[0], args[1] if len(args) > 1 else ""),
        "processing": lambda: format_processing(args[0]),
        "already_done": lambda: format_already_done(args[0], args[1] if len(args) > 1 else "done"),
        "ci_rerun": lambda: format_ci_rerun(args[0], int(args[1]), int(args[2]), summary),
        "ci_failed": lambda: format_ci_failed(args[0], int(args[1]), summary),
        "worker_started": lambda: format_worker_started(int(args[0])),
    }

    if cmd not in messages:
        sys.exit(2)

    print(messages[cmd]())


if __name__ == "__main__":
    main()
