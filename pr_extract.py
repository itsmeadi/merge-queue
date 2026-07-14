#!/usr/bin/env python3
"""Extract GitHub PR URLs from Slack message payloads."""

from __future__ import annotations

import re
from typing import Any

PR_URL_RE = re.compile(
    r"https?://github\.com/[^/\s>]+/[^/\s>]+/pull/\d+",
    re.IGNORECASE,
)
SLACK_LINK_RE = re.compile(r"<(https?://[^>|]+)(?:\|[^>]*)?>")


def _urls_in_text(text: str) -> list[str]:
    found: list[str] = []
    for match in SLACK_LINK_RE.finditer(text):
        found.extend(PR_URL_RE.findall(match.group(1)))
    found.extend(PR_URL_RE.findall(text))
    return found


def _walk_blocks(blocks: list[Any]) -> list[str]:
    parts: list[str] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "section":
            text_obj = block.get("text") or {}
            if isinstance(text_obj, dict) and text_obj.get("text"):
                parts.append(str(text_obj["text"]))
            for field in block.get("fields") or []:
                if isinstance(field, dict) and field.get("text"):
                    parts.append(str(field["text"]))
        elif block_type == "context":
            for element in block.get("elements") or []:
                if isinstance(element, dict):
                    if element.get("text"):
                        parts.append(str(element["text"]))
                    if element.get("url"):
                        parts.append(str(element["url"]))
        elif block_type == "rich_text":
            parts.extend(_walk_rich_text(block.get("elements") or []))
    return parts


def _walk_rich_text(elements: list[Any]) -> list[str]:
    parts: list[str] = []
    for element in elements:
        if not isinstance(element, dict):
            continue
        element_type = element.get("type")
        if element_type == "rich_text_section":
            for sub in element.get("elements") or []:
                if isinstance(sub, dict):
                    if sub.get("type") == "link" and sub.get("url"):
                        parts.append(str(sub["url"]))
                    elif sub.get("text"):
                        parts.append(str(sub["text"]))
        elif element_type == "rich_text_list":
            parts.extend(_walk_rich_text(element.get("elements") or []))
        elif element_type == "rich_text_preformatted":
            for sub in element.get("elements") or []:
                if isinstance(sub, dict) and sub.get("text"):
                    parts.append(str(sub["text"]))
    return parts


def message_text_parts(msg: dict[str, Any]) -> list[str]:
    parts: list[str] = []
    if msg.get("text"):
        parts.append(str(msg["text"]))
    for attachment in msg.get("attachments") or []:
        if not isinstance(attachment, dict):
            continue
        for key in (
            "title_link",
            "from_url",
            "author_link",
            "title",
            "text",
            "pretext",
            "fallback",
        ):
            if attachment.get(key):
                parts.append(str(attachment[key]))
    parts.extend(_walk_blocks(msg.get("blocks") or []))
    return parts


def normalize_pr_url(url: str) -> str:
    cleaned = url.split("?")[0].split("#")[0].rstrip("/")
    if cleaned.startswith("http://"):
        cleaned = "https://" + cleaned[len("http://") :]
    return cleaned


def extract_pr_urls(
    msg: dict[str, Any],
    *,
    default_repo: str = "",
) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for part in message_text_parts(msg):
        for raw in _urls_in_text(part):
            url = normalize_pr_url(raw)
            if url in seen:
                continue
            if default_repo:
                prefix = f"github.com/{default_repo}/pull/".lower()
                if prefix not in url.lower():
                    continue
            seen.add(url)
            out.append(url)
    return out
