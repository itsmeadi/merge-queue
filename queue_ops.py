#!/usr/bin/env python3
"""Queue file read/write helpers (shared by bot and tests)."""

from __future__ import annotations

from pathlib import Path

from queue_meta import clear_thread


def read_queue(queue_file: Path) -> list[str]:
    if not queue_file.exists():
        return []
    lines = queue_file.read_text().splitlines()
    return [line.strip() for line in lines if line.strip() and not line.strip().startswith("#")]


def write_queue(queue_file: Path, urls: list[str]) -> None:
    queue_file.parent.mkdir(parents=True, exist_ok=True)
    queue_file.write_text("\n".join(urls) + ("\n" if urls else ""))


def append_to_queue(queue_file: Path, url: str) -> tuple[bool, int]:
    queue = read_queue(queue_file)
    if url in queue:
        return False, queue.index(url) + 1
    with queue_file.open("a") as f:
        f.write(f"{url}\n")
    return True, len(queue) + 1


def remove_from_queue(
    queue_file: Path,
    threads_file: Path,
    processing_file: Path,
    url: str,
) -> tuple[str, int]:
    """Remove a PR from the queue. Returns (status, position).

    status is one of: removed, not_found, processing.
    """
    processing = processing_file.read_text().strip() if processing_file.exists() else ""
    if url == processing:
        return "processing", 0

    queue = read_queue(queue_file)
    if url not in queue:
        return "not_found", 0

    position = queue.index(url) + 1
    write_queue(queue_file, [entry for entry in queue if entry != url])
    clear_thread(threads_file, url)
    return "removed", position
