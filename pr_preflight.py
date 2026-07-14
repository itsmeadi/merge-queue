#!/usr/bin/env python3
"""GitHub PR preflight checks before enqueueing via /merge."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class PreflightResult:
    ok: bool
    reason: str = ""
    title: str = ""
    author: str = ""


def _author_login(data: dict[str, object]) -> str:
    author = data.get("author")
    if isinstance(author, dict):
        return str(author.get("login") or "").strip()
    return ""


def _evaluate_pr(data: dict[str, object]) -> PreflightResult:
    state = str(data.get("state") or "")
    mergeable = str(data.get("mergeable") or "")
    review = str(data.get("reviewDecision") or "")
    title = str(data.get("title") or "").strip()
    author = _author_login(data)

    if state == "MERGED":
        return PreflightResult(ok=False, reason="already MERGED", title=title, author=author)
    if state == "CLOSED":
        return PreflightResult(ok=False, reason="already CLOSED", title=title, author=author)
    if mergeable == "CONFLICTING":
        return PreflightResult(ok=False, reason="merge conflict", title=title, author=author)
    if review == "REVIEW_REQUIRED":
        return PreflightResult(ok=False, reason="missing approval", title=title, author=author)
    if review == "CHANGES_REQUESTED":
        return PreflightResult(ok=False, reason="changes requested", title=title, author=author)
    return PreflightResult(ok=True, title=title, author=author)


def check_pr_preflight(url: str, timeout: int = 15) -> PreflightResult:
    try:
        result = subprocess.run(
            [
                "gh",
                "pr",
                "view",
                url,
                "--json",
                "state,mergeable,reviewDecision,title,author",
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return PreflightResult(ok=False, reason=f"gh pr view failed: {exc}")

    if result.returncode != 0:
        return PreflightResult(ok=False, reason="gh pr view failed")

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return PreflightResult(ok=False, reason="gh pr view failed")

    if not isinstance(data, dict):
        return PreflightResult(ok=False, reason="gh pr view failed")

    return _evaluate_pr(data)


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(2)
    preflight = check_pr_preflight(sys.argv[1])
    print(
        json.dumps(
            {
                "ok": preflight.ok,
                "reason": preflight.reason,
                "title": preflight.title,
                "author": preflight.author,
            }
        )
    )


if __name__ == "__main__":
    main()
