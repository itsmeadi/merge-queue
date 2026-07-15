#!/usr/bin/env python3
"""Extract failed CI check names and a short log excerpt for a PR."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from typing import Any

PR_URL_RE = re.compile(r"github\.com/([^/]+)/([^/]+)/pull/(\d+)")
RUN_ID_RE = re.compile(r"/actions/runs/(\d+)")
GO_FAIL_RE = re.compile(r"--- FAIL: (\S+)")
GO_FILE_RE = re.compile(r"^\s+(\S+_test\.go:\d+:.*)$", re.MULTILINE)
GENERIC_FAIL_RE = re.compile(r"(FAIL|Error:|exit code 1)", re.IGNORECASE)

MAX_EXCERPT = 400
SKIP_RERUN_CHECK_NAMES = frozenset({"ready to merge"})


def parse_repo(url: str) -> str | None:
    match = PR_URL_RE.search(url)
    if not match:
        return None
    return f"{match.group(1)}/{match.group(2)}"


def run_gh(args: list[str], timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def fetch_failed_checks(url: str) -> list[dict[str, Any]]:
    result = run_gh(["gh", "pr", "checks", url, "--json", "name,bucket,link"])
    if result.returncode != 0:
        return []
    try:
        checks = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
    return [check for check in checks if check.get("bucket") == "fail"]


def extract_run_id(link: str) -> str | None:
    match = RUN_ID_RE.search(link or "")
    return match.group(1) if match else None


def fetch_failed_log(repo: str, run_id: str) -> str:
    result = run_gh(
        ["gh", "run", "view", run_id, "--repo", repo, "--log-failed"],
        timeout=120,
    )
    if result.returncode != 0:
        return result.stderr or result.stdout or ""
    return result.stdout


def parse_excerpt(log_text: str) -> str:
    if not log_text.strip():
        return ""

    lines = log_text.splitlines()
    tail = lines[-80:] if len(lines) > 80 else lines
    tail_text = "\n".join(tail)

    parts: list[str] = []
    for match in GO_FAIL_RE.finditer(tail_text):
        parts.append(f"--- FAIL: {match.group(1)}")
        break

    for match in GO_FILE_RE.finditer(tail_text):
        parts.append(match.group(1).strip())
        if len(parts) >= 3:
            break

    if not parts:
        for line in reversed(tail):
            if GENERIC_FAIL_RE.search(line):
                parts.insert(0, line.strip())
                if len(parts) >= 3:
                    break

    if not parts:
        for line in reversed(tail):
            stripped = line.strip()
            if stripped:
                parts.insert(0, stripped)
                if len(parts) >= 2:
                    break

    excerpt = "\n".join(parts).strip()
    if len(excerpt) > MAX_EXCERPT:
        excerpt = excerpt[: MAX_EXCERPT - 3] + "..."
    return excerpt


def fetch_head_ref(url: str) -> tuple[str, str]:
    """Return (branch, head_oid) for a PR."""
    result = run_gh(
        ["gh", "pr", "view", url, "--json", "headRefName,headRefOid"],
    )
    if result.returncode != 0:
        return "", ""
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return "", ""
    if not isinstance(data, dict):
        return "", ""
    branch = str(data.get("headRefName") or "").strip()
    oid = str(data.get("headRefOid") or "").strip()
    return branch, oid


def list_failed_run_id(repo: str, *, commit: str = "", branch: str = "") -> str | None:
    args = [
        "gh",
        "run",
        "list",
        "-R",
        repo,
        "--status",
        "failure",
        "-L",
        "5",
        "--json",
        "databaseId",
    ]
    if commit:
        args.extend(["--commit", commit])
    elif branch:
        args.extend(["-b", branch])
    else:
        return None

    result = run_gh(args)
    if result.returncode != 0:
        return None
    try:
        runs = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(runs, list):
        return None
    for run in runs:
        if not isinstance(run, dict):
            continue
        run_id = str(run.get("databaseId") or "").strip()
        if run_id and run_id != "null":
            return run_id
    return None


def find_rerun_run_id(url: str) -> str | None:
    """Pick a failed Actions run to rerun — not the latest run on HEAD."""
    failed_checks = fetch_failed_checks(url)
    seen: set[str] = set()
    for check in failed_checks:
        name = str(check.get("name") or "").strip()
        if name.lower() in SKIP_RERUN_CHECK_NAMES:
            continue
        run_id = extract_run_id(str(check.get("link") or ""))
        if run_id and run_id not in seen:
            seen.add(run_id)
            return run_id

    repo = parse_repo(url)
    if not repo:
        return None

    branch, oid = fetch_head_ref(url)
    if oid:
        run_id = list_failed_run_id(repo, commit=oid)
        if run_id:
            return run_id
    if branch:
        return list_failed_run_id(repo, branch=branch)
    return None


def collect_ci_failure(url: str) -> dict[str, Any]:
    failed_checks = fetch_failed_checks(url)
    names = [str(check.get("name", "")) for check in failed_checks if check.get("name")]
    job = names[0] if names else ""

    excerpt = ""
    repo = parse_repo(url)
    if repo and failed_checks:
        run_id = extract_run_id(str(failed_checks[0].get("link", "")))
        if run_id:
            excerpt = parse_excerpt(fetch_failed_log(repo, run_id))

    return {
        "failed_checks": names,
        "job": job,
        "excerpt": excerpt,
    }


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(2)

    if sys.argv[1] == "rerun-run-id":
        if len(sys.argv) != 3:
            sys.exit(2)
        run_id = find_rerun_run_id(sys.argv[2])
        if not run_id:
            sys.exit(1)
        print(run_id)
        sys.exit(0)

    if len(sys.argv) != 2:
        sys.exit(2)
    print(json.dumps(collect_ci_failure(sys.argv[1])))


if __name__ == "__main__":
    main()
