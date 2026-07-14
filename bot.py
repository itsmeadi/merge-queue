#!/usr/bin/env python3
"""Slack merge-queue bot (Socket Mode). Manages prs.txt and runs worker.sh."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from slack_sdk.errors import SlackApiError

from messages import (
    format_history_lines,
    format_preflight_reject,
    format_queue_status,
    format_queued,
    format_reaction_no_pr,
    pr_label,
)
from pr_extract import extract_pr_urls
from pr_preflight import check_pr_preflight
from queue_meta import save_thread

INSTALL_DIR = Path(__file__).resolve().parent
QUEUE_DATA_DIR = Path(os.environ.get("MERGE_QUEUE_DIR", str(INSTALL_DIR)))
PR_QUEUE_FILE = Path(os.environ.get("PR_QUEUE_FILE", QUEUE_DATA_DIR / "prs.txt"))
PR_FAILED_FILE = Path(os.environ.get("PR_FAILED_FILE", QUEUE_DATA_DIR / "prs-failed.txt"))
PR_SKIPPED_FILE = Path(os.environ.get("PR_SKIPPED_FILE", QUEUE_DATA_DIR / "prs-skipped.txt"))
PR_MERGED_FILE = Path(os.environ.get("PR_MERGED_FILE", QUEUE_DATA_DIR / "prs-merged.txt"))
PR_PROCESSING_FILE = Path(os.environ.get("PR_PROCESSING_FILE", QUEUE_DATA_DIR / "processing.txt"))
PR_THREADS_FILE = Path(os.environ.get("PR_THREADS_FILE", QUEUE_DATA_DIR / "prs-threads.json"))
DEFAULT_REPO = os.environ.get("DEFAULT_REPO", "GetStream/chat")
SLACK_CHANNEL_ID = os.environ.get("SLACK_CHANNEL_ID", "")
START_WORKER = os.environ.get("START_WORKER", "true").lower() != "false"
DEPLOY_ENABLED = os.environ.get("DEPLOY_ENABLED", "false").lower() == "true"
DEPLOY_ALLOWED_USER_IDS = {
    uid.strip()
    for uid in os.environ.get("DEPLOY_ALLOWED_USER_IDS", "").split(",")
    if uid.strip()
}
WORKER_PATH = INSTALL_DIR / "worker.sh"
DEPLOY_PATH = INSTALL_DIR / "deploy.sh"
HISTORY_DEFAULT = 5
HISTORY_MAX = 50
MERGE_REACTION_EMOJI = os.environ.get("MERGE_REACTION_EMOJI", "merge_bot")
MERGE_REACTION_ACK = os.environ.get("MERGE_REACTION_ACK", "true").lower() != "false"
REACTION_ACK_OK = "white_check_mark"
REACTION_ACK_FAIL = "x"
REACTION_ACK_NO_PR = "ghost"

PR_URL_RE = re.compile(r"github\.com/[^/]+/[^/]+/pull/\d+")
HISTORY_LINE_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) (\S+)(?: # (.+))?$"
)


@dataclass(frozen=True)
class HistoryEntry:
    timestamp: datetime
    url: str
    outcome: str
    reason: str


def ensure_queue_files() -> None:
    for path in (PR_QUEUE_FILE, PR_FAILED_FILE, PR_SKIPPED_FILE, PR_MERGED_FILE):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)


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


def parse_history_line(line: str, outcome: str) -> HistoryEntry | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    match = HISTORY_LINE_RE.match(stripped)
    if not match:
        return None
    ts_raw, url, reason = match.group(1), match.group(2), match.group(3) or ""
    try:
        timestamp = datetime.strptime(ts_raw, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return HistoryEntry(timestamp=timestamp, url=url, outcome=outcome, reason=reason.strip())


def read_history_file(path: Path, outcome: str) -> list[HistoryEntry]:
    if not path.exists():
        return []
    entries: list[HistoryEntry] = []
    for line in path.read_text().splitlines():
        entry = parse_history_line(line, outcome)
        if entry:
            entries.append(entry)
    return entries


def read_recent_history(n: int) -> list[HistoryEntry]:
    entries: list[HistoryEntry] = []
    entries.extend(read_history_file(PR_MERGED_FILE, "merged"))
    entries.extend(read_history_file(PR_SKIPPED_FILE, "skipped"))
    entries.extend(read_history_file(PR_FAILED_FILE, "failed"))
    entries.sort(key=lambda e: e.timestamp, reverse=True)
    return entries[:n]


def build_history_message(n: int) -> str:
    entries = read_recent_history(n)
    rows = [(e.timestamp, e.url, e.outcome, e.reason) for e in entries]
    return format_history_lines(rows, n)


def build_queue_status_message() -> str:
    queue = read_queue()
    failed = [line for line in PR_FAILED_FILE.read_text().splitlines() if line.strip()] if PR_FAILED_FILE.exists() else []
    skipped = [line for line in PR_SKIPPED_FILE.read_text().splitlines() if line.strip()] if PR_SKIPPED_FILE.exists() else []
    return format_queue_status(queue, len(failed), len(skipped), read_processing())


def read_processing() -> str:
    if not PR_PROCESSING_FILE.exists():
        return ""
    return PR_PROCESSING_FILE.read_text().strip()


def capture_thread_ts(client: Any, channel_id: str, message: str, url: str) -> None:
    """Save thread anchor after respond() posts via the slash-command response_url."""
    try:
        auth = client.auth_test()
        bot_user_id = auth["user_id"]
    except SlackApiError as exc:
        print(f"WARN: auth_test failed, skipping thread capture: {exc}", file=sys.stderr)
        return

    headline = message.split("\n", 1)[0]
    pr_number = pr_label(url)
    time.sleep(0.5)

    try:
        result = client.conversations_history(channel=channel_id, limit=10)
    except SlackApiError as exc:
        print(f"WARN: could not read channel history for thread capture: {exc}", file=sys.stderr)
        return

    for msg in result.get("messages", []):
        if msg.get("user") != bot_user_id:
            continue
        text = msg.get("text", "")
        if headline in text or pr_number in text:
            thread_ts = msg.get("ts")
            if thread_ts:
                save_thread(PR_THREADS_FILE, url, channel_id, thread_ts)
            return


def handle_merge_command(
    respond: Callable[..., Any],
    client: Any,
    channel_id: str,
    text: str,
) -> None:
    url = normalize_pr_input(text)
    if not url:
        respond(
            response_type="ephemeral",
            text=(
                "Usage: `/merge 12345` or `/merge-queue 12345` "
                f"or `/merge https://github.com/{DEFAULT_REPO}/pull/12345`"
            ),
        )
        return

    preflight = check_pr_preflight(url)
    if not preflight.ok:
        respond(
            response_type="ephemeral",
            text=format_preflight_reject(url, preflight.reason),
        )
        return

    added, position = append_to_queue(url)
    queue = read_queue()
    message = format_queued(
        url,
        position,
        added,
        queue,
        read_processing(),
        preflight.title,
    )

    # respond() uses the slash-command response_url and works without chat:write
    # to the channel; chat.postMessage returns channel_not_found in many setups.
    respond(response_type="in_channel", text=message)

    def save_thread_async() -> None:
        capture_thread_ts(client, channel_id, message, url)

    threading.Thread(target=save_thread_async, daemon=True).start()


def slack_add_reaction(client: Any, channel_id: str, timestamp: str, emoji: str) -> None:
    try:
        client.reactions_add(channel=channel_id, timestamp=timestamp, name=emoji)
    except SlackApiError as exc:
        print(f"WARN: reactions_add failed ({emoji}): {exc}", file=sys.stderr)


def slack_post_thread(client: Any, channel_id: str, thread_ts: str, text: str) -> None:
    try:
        client.chat_postMessage(channel=channel_id, thread_ts=thread_ts, text=text)
    except SlackApiError as exc:
        print(f"WARN: chat_postMessage failed: {exc}", file=sys.stderr)


def fetch_message(client: Any, channel_id: str, message_ts: str) -> dict[str, Any] | None:
    try:
        result = client.conversations_history(
            channel=channel_id,
            latest=message_ts,
            limit=1,
            inclusive=True,
        )
    except SlackApiError as exc:
        print(f"WARN: could not fetch message for reaction: {exc}", file=sys.stderr)
        return None
    messages = result.get("messages") or []
    return messages[0] if messages else None


def handle_merge_reaction(client: Any, event: dict[str, Any]) -> None:
    if event.get("reaction") != MERGE_REACTION_EMOJI:
        return

    item = event.get("item") or {}
    if item.get("type") != "message":
        return

    channel_id = item.get("channel", "")
    message_ts = item.get("ts", "")
    if not channel_id or not message_ts:
        return

    try:
        auth = client.auth_test()
        bot_user_id = auth["user_id"]
    except SlackApiError as exc:
        print(f"WARN: auth_test failed in reaction handler: {exc}", file=sys.stderr)
        return

    if event.get("user") == bot_user_id:
        return

    msg = fetch_message(client, channel_id, message_ts)
    if not msg:
        return

    urls = extract_pr_urls(msg, default_repo=DEFAULT_REPO)
    if not urls:
        if MERGE_REACTION_ACK:
            slack_add_reaction(client, channel_id, message_ts, REACTION_ACK_NO_PR)
        slack_post_thread(client, channel_id, message_ts, format_reaction_no_pr())
        return

    url = urls[0]
    if len(urls) > 1:
        slack_post_thread(
            client,
            channel_id,
            message_ts,
            f":information_source: found {len(urls)} PR links — queuing {pr_label(url)}",
        )

    preflight = check_pr_preflight(url)
    if not preflight.ok:
        if MERGE_REACTION_ACK:
            slack_add_reaction(client, channel_id, message_ts, REACTION_ACK_FAIL)
        slack_post_thread(
            client,
            channel_id,
            message_ts,
            format_preflight_reject(url, preflight.reason),
        )
        return

    added, position = append_to_queue(url)
    save_thread(PR_THREADS_FILE, url, channel_id, message_ts)
    queue = read_queue()
    message = format_queued(
        url,
        position,
        added,
        queue,
        read_processing(),
        preflight.title,
    )
    if MERGE_REACTION_ACK:
        slack_add_reaction(client, channel_id, message_ts, REACTION_ACK_OK)
    slack_post_thread(client, channel_id, message_ts, message)


def worker_env() -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "MERGE_QUEUE_DIR": str(QUEUE_DATA_DIR),
            "PR_QUEUE_FILE": str(PR_QUEUE_FILE),
            "PR_FAILED_FILE": str(PR_FAILED_FILE),
            "PR_SKIPPED_FILE": str(PR_SKIPPED_FILE),
            "PR_MERGED_FILE": str(PR_MERGED_FILE),
            "PR_THREADS_FILE": str(PR_THREADS_FILE),
            "SLACK_CHANNEL_ID": SLACK_CHANNEL_ID,
            "MERGED_REACTION_EMOJI": os.environ.get("MERGED_REACTION_EMOJI", "merged"),
        }
    )
    return env


def _monitor_worker(proc: subprocess.Popen[bytes]) -> None:
    while True:
        code = proc.wait()
        print(f"Worker exited (code={code}), restarting in 5s...", file=sys.stderr)
        time.sleep(5)
        proc = subprocess.Popen(
            [str(WORKER_PATH)],
            cwd=INSTALL_DIR,
            env=worker_env(),
        )


def start_worker() -> None:
    proc = subprocess.Popen(
        [str(WORKER_PATH)],
        cwd=INSTALL_DIR,
        env=worker_env(),
    )
    thread = threading.Thread(target=_monitor_worker, args=(proc,), daemon=True)
    thread.start()


def parse_history_count(text: str) -> int | None:
    raw = (text or "").strip()
    if not raw:
        return HISTORY_DEFAULT
    if not raw.isdigit():
        return None
    count = int(raw)
    if count < 1:
        return None
    return min(count, HISTORY_MAX)


def deploy_allowed(user_id: str) -> bool:
    if not DEPLOY_ENABLED:
        return False
    if not DEPLOY_ALLOWED_USER_IDS:
        return False
    return user_id in DEPLOY_ALLOWED_USER_IDS


def trigger_deploy(response_url: str) -> None:
    if not DEPLOY_PATH.is_file():
        raise FileNotFoundError(f"deploy script not found: {DEPLOY_PATH}")
    subprocess.Popen(
        [str(DEPLOY_PATH), response_url],
        cwd=INSTALL_DIR,
        env=worker_env(),
        start_new_session=True,
    )


def create_app() -> App:
    token = os.environ.get("SLACK_BOT_TOKEN")
    if not token or not os.environ.get("SLACK_APP_TOKEN"):
        raise SystemExit("SLACK_BOT_TOKEN and SLACK_APP_TOKEN are required")

    app = App(token=token)

    @app.command("/merge")
    def handle_merge(ack, respond, client, command):
        ack()
        handle_merge_command(
            respond,
            client,
            command["channel_id"],
            command.get("text", ""),
        )

    @app.command("/merge-queue")
    def handle_merge_queue(ack, respond, client, command):
        ack()
        handle_merge_command(
            respond,
            client,
            command["channel_id"],
            command.get("text", ""),
        )

    @app.command("/merge-status")
    def handle_merge_status(ack, respond, command):
        ack()
        respond(response_type="in_channel", text=build_queue_status_message())

    @app.command("/merge-history")
    def handle_merge_history(ack, respond, command):
        ack()
        count = parse_history_count(command.get("text", ""))
        if count is None:
            respond(
                response_type="ephemeral",
                text=f"Usage: `/merge-history` or `/merge-history 10` (1–{HISTORY_MAX})",
            )
            return
        respond(response_type="in_channel", text=build_history_message(count))

    @app.command("/merge-deploy")
    def handle_merge_deploy(ack, respond, command):
        ack()
        user_id = command.get("user_id", "")

        if not DEPLOY_ENABLED:
            respond(
                response_type="ephemeral",
                text="Deploy is disabled on this host (`DEPLOY_ENABLED=false`).",
            )
            return

        if not deploy_allowed(user_id):
            respond(response_type="ephemeral", text="You're not allowed to run deploy.")
            return

        response_url = command.get("response_url", "")
        if not response_url:
            respond(response_type="ephemeral", text="Missing Slack response URL for deploy status.")
            return

        if not DEPLOY_PATH.is_file():
            respond(response_type="ephemeral", text=f"deploy script not found: {DEPLOY_PATH}")
            return

        respond(
            response_type="in_channel",
            text="Deploying — pulling from git and restarting bot + worker...",
        )
        trigger_deploy(response_url)

    @app.event("reaction_added")
    def handle_reaction_added(event, client):
        threading.Thread(
            target=handle_merge_reaction,
            args=(client, event),
            daemon=True,
        ).start()

    return app


def main() -> None:
    ensure_queue_files()

    if START_WORKER:
        if not WORKER_PATH.is_file():
            raise SystemExit(f"worker not found: {WORKER_PATH}")
        start_worker()

    app = create_app()
    handler = SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])
    print(f"Merge queue bot is running (Socket Mode, queue: {PR_QUEUE_FILE})")
    handler.start()


if __name__ == "__main__":
    main()
