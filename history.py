#!/usr/bin/env python3
"""Merge history helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class HistoryEntry:
    timestamp: datetime
    url: str
    outcome: str
    reason: str


def dedupe_history_by_url(entries: list[HistoryEntry]) -> list[HistoryEntry]:
    """Keep the newest entry per PR URL (entries must be newest-first)."""
    seen: set[str] = set()
    deduped: list[HistoryEntry] = []
    for entry in entries:
        if entry.url in seen:
            continue
        seen.add(entry.url)
        deduped.append(entry)
    return deduped
