"""Slack notification client."""

import logging
from collections import defaultdict
from datetime import datetime

from slack_sdk import WebClient
from slack_sdk.errors import SlackClientError

from config import CONFIG

from . import JST, ReportSummary
from .notice import Notice
from .summarizer import ValidationResult

logger = logging.getLogger(__name__)

# Slack section text limit is 3000; cap below to leave room for headers and continuation prefixes
SECTION_TEXT_MAX = 2900


def _truncate_headline(headline: str, limit: int = CONFIG.slack.headline_max) -> str:
    if len(headline) <= limit:
        return headline
    return headline[: limit - 1] + "…"


def _escape_mrkdwn(text: str) -> str:
    """All Slack mrkdwn specials (`<!channel>`, `<@U...>`, `<url|text>`) start with `<`, so HTML-entity-escaping `&`/`<`/`>` is sufficient to disable them."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _divider() -> dict:
    return {"type": "divider"}


def _header_block(text: str) -> dict:
    return {
        "type": "header",
        "text": {"type": "plain_text", "text": text},
    }


def _section_block(text: str) -> dict:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def _context_block(text: str) -> dict:
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": text}]}


def _chunk_lines(lines: list[str], limit: int) -> list[str]:
    """Truncates any single line longer than `limit` so each chunk stays within Slack's section text limit."""
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


