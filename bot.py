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

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from messages import format_history_lines, format_queue_status, format_queued

INSTALL_DIR = Path(__file__).resolve().parent
QUEUE_DATA_DIR = Path(os.environ.get("MERGE_QUEUE_DIR", str(INSTALL_DIR)))
PR_QUEUE_FILE = Path(os.environ.get("PR_QUEUE_FILE", QUEUE_DATA_DIR / "prs.txt"))
PR_FAILED_FILE = Path(os.environ.get("PR_FAILED_FILE", QUEUE_DATA_DIR / "prs-failed.txt"))
PR_SKIPPED_FILE = Path(os.environ.get("PR_SKIPPED_FILE", QUEUE_DATA_DIR / "prs-skipped.txt"))
PR_MERGED_FILE = Path(os.environ.get("PR_MERGED_FILE", QUEUE_DATA_DIR / "prs-merged.txt"))
PR_PROCESSING_FILE = Path(os.environ.get("PR_PROCESSING_FILE", QUEUE_DATA_DIR / "processing.txt"))
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


def handle_merge_command(respond, text: str) -> None:
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

    added, position = append_to_queue(url)
    queue = read_queue()
    respond(
        response_type="in_channel",
        text=format_queued(url, position, added, queue),
    )


def worker_env() -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "MERGE_QUEUE_DIR": str(QUEUE_DATA_DIR),
            "PR_QUEUE_FILE": str(PR_QUEUE_FILE),
            "PR_FAILED_FILE": str(PR_FAILED_FILE),
            "PR_SKIPPED_FILE": str(PR_SKIPPED_FILE),
            "PR_MERGED_FILE": str(PR_MERGED_FILE),
            "SLACK_CHANNEL_ID": SLACK_CHANNEL_ID,
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
    def handle_merge(ack, respond, command):
        ack()
        handle_merge_command(respond, command.get("text", ""))

    @app.command("/merge-queue")
    def handle_merge_queue(ack, respond, command):
        ack()
        handle_merge_command(respond, command.get("text", ""))

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
