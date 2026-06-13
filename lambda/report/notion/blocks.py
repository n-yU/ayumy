"""Notion block primitives: low-level rich_text / block dict builders."""

RICH_TEXT_LIMIT = 2000


def chunk_rich_text(text: str) -> list[dict]:
    """Split `text` into rich_text objects each within Notion's per-item character limit."""
    return [
        {"type": "text", "text": {"content": text[i : i + RICH_TEXT_LIMIT]}}
        for i in range(0, len(text), RICH_TEXT_LIMIT)
    ]


def linked_text(content: str, url: str) -> list[dict]:
    """Build hyperlinked rich_text objects, each within Notion's per-item character limit and sharing the same link."""
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
    children: list[dict] | None = None,
) -> dict:
    """Build a `bulleted_list_item` block with an optional plain-text `prefix` rendered before the linked label and optional nested `children`."""
    rich_text: list[dict] = []
    if prefix:
        rich_text.append({"type": "text", "text": {"content": prefix}})
    rich_text.extend(linked_text(label, url))
    body: dict = {"rich_text": rich_text}
    if children:
        body["children"] = children
    return {
        "object": "block",
        "type": "bulleted_list_item",
        "bulleted_list_item": body,
    }
