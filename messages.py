#!/usr/bin/env python3
"""Cute Slack message formatters for the merge queue bot and worker."""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

PR_NUMBER_RE = re.compile(r"/pull/(\d+)")
PR_TITLE_MAX_LEN = int(os.environ.get("PR_TITLE_MAX_LEN", "50"))


def pr_label(url: str) -> str:
    match = PR_NUMBER_RE.search(url)
    return f"#{match.group(1)}" if match else url


def pr_link(url: str) -> str:
    return f"<{url}|{pr_label(url)}>"


def truncate_title(title: str, max_len: int = PR_TITLE_MAX_LEN) -> str:
    text = (title or "").strip()
    if not text:
        return ""
    if len(text) <= max_len:
        return text
    if max_len <= 1:
        return "…"
    return text[: max_len - 1].rstrip() + "…"


def format_pr_descriptor(url: str, title: str = "", author: str = "") -> str:
    parts = [pr_link(url)]
    author_text = (author or "").strip()
    if author_text:
        login = author_text if author_text.startswith("@") else f"@{author_text}"
        parts.append(login)
    title_text = truncate_title(title)
    if title_text:
        parts.append(title_text)
    return " · ".join(parts)


def _descriptor(
    url: str,
    meta: dict[str, tuple[str, str]] | None = None,
    title: str = "",
    author: str = "",
) -> str:
    if meta and url in meta:
        stored_title, stored_author = meta[url]
        title = title or stored_title
        author = author or stored_author
    return format_pr_descriptor(url, title, author)


def format_time(dt: datetime) -> str:
    hour = dt.strftime("%I").lstrip("0") or "12"
    minute = dt.strftime("%M")
    ampm = dt.strftime("%p").lower()
    return f"{dt.strftime('%b')} {dt.day}, {hour}:{minute}{ampm}"


def cute_reason(raw: str, outcome: str = "") -> str:
    text = (raw or "").strip().lower()
    if not text:
        if outcome == "merged":
            return "merged"
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
        (("merged",), "merged"),
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
    title: str = "",
    author: str = "",
) -> str:
    emoji = reason_emoji(outcome, reason)
    detail = cute_reason(reason, outcome)
    when = format_time(timestamp)
    descriptor = _descriptor(url, title=title, author=author)
    return f"• {emoji} {descriptor} · {detail} · {when}"


def format_history_lines(
    entries: list[tuple[datetime, str, str, str]],
    n: int,
    meta: dict[str, tuple[str, str]] | None = None,
) -> str:
    lines = [f":scroll: *Queue diary* · last {n}"]
    if not entries:
        lines.append("_Nothing finished yet — queue is waiting for its first story_")
        return "\n".join(lines)

    for timestamp, url, outcome, reason in entries:
        title, author = meta.get(url, ("", "")) if meta else ("", "")
        lines.append(format_history_entry(timestamp, url, outcome, reason, title, author))
    return "\n".join(lines)


def _format_queue_lines(
    queue: list[str],
    processing_url: str = "",
    highlight_url: str = "",
    meta: dict[str, tuple[str, str]] | None = None,
) -> list[str]:
    lines: list[str] = []
    position = 0

    if processing_url:
        position += 1
        descriptor = _descriptor(processing_url, meta)
        lines.append(f"{position}. :loading: {descriptor} · processing")

    for url in queue:
        if url == processing_url:
            continue
        position += 1
        descriptor = _descriptor(url, meta)
        if url == highlight_url:
            lines.append(f"{position}. :point_right: {descriptor} · you")
        else:
            lines.append(f"{position}. {descriptor}")

    return lines


def _finished_emoji(label: str) -> str:
    text = (label or "done").strip().lower()
    if text in ("done", "removed", "merged", "closed"):
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
    meta: dict[str, tuple[str, str]] | None = None,
) -> str:
    lines = [":hourglass_flowing_sand: *Who's in line?*"]
    excluded = {finished_url} if finished_url else set()
    active_processing = processing_url if processing_url not in excluded else ""

    entries: list[str] = []
    if finished_url:
        descriptor = _descriptor(finished_url, meta)
        entries.append(
            f"{_finished_emoji(finished_label)} {descriptor} · {finished_label}"
        )
    if active_processing:
        descriptor = _descriptor(active_processing, meta)
        entries.append(f":loading: {descriptor} · processing")
    for url in queue:
        if url in excluded or url == active_processing:
            continue
        entries.append(_descriptor(url, meta))

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
    author: str = "",
    meta: dict[str, tuple[str, str]] | None = None,
) -> str:
    queue = queue or []
    total = len(queue)
    descriptor = _descriptor(url, meta, title=title, author=author)
    queue_meta = dict(meta or {})
    if title or author:
        queue_meta[url] = (title, author)

    if not added:
        return f":information_source: {descriptor} · already queued (spot {position})"

    if total <= 1 and not processing_url:
        return f":inbox_tray: *Queued* · {descriptor}\nspot {position} · up next"

    headline = f":inbox_tray: *Queued* · {descriptor} · spot {position} of {total}"
    lines = [headline, "", "*Queue:*"]
    lines.extend(_format_queue_lines(queue, processing_url, highlight_url=url, meta=queue_meta))
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


