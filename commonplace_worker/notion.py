"""Notion API helpers for Commonplace ingestion.

The Therapy ingest path treats Notion as read-only and canonical. This module
keeps HTTP/auth/markdown conversion pure enough to unit test without touching
the database or worker dispatcher.
"""

from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx

logger = logging.getLogger(__name__)

NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
NOTION_TOKEN_ENV = "COMMONPLACE_NOTION_TOKEN"
THERAPY_PARENT_ENV = "COMMONPLACE_THERAPY_PARENT_PAGE_ID"
DEFAULT_THERAPY_PARENT_PAGE_ID = "3655db5e-048c-816d-b610-d921130ef038"

_KEYCHAIN_SERVICE = "commonplace_notion_token"
_KEYCHAIN_ACCOUNT = "commonplace"


class NotionConfigError(Exception):
    """Raised when Notion configuration or credentials are unavailable."""


class NotionAPIError(Exception):
    """Raised when a Notion API call fails."""


@dataclass(frozen=True)
class NotionPageSummary:
    page_id: str
    title: str
    last_edited_time: str
    url: str | None


def resolve_notion_token() -> str | None:
    """Resolve the Notion integration token from env or macOS Keychain."""
    env_val = os.environ.get(NOTION_TOKEN_ENV)
    if env_val:
        return env_val.strip()

    try:
        result = subprocess.run(
            [
                "security",
                "find-generic-password",
                "-a",
                _KEYCHAIN_ACCOUNT,
                "-s",
                _KEYCHAIN_SERVICE,
                "-w",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        logger.debug("Notion token keychain lookup failed: %s", exc)
        return None

    if result.returncode != 0:
        return None
    token = result.stdout.strip()
    return token or None


def resolve_therapy_parent_page_id() -> str:
    """Return the configured Therapy parent page ID."""
    return os.environ.get(THERAPY_PARENT_ENV, DEFAULT_THERAPY_PARENT_PAGE_ID).strip()


class NotionClient:
    """Minimal Notion REST client for pages and block children."""

    def __init__(
        self,
        *,
        token: str | None = None,
        base_url: str = NOTION_API_BASE,
        timeout: float = 20.0,
    ) -> None:
        resolved = token if token is not None else resolve_notion_token()
        if not resolved:
            raise NotionConfigError(
                "Notion token is not configured. Set COMMONPLACE_NOTION_TOKEN or run: "
                "security add-generic-password -U -a commonplace "
                "-s commonplace_notion_token -w '<notion-integration-token>'"
            )
        self._token = resolved
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Notion-Version": NOTION_VERSION,
            "Accept": "application/json",
        }

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        query = f"?{urlencode(params)}" if params else ""
        url = f"{self._base_url}{path}{query}"
        try:
            resp = httpx.get(url, headers=self._headers(), timeout=self._timeout)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as exc:
            raise NotionAPIError(
                f"Notion API returned HTTP {exc.response.status_code} for {path}"
            ) from exc
        except (httpx.RequestError, ValueError) as exc:
            raise NotionAPIError(f"Notion API request failed for {path}: {exc}") from exc
        if not isinstance(data, dict):
            raise NotionAPIError(f"Notion API returned non-object JSON for {path}")
        return data

    def get_page(self, page_id: str) -> dict[str, Any]:
        return self._get(f"/pages/{page_id}")

    def list_block_children(self, block_id: str) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {"page_size": 100}
            if cursor:
                params["start_cursor"] = cursor
            data = self._get(f"/blocks/{block_id}/children", params=params)
            page_results = data.get("results", [])
            if isinstance(page_results, list):
                results.extend([r for r in page_results if isinstance(r, dict)])
            if not data.get("has_more"):
                return results
            next_cursor = data.get("next_cursor")
            cursor = next_cursor if isinstance(next_cursor, str) else None
            if cursor is None:
                return results

    def fetch_block_tree(self, block_id: str) -> list[dict[str, Any]]:
        blocks = self.list_block_children(block_id)
        for block in blocks:
            if block.get("has_children"):
                child_id = block.get("id")
                if isinstance(child_id, str):
                    block["children"] = self.fetch_block_tree(child_id)
        return blocks

    def list_child_pages(self, parent_page_id: str) -> list[dict[str, Any]]:
        return [
            block
            for block in self.list_block_children(parent_page_id)
            if block.get("type") == "child_page"
        ]


def page_summary(page: dict[str, Any]) -> NotionPageSummary:
    """Extract the fields needed by the handler and watcher from a page object."""
    page_id = str(page.get("id") or "")
    title = extract_page_title(page)
    last_edited = str(page.get("last_edited_time") or "")
    url = page.get("url")
    return NotionPageSummary(
        page_id=page_id,
        title=title,
        last_edited_time=last_edited,
        url=url if isinstance(url, str) else None,
    )


def extract_page_title(page: dict[str, Any]) -> str:
    """Return the first title property text from a Notion page object."""
    props = page.get("properties")
    if not isinstance(props, dict):
        return ""
    for prop in props.values():
        if not isinstance(prop, dict) or prop.get("type") != "title":
            continue
        return _rich_text_plain(prop.get("title", []))
    return ""


def extract_property_text(page: dict[str, Any], names: tuple[str, ...]) -> str | None:
    """Extract a plain string from the first matching Notion property name."""
    props = page.get("properties")
    if not isinstance(props, dict):
        return None
    lower_names = {name.lower() for name in names}
    for prop_name, prop in props.items():
        if str(prop_name).lower() not in lower_names or not isinstance(prop, dict):
            continue
        typ = prop.get("type")
        if typ == "rich_text":
            value = _rich_text_plain(prop.get("rich_text", []))
        elif typ == "title":
            value = _rich_text_plain(prop.get("title", []))
        elif typ == "select":
            select = prop.get("select")
            value = str(select.get("name", "")) if isinstance(select, dict) else ""
        else:
            value = ""
        if value.strip():
            return value.strip()
    return None


def blocks_to_markdown(blocks: list[dict[str, Any]]) -> str:
    """Convert a Notion block tree to clean Markdown."""
    lines = _render_blocks(blocks, depth=0)
    return _clean_markdown_lines(lines).rstrip() + "\n"


def _render_blocks(blocks: list[dict[str, Any]], *, depth: int) -> list[str]:
    rendered: list[str] = []
    number = 1
    for block in blocks:
        block_type = block.get("type")
        data = block.get(block_type) if isinstance(block_type, str) else None
        data = data if isinstance(data, dict) else {}
        prefix = "  " * depth

        if block_type == "paragraph":
            text = _rich_text_markdown(data.get("rich_text", []))
            if text:
                rendered.extend([prefix + text, ""])
        elif block_type in {"heading_1", "heading_2", "heading_3"}:
            level = {"heading_1": "#", "heading_2": "##", "heading_3": "###"}[block_type]
            text = _rich_text_markdown(data.get("rich_text", []))
            if text:
                rendered.extend([f"{level} {text}", ""])
        elif block_type == "quote":
            text = _rich_text_markdown(data.get("rich_text", []))
            if text:
                rendered.extend([f"{prefix}> {text}", ""])
            children = block.get("children")
            if isinstance(children, list):
                child_lines = _render_blocks(children, depth=depth)
                rendered.extend([_quote_line(line) for line in child_lines])
        elif block_type == "bulleted_list_item":
            text = _rich_text_markdown(data.get("rich_text", []))
            rendered.append(f"{prefix}- {text}")
            rendered.extend(_render_list_children(block, depth=depth + 1))
            rendered.append("")
            number = 1
        elif block_type == "numbered_list_item":
            text = _rich_text_markdown(data.get("rich_text", []))
            rendered.append(f"{prefix}{number}. {text}")
            rendered.extend(_render_list_children(block, depth=depth + 1))
            rendered.append("")
            number += 1
        elif block_type == "divider":
            rendered.extend(["---", ""])
            number = 1
        elif block_type == "child_page":
            title = data.get("title")
            if isinstance(title, str) and title.strip():
                rendered.extend([f"## {title.strip()}", ""])
            number = 1
        else:
            rich = data.get("rich_text")
            text = _rich_text_markdown(rich) if isinstance(rich, list) else ""
            if text:
                rendered.extend([prefix + text, ""])
            number = 1
    return rendered


def _render_list_children(block: dict[str, Any], *, depth: int) -> list[str]:
    children = block.get("children")
    if not isinstance(children, list):
        return []
    return [line for line in _render_blocks(children, depth=depth) if line != ""]


def _quote_line(line: str) -> str:
    return ">" if not line else f"> {line}"


def _clean_markdown_lines(lines: list[str]) -> str:
    cleaned: list[str] = []
    blank = False
    for line in lines:
        stripped = line.rstrip()
        if not stripped:
            if not blank and cleaned:
                cleaned.append("")
            blank = True
            continue
        cleaned.append(stripped)
        blank = False
    return "\n".join(cleaned)


def _rich_text_plain(items: Any) -> str:
    if not isinstance(items, list):
        return ""
    parts: list[str] = []
    for item in items:
        if isinstance(item, dict):
            plain = item.get("plain_text")
            if isinstance(plain, str):
                parts.append(plain)
    return "".join(parts).strip()


def _rich_text_markdown(items: Any) -> str:
    if not isinstance(items, list):
        return ""
    parts: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        text = item.get("plain_text")
        if not isinstance(text, str) or not text:
            continue
        annotations = item.get("annotations")
        href = item.get("href")
        parts.append(_apply_annotations(text, annotations, href if isinstance(href, str) else None))
    return "".join(parts).strip()


def _apply_annotations(text: str, annotations: Any, href: str | None) -> str:
    if not isinstance(annotations, dict):
        annotations = {}
    escaped = text.replace("`", "\\`")
    if annotations.get("code"):
        escaped = f"`{escaped}`"
    if annotations.get("bold"):
        escaped = f"**{escaped}**"
    if annotations.get("italic"):
        escaped = f"*{escaped}*"
    if annotations.get("strikethrough"):
        escaped = f"~~{escaped}~~"
    if href:
        escaped = f"[{escaped}]({href})"
    return escaped
