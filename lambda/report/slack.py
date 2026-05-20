"""Slack notification client."""

import logging
from datetime import datetime

from slack_sdk.webhook import WebhookClient

from . import JST, ReportSummary
from .summarizer import ValidationResult

logger = logging.getLogger(__name__)

# Per-headline max length (truncated with ellipsis beyond this) to keep the
# aggregated section text comfortably within Slack's 3000-char limit for a
# realistic number of repos per day
HEADLINE_MAX = 200


def _truncate_headline(headline: str, limit: int = HEADLINE_MAX) -> str:
    """Truncate a headline string with an ellipsis if it exceeds the limit."""
    if len(headline) <= limit:
        return headline
    return headline[: limit - 1] + "…"


def _escape_mrkdwn(text: str) -> str:
    """Escape Slack mrkdwn special characters to neutralize mentions and markup.

    Per Slack's formatting spec, replacing `&`, `<`, `>` with entities is
    sufficient: all special sequences (`<!channel>`, `<@U...>`, `<url|text>`)
    start with `<`, so escaping it disables them entirely.
    """
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class SlackClient:
    """Client for sending daily report notifications via Slack Incoming Webhook."""

    def __init__(self, webhook_url: str) -> None:
        """Initialize the client with a Slack webhook URL.

        Args:
            webhook_url: Slack Incoming Webhook URL
        """
        self.client = WebhookClient(webhook_url)
        self._blocks: list[dict] = []
        self._fallback_parts: list[str] = []

    def notify(
        self,
        target_date: datetime,
        report: ReportSummary,
        pages: list[tuple[str, str]],
        skipped_repos: list[str] | None = None,
    ) -> None:
        """Buffer a daily report notification.

        Args:
            target_date: The target date for the report
            report: Full report summary from Claude API
            pages: List of (repo_name, page_url) tuples
            skipped_repos: Repo names that were in the summary but skipped
                during Notion page creation
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
                # Collapse newlines so a multi-line headline cannot break
                # the one-line-per-repo layout of the Slack section.
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
        """Buffer a no-activity notification.

        Args:
            target_date: The target date with no activity
        """
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
        """Buffer a validation error notification.

        Args:
            target_date: The target date for the report
            result: Validation result containing invalid values
        """
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
        """Buffer an error notification.

        Args:
            target_date: The target date for the report
            error: The exception that occurred
        """
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
        """Buffer execution metrics.

        Args:
            elapsed: Elapsed wall-clock time in seconds
            peak_memory_mb: Peak RSS memory usage in MB
            version: ayumy version string (e.g. "0.2.1")
            memory_limit_mb: Lambda memory limit in MB, or None for CLI
            timeout_seconds: Lambda timeout in seconds, or None for CLI
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
        """Send a message via Slack webhook (best-effort).

        Args:
            text: Fallback text for notifications and accessibility
            blocks: Block Kit blocks for rich formatting
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