class SlackClient:
    """Client for sending daily report notifications via Slack chat.postMessage."""

    def __init__(self, token: str, channel: str) -> None:
        self.client = WebClient(token=token)
        self.channel = channel
        self._blocks: list[dict] = []
        self._fallback_parts: list[str] = []
        self.parent_ts: str | None = None

    def _append_with_divider(self, *blocks: dict) -> None:
        if self._blocks:
            self._blocks.append(_divider())
        self._blocks.extend(blocks)

    def _append_report_section(
        self,
        emoji: str,
        date_str: str,
        body_text: str,
        fallback_suffix: str,
        skipped_repos: list[str] | None = None,
    ) -> None:
        title = f"{emoji} Daily Report ({date_str})"
        blocks = [_header_block(title), _section_block(body_text)]
        if skipped_repos:
            blocks.append(_context_block(f"⚠️ Skipped: {', '.join(skipped_repos)}"))
        self._append_with_divider(*blocks)
        self._fallback_parts.append(f"{title}: {fallback_suffix}")

    def notify(
        self,
        target_date: datetime,
        report: ReportSummary,
        pages: list[tuple[str, str]],
        skipped_repos: list[str] | None = None,
    ) -> None:
        """`skipped_repos` covers repos that appeared in `report` but were dropped during Notion page creation."""
        date_str = target_date.astimezone(JST).strftime("%Y-%m-%d")

        if pages:
            repo_map = {r["name"]: r for r in report["repositories"]}
            page_lines = []
            for name, url in pages:
                repo = repo_map.get(name)
                raw_headline = repo["summary"][0] if repo and repo["summary"] else ""
                # Collapse newlines so a multi-line headline cannot break the one-line-per-repo layout of the Slack section.
                raw_headline = raw_headline.replace("\n", " ").replace("\r", " ")
                headline = _escape_mrkdwn(_truncate_headline(raw_headline))
                link = f"<{url}|{date_str}: {name}>"
                page_lines.append(f"{link} — {headline}" if headline else link)

            self._append_report_section(
                "📝",
                date_str,
                "\n".join(page_lines),
                f"{len(pages)} page(s) created",
                skipped_repos=skipped_repos,
            )
        else:
            self._append_report_section(
                "✅",
                date_str,
                "No pages created",
                "No pages created",
                skipped_repos=skipped_repos,
            )

    def notify_no_activity(self, target_date: datetime) -> None:
        date_str = target_date.astimezone(JST).strftime("%Y-%m-%d")
        self._append_report_section("💤", date_str, "No activity", "No activity")

    def notify_validation_errors(
        self,
        target_date: datetime,
        result: ValidationResult,
    ) -> None:
        date_str = target_date.astimezone(JST).strftime("%Y-%m-%d")
        lines = [f"• {name}: tags={tags}" for name, tags in result.invalid_tags.items()]
        body = f"Invalid tags detected\n{'\n'.join(lines)}"
        self._append_report_section("⚠️", date_str, body, "Invalid tags detected")

    def notify_error(self, target_date: datetime, error: Exception) -> None:
        date_str = target_date.astimezone(JST).strftime("%Y-%m-%d")
        self._append_report_section("❌", date_str, str(error), str(error))

    def notify_metrics(
        self,
        elapsed: float,
        peak_memory_mb: float,
        version: str,
        memory_limit_mb: int | None = None,
        timeout_seconds: int | None = None,
    ) -> None:
        """`memory_limit_mb` / `timeout_seconds` are None from CLI; ratios against limits are then omitted from the rendered text."""
        if timeout_seconds is not None:
            elapsed_pct = elapsed / timeout_seconds * 100
            elapsed_text = f"{elapsed:.1f} / {timeout_seconds}s ({elapsed_pct:.0f}%)"
        else:
            elapsed_text = f"{elapsed:.1f}s"

        if memory_limit_mb is not None:
            pct = peak_memory_mb / memory_limit_mb * 100
            memory_text = f"{peak_memory_mb:.0f} / {memory_limit_mb} MB ({pct:.0f}%)"
        else:
            memory_text = f"{peak_memory_mb:.0f} MB"

        self._append_with_divider(
            _context_block(f"🔖 v{version}  |  ⏱️ {elapsed_text}  |  💾 {memory_text}")
        )
        self._fallback_parts.append(
            f"📊 Execution Metrics: v{version}, {elapsed_text}, {memory_text}"
        )

    def flush(self) -> None:
        if not self._blocks:
            return
        fallback = " | ".join(self._fallback_parts)
        self._send(fallback, self._blocks)
        self._blocks = []
        self._fallback_parts = []

    def send_notice_thread(self, notice: Notice) -> None:
        """Posts as a reply to `parent_ts` so the warning digest stays attached to the daily summary."""
        if not notice or self.parent_ts is None:
            return
        grouped: dict[str, list[str]] = defaultdict(list)
        for entry in notice.entries():
            line = f"• {_escape_mrkdwn(entry.title)}"
            if entry.details:
                detail_str = ", ".join(
                    f"{k}={_escape_mrkdwn(v)}" for k, v in entry.details.items()
                )
                line += f"  `{detail_str}`"
            grouped[entry.source].append(line)
        total = len(notice)
        blocks: list[dict] = [_header_block(f"⚠️ Warnings ({total})")]
        for source, lines in grouped.items():
            header = f"*{source}* ({len(lines)})"
            for chunk in _chunk_lines(lines, SECTION_TEXT_MAX - len(header) - 1):
                blocks.append(_section_block(f"{header}\n{chunk}"))
        fallback = f"⚠️ {total} warning(s) emitted"
        self._send(fallback, blocks, thread_ts=self.parent_ts)

    def _send(
        self,
        text: str,
        blocks: list[dict] | None = None,
        *,
        thread_ts: str | None = None,
    ) -> None:
        """Suppress Slack SDK failures as best-effort; other exceptions propagate to the pipeline."""
        try:
            kwargs: dict = {"channel": self.channel, "text": text, "blocks": blocks}
            if thread_ts is not None:
                kwargs["thread_ts"] = thread_ts
            response = self.client.chat_postMessage(**kwargs)
            if self.parent_ts is None and thread_ts is None:
                self.parent_ts = response.get("ts")
        except SlackClientError as e:
            # Broad within Slack SDK errors: best-effort notification must not abort the pipeline
            logger.exception("Failed to send Slack notification: %r", e)
