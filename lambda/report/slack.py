"""Slack notification client."""

import logging
from datetime import datetime

from slack_sdk.webhook import WebhookClient

from . import JST, ReportSummary
from .summarizer import ValidationResult

logger = logging.getLogger(__name__)

# Per-headline max length (truncated with ellipsis beyond this);
# keeps the aggregated section text within Slack's 3000-char limit for a realistic number of repos per day
HEADLINE_MAX = 200


def _truncate_headline(headline: str, limit: int = HEADLINE_MAX) -> str:
    """Truncate `headline` with an ellipsis when it exceeds `limit`."""
    if len(headline) <= limit:
        return headline
    return headline[: limit - 1] + "…"


def _escape_mrkdwn(text: str) -> str:
    """Escape Slack mrkdwn special characters to neutralize mentions and markup.

    All special sequences (`<!channel>`, `<@U...>`, `<url|text>`) start with `<`,
    so replacing `&`, `<`, `>` with HTML entities is sufficient to disable them entirely.
    """
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _divider() -> dict:
    """Build a Slack divider block."""
    return {"type": "divider"}


def _header_block(emoji: str, date_str: str) -> dict:
    """Build a Slack header block with a `{emoji} Daily Report ({date_str})` title."""
    return {
        "type": "header",
        "text": {
            "type": "plain_text",
            "text": f"{emoji} Daily Report ({date_str})",
        },
    }


def _section_block(text: str) -> dict:
    """Build a Slack mrkdwn section block."""
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def _context_block(text: str) -> dict:
    """Build a Slack mrkdwn context block."""
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": text}]}


class SlackClient:
    """Client for sending daily report notifications via Slack Incoming Webhook."""

    def __init__(self, webhook_url: str) -> None:
        self.client = WebhookClient(webhook_url)
        self._blocks: list[dict] = []
        self._fallback_parts: list[str] = []

    def _append_with_divider(self, *blocks: dict) -> None:
        """Append `blocks` to the buffer, prepending a divider when the buffer already holds content."""
        if self._blocks:
            self._blocks.append(_divider())
        self._blocks.extend(blocks)

    def notify(
        self,
        target_date: datetime,
        report: ReportSummary,
        pages: list[tuple[str, str]],
        skipped_repos: list[str] | None = None,
    ) -> None:
        """Buffer a daily-report notification for `target_date` listing the created Notion pages.

        `skipped_repos` covers repos that appeared in `report` but were dropped during Notion page creation.
        """
        date_str = target_date.astimezone(JST).strftime("%Y-%m-%d")

        if self._blocks:
            self._blocks.append({"type": "divider"})

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

            self._blocks.extend(
                [
                    {
                        "type": "header",
                        "text": {
                            "type": "plain_text",
                            "text": f"📝 Daily Report ({date_str})",
                        },
                    },
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": "\n".join(page_lines)},
                    },
                ]
            )
            if skipped_repos:
                skipped = ", ".join(skipped_repos)
                self._blocks.append(
                    {
                        "type": "context",
                        "elements": [
                            {"type": "mrkdwn", "text": f"⚠️ Skipped: {skipped}"}
                        ],
                    }
                )
            self._fallback_parts.append(
                f"📝 Daily Report ({date_str}): {len(pages)} page(s) created"
            )
        else:
            self._blocks.extend(
                [
                    {
                        "type": "header",
                        "text": {
                            "type": "plain_text",
                            "text": f"✅ Daily Report ({date_str})",
                        },
                    },
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": "No pages created"},
                    },
                ]
            )
            if skipped_repos:
                skipped = ", ".join(skipped_repos)
                self._blocks.append(
                    {
                        "type": "context",
                        "elements": [
                            {"type": "mrkdwn", "text": f"⚠️ Skipped: {skipped}"}
                        ],
                    }
                )
            self._fallback_parts.append(
                f"✅ Daily Report ({date_str}): No pages created"
            )

    def notify_no_activity(self, target_date: datetime) -> None:
        """Buffer a no-activity notification for `target_date`."""
        date_str = target_date.astimezone(JST).strftime("%Y-%m-%d")

        if self._blocks:
            self._blocks.append({"type": "divider"})

        self._blocks.extend(
            [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": f"💤 Daily Report ({date_str})",
                    },
                },
                {"type": "section", "text": {"type": "mrkdwn", "text": "No activity"}},
            ]
        )
        self._fallback_parts.append(f"💤 Daily Report ({date_str}): No activity")

    def notify_validation_errors(
        self,
        target_date: datetime,
        result: ValidationResult,
    ) -> None:
        """Buffer a notification listing the invalid tag values detected during summary validation."""
        date_str = target_date.astimezone(JST).strftime("%Y-%m-%d")
        lines = []
        for name, tags in result.invalid_tags.items():
            lines.append(f"• {name}: tags={tags}")

        if self._blocks:
            self._blocks.append({"type": "divider"})

        self._blocks.extend(
            [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": f"⚠️ Daily Report ({date_str})",
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"Invalid tags detected\n{'\n'.join(lines)}",
                    },
                },
            ]
        )
        self._fallback_parts.append(
            f"⚠️ Daily Report ({date_str}): Invalid tags detected"
        )

    def notify_error(self, target_date: datetime, error: Exception) -> None:
        """Buffer an error notification carrying `error`'s message for `target_date`."""
        date_str = target_date.astimezone(JST).strftime("%Y-%m-%d")

        if self._blocks:
            self._blocks.append({"type": "divider"})

        self._blocks.extend(
            [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": f"❌ Daily Report ({date_str})",
                    },
                },
                {"type": "section", "text": {"type": "mrkdwn", "text": str(error)}},
            ]
        )
        self._fallback_parts.append(f"❌ Daily Report ({date_str}): {error}")

    def notify_metrics(
        self,
        elapsed: float,
        peak_memory_mb: float,
        version: str,
        memory_limit_mb: int | None = None,
        timeout_seconds: int | None = None,
    ) -> None:
        """Buffer an execution-metrics context block.

        `memory_limit_mb` / `timeout_seconds` are None when invoked from the CLI,
        in which case ratios against the limits are omitted from the rendered text.
        """
        if self._blocks:
            self._blocks.append({"type": "divider"})

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

        self._blocks.append(
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"🔖 v{version}  |  ⏱️ {elapsed_text}  |  💾 {memory_text}",
                    }
                ],
            }
        )
        self._fallback_parts.append(
            f"📊 Execution Metrics: v{version}, {elapsed_text}, {memory_text}"
        )

    def flush(self) -> None:
        """Send all buffered blocks as a single Slack message."""
        if not self._blocks:
            return
        fallback = " | ".join(self._fallback_parts)
        self._send(fallback, self._blocks)
        self._blocks = []
        self._fallback_parts = []

    def _send(self, text: str, blocks: list[dict] | None = None) -> None:
        """Send a message via Slack webhook on a best-effort basis;
        failures are logged but do not raise so notification errors never abort report generation.
        """
        try:
            response = self.client.send(text=text, blocks=blocks)
            if response.status_code != 200:
                logger.error(
                    "Failed to send Slack notification: status=%d, body=%s",
                    response.status_code,
                    response.body,
                )
        except Exception as e:
            logger.exception("Failed to send Slack notification: %r", e)