def _worker_line(
    url: str,
    suffix: str,
    emoji: str = "",
    title: str = "",
    author: str = "",
) -> str:
    descriptor = format_pr_descriptor(url, title, author)
    if emoji:
        return f"{emoji} {descriptor} · {suffix}"
    return f"{descriptor} · {suffix}"


# --- Worker notification formatters ---

def format_skip(url: str, reason: str, title: str = "", author: str = "") -> str:
    emoji = reason_emoji("skipped", reason)
    return _worker_line(url, cute_reason(reason, "skipped"), emoji, title, author)


def format_merged(url: str, title: str = "", author: str = "") -> str:
    return _worker_line(url, "merged", ":white_check_mark:", title, author)


def format_preflight_reject(url: str, reason: str, title: str = "", author: str = "") -> str:
    descriptor = format_pr_descriptor(url, title, author)
    return f":no_entry: {descriptor} · not queued · {cute_reason(reason)}"


def format_reaction_no_pr() -> str:
    return ":ghost: couldn't find a PR link in this message — use `/merge 14699`"


def format_removed(url: str, position: int, title: str = "", author: str = "") -> str:
    return _worker_line(
        url,
        f"removed from queue (was spot {position})",
        ":wastebasket:",
        title,
        author,
    )


def format_remove_not_found(url: str, title: str = "", author: str = "") -> str:
    descriptor = format_pr_descriptor(url, title, author)
    return f":information_source: {descriptor} · not in queue"


def format_remove_processing(url: str, title: str = "", author: str = "") -> str:
    descriptor = format_pr_descriptor(url, title, author)
    return f":hourglass_flowing_sand: {descriptor} · already merging — can't remove"


def format_failed(url: str, reason: str, title: str = "", author: str = "") -> str:
    emoji = reason_emoji("failed", reason)
    return _worker_line(url, cute_reason(reason, "failed"), emoji, title, author)


def format_processing(url: str, title: str = "", author: str = "") -> str:
    return _worker_line(url, "syncing with base...", ":hourglass_flowing_sand:", title, author)


def format_already_done(url: str, state: str, title: str = "", author: str = "") -> str:
    descriptor = format_pr_descriptor(url, title, author)
    return f":information_source: {descriptor} · already {state.lower()}, removed from queue"


def format_ci_rerun(
    url: str,
    attempt: int,
    max_attempts: int,
    summary: dict | None = None,
    title: str = "",
    author: str = "",
) -> str:
    lines = [
        _worker_line(
            url,
            f"CI failed, retry {attempt}/{max_attempts}",
            ":arrows_counterclockwise:",
            title,
            author,
        ),
    ]
    lines.extend(_format_ci_summary_lines(summary))
    lines.append("Rerunning failed jobs...")
    return "\n".join(lines)


def format_ci_failed(
    url: str,
    max_attempts: int,
    summary: dict | None = None,
    title: str = "",
    author: str = "",
) -> str:
    lines = [
        _worker_line(
            url,
            f"CI failed after {max_attempts} retries — giving up",
            ":x:",
            title,
            author,
        ),
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


def _lookup_meta_for_url(url: str) -> tuple[str, str]:
    threads_file = os.environ.get("PR_THREADS_FILE", "")
    if not threads_file:
        return "", ""
    from queue_meta import lookup_pr_meta

    return lookup_pr_meta(Path(threads_file), url)


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

    url = args[0] if args else ""
    title, author = _lookup_meta_for_url(url) if url else ("", "")

    messages = {
        "skip": lambda: format_skip(url, args[1] if len(args) > 1 else "", title, author),
        "merged": lambda: format_merged(url, title, author),
        "failed": lambda: format_failed(url, args[1] if len(args) > 1 else "", title, author),
        "processing": lambda: format_processing(url, title, author),
        "already_done": lambda: format_already_done(
            url,
            args[1] if len(args) > 1 else "done",
            title,
            author,
        ),
        "ci_rerun": lambda: format_ci_rerun(
            url,
            int(args[1]),
            int(args[2]),
            summary,
            title,
            author,
        ),
        "ci_failed": lambda: format_ci_failed(url, int(args[1]), summary, title, author),
        "worker_started": lambda: format_worker_started(int(args[0])),
    }

    if cmd not in messages:
        sys.exit(2)

    print(messages[cmd]())


if __name__ == "__main__":
    main()
