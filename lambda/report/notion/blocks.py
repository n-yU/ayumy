"""Notion block primitives: low-level rich_text / block dict builders."""

from typing import Any

from ..shared import inline

type Block = dict[str, Any]
type RichText = dict[str, Any]

RICH_TEXT_LIMIT = 2000  # Notion's rich_text per-item char limit


def chunk_rich_text(text: str) -> list[RichText]:
    return [
        {"type": "text", "text": {"content": text[i : i + RICH_TEXT_LIMIT]}}
        for i in range(0, len(text), RICH_TEXT_LIMIT)
    ]


def _segment_rich_text(segment: inline.Segment) -> list[RichText]:
    items = chunk_rich_text(segment.text)
    for item in items:
        if segment.url:
            item["text"]["link"] = {"url": segment.url}
        annotations = {}
        if segment.code:
            annotations["code"] = True
        if segment.bold:
            annotations["bold"] = True
        if annotations:
            item["annotations"] = annotations
    return items


def linked_text(content: str, url: str) -> list[RichText]:
    return [
        {
            "type": "text",
            "text": {"content": content[i : i + RICH_TEXT_LIMIT], "link": {"url": url}},
        }
        for i in range(0, len(content), RICH_TEXT_LIMIT)
    ]


def bulleted_link(
    label: str,
    url: str,
    prefix: str = "",
    children: list[Block] | None = None,
) -> Block:
    rich_text: list[RichText] = []
    if prefix:
        rich_text.append({"type": "text", "text": {"content": prefix}})
    rich_text.extend(linked_text(label, url))
    body: dict[str, Any] = {"rich_text": rich_text}
    if children:
        body["children"] = children
    return {
        "object": "block",
        "type": "bulleted_list_item",
        "bulleted_list_item": body,
    }


def bulleted_text(text: str, owner: str, repo: str) -> Block:
    rich_text: list[RichText] = []
    for segment in inline.parse_inline(text, owner, repo):
        rich_text.extend(_segment_rich_text(segment))
    return {
        "object": "block",
        "type": "bulleted_list_item",
        "bulleted_list_item": {"rich_text": rich_text},
    }


def heading_2(text: str) -> Block:
    return {
        "object": "block",
        "type": "heading_2",
        "heading_2": {
            "rich_text": [{"type": "text", "text": {"content": text}}],
        },
    }
