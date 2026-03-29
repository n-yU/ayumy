"""Slack notification client."""

import logging
from datetime import datetime

logger = logging.getLogger(__name__)

from slack_sdk.webhook import WebhookClient

from . import JST, ReportSummary
from .summarizer import ValidationResult


class SlackClient:
    """Client for sending daily report notifications via Slack Incoming Webhook."""

    def __init__(self, webhook_url: str) -> None:
        """Initialize the client with a Slack webhook URL.

        Args:
            webhook_url: Slack Incoming Webhook URL
        """
        self.client = WebhookClient(webhook_url)

    def notify(
        self,
        target_date: datetime,
        report: ReportSummary,
        pages: list[tuple[str, str]],
        skipped_repos: list[str] | None = None,
    ) -> None:
        """Send a daily report notification to Slack.

        Sends the overall summary and links to created Notion pages.
        Failures are logged to stderr and do not raise exceptions.

        Args:
            target_date: The target date for the report
            report: Full report summary from Claude API
            pages: List of (repo_name, page_url) tuples
            skipped_repos: Repo names that were in the summary but skipped
                during Notion page creation
        """
        date_str = target_date.astimezone(JST).strftime("%Y-%m-%d")

        if pages:
            page_lines = "\n".join(f"• {name}: {url}" for name, url in pages)
            text = (
                f"📝 Daily Report ({date_str})\n\n"
                f"{report['summary']}\n\n"
                f"📄 Notion Pages\n{page_lines}"
            )
        else:
            text = f"✅ Daily Report ({date_str}): No pages created"

        if skipped_repos:
            skipped = ", ".join(skipped_repos)
            text += f"\n\n⚠️ Skipped: {skipped}"

        self._send(text)

    def notify_validation_errors(
        self, target_date: datetime, result: ValidationResult,
    ) -> None:
        """Send a notification about invalid tags/status values.

        Args:
            target_date: The target date for the report
            result: Validation result containing invalid values
        """
        date_str = target_date.astimezone(JST).strftime("%Y-%m-%d")
        lines = [f"⚠️ Daily Report ({date_str}): Invalid tags/status detected"]

        for name, tags in result.invalid_tags.items():
            lines.append(f"• {name}: tags={tags}")
        for name, status in result.invalid_statuses.items():
            lines.append(f"• {name}: status={status}")

        self._send("\n".join(lines))

    def notify_error(self, target_date: datetime, error: Exception) -> None:
        """Send an error notification to Slack.

        Args:
            target_date: The target date for the report
            error: The exception that occurred
        """
        date_str = target_date.astimezone(JST).strftime("%Y-%m-%d")
        text = f"❌ Daily Report ({date_str}): {error}"
        self._send(text)

    def _send(self, text: str) -> None:
        """Send a message via Slack webhook (best-effort).

        Args:
            text: Message text to send
        """
        try:
            response = self.client.send(text=text)
            if response.status_code != 200:
                logger.error(
                    "Failed to send Slack notification: status=%d, body=%s",
                    response.status_code, response.body,
                )
        except Exception as e:
            logger.error("Failed to send Slack notification: %r", e)
