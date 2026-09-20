"""Slack block builders: mrkdwn conversion / block dict builders / text shaping."""

from typing import Any

from config import CONFIG

from ..shared import inline

type Block = dict[str, Any]

SECTION_TEXT_MAX = 2900  # Slack section text limit is 3000; cap below to leave room for headers and continuation prefixes


def truncate_headline(headline: str, limit: int = CONFIG.slack.headline_max) -> str:
    if len(headline) <= limit:
        return headline
    return headline[: limit - 1] + "…"


def escape_mrkdwn(text: str) -> str:
    """Neutralize the Slack special sequences (`<!channel>`, `<@U...>`, `<url|text>`) in `text`; they all start with `<`, so HTML-entity-escaping `&`/`<`/`>` is sufficient."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def to_mrkdwn(text: str, owner: str, repo: str) -> str:
    """Render the inline notation of a summary line as mrkdwn.

    Escaping runs first so Slack specials in the generated text stay neutralized.
    Backticks pass through unchanged because they already mean inline code in mrkdwn.
    """
    parts = []
    for segment in inline.parse_inline(escape_mrkdwn(text), owner, repo):
        if segment.code:
            parts.append(f"`{segment.text}`")
        elif segment.bold:
            parts.append(f"*{segment.text}*")
        elif segment.url:
            parts.append(f"<{segment.url}|{segment.text}>")
        else:
            parts.append(segment.text)
    return "".join(parts)


def divider() -> Block:
    return {"type": "divider"}


def header_block(text: str) -> Block:
    return {
        "type": "header",
        "text": {"type": "plain_text", "text": text},
    }


def section_block(text: str) -> Block:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def context_block(text: str) -> Block:
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": text}]}


def run_labels(is_manual: bool, is_backfill: bool) -> str:
    """Render the header suffix marking how the report was triggered, empty for a routine scheduled run so its notifications keep their usual look."""
    labels = []
    if is_manual:
        labels.append("[manual]")
    if is_backfill:
        labels.append("[backfill]")
    return "".join(f" {label}" for label in labels)


def mom_suffix(change: float | None) -> str:
    """Render the `(MoM ...)` fragment, returning empty string when `change` is None so callers can drop it for uncomputable periods."""
    if change is None:
        return ""
    return f" (MoM {change:+.0f}%)"


def chunk_lines(lines: list[str], limit: int) -> list[str]:
    """Split `lines` into chunks of at most `limit` characters, truncating any single line longer than `limit` so each chunk stays within Slack's section text limit."""
    chunks: list[str] = []
    current: list[str] = []
    used = 0
    for raw in lines:
        line = raw if len(raw) <= limit else raw[: limit - 1] + "…"
        added = len(line) + (1 if current else 0)
        if current and used + added > limit:
            chunks.append("\n".join(current))
            current = [line]
            used = len(line)
        else:
            current.append(line)
            used += added
    if current:
        chunks.append("\n".join(current))
    return chunks
