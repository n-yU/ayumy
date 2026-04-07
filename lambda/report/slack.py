"""Slack notification client."""

import logging
from datetime import datetime

from slack_sdk.webhook import WebhookClient

from . import JST, ReportSummary
from .summarizer import ValidationResult

logger = logging.getLogger(__name__)


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
            page_fields = [
                {"type": "mrkdwn", "text": f"<{url}|{date_str}: {name}>"}
                for name, url in pages
            ]
            summary = report["summary"] or " "
            self._blocks.extend([
                {"type": "header", "text": {"type": "plain_text", "text": f"📝 Daily Report ({date_str})"}},
                {"type": "section", "text": {"type": "mrkdwn", "text": summary}},
                {"type": "divider"},
            ])
            # section.fields allows max 10 items
            if len(page_fields) <= 10:
                self._blocks.append({"type": "section", "fields": page_fields})
            else:
                page_lines = "\n".join(f["text"] for f in page_fields)
                self._blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": page_lines}})
            if skipped_repos:
                skipped = ", ".join(skipped_repos)
                self._blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": f"⚠️ Skipped: {skipped}"}]})
            self._fallback_parts.append(f"📝 Daily Report ({date_str}): {len(pages)} page(s) created")
        else:
            self._blocks.extend([
                {"type": "header", "text": {"type": "plain_text", "text": f"✅ Daily Report ({date_str})"}},
                {"type": "section", "text": {"type": "mrkdwn", "text": "No pages created"}},
            ])
            if skipped_repos:
                skipped = ", ".join(skipped_repos)
                self._blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": f"⚠️ Skipped: {skipped}"}]})
            self._fallback_parts.append(f"✅ Daily Report ({date_str}): No pages created")

    def notify_no_activity(self, target_date: datetime) -> None:
        """Buffer a no-activity notification.

        Args:
            target_date: The target date with no activity
        """
        date_str = target_date.astimezone(JST).strftime("%Y-%m-%d")

        if self._blocks:
            self._blocks.append({"type": "divider"})

        self._blocks.extend([
            {"type": "header", "text": {"type": "plain_text", "text": f"💤 Daily Report ({date_str})"}},
            {"type": "section", "text": {"type": "mrkdwn", "text": "No activity"}},
        ])
        self._fallback_parts.append(f"💤 Daily Report ({date_str}): No activity")

    def notify_validation_errors(
        self, target_date: datetime, result: ValidationResult,
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

        self._blocks.extend([
            {"type": "header", "text": {"type": "plain_text", "text": f"⚠️ Daily Report ({date_str})"}},
            {"type": "section", "text": {"type": "mrkdwn", "text": f"Invalid tags detected\n{'\n'.join(lines)}"}},
        ])
        self._fallback_parts.append(f"⚠️ Daily Report ({date_str}): Invalid tags detected")

    def notify_error(self, target_date: datetime, error: Exception) -> None:
        """Buffer an error notification.

        Args:
            target_date: The target date for the report
            error: The exception that occurred
        """
        date_str = target_date.astimezone(JST).strftime("%Y-%m-%d")

        if self._blocks:
            self._blocks.append({"type": "divider"})

        self._blocks.extend([
            {"type": "header", "text": {"type": "plain_text", "text": f"❌ Daily Report ({date_str})"}},
            {"type": "section", "text": {"type": "mrkdwn", "text": str(error)}},
        ])
        self._fallback_parts.append(f"❌ Daily Report ({date_str}): {error}")

    def notify_metrics(
        self, elapsed: float, peak_memory_mb: float,
        memory_limit_mb: int | None = None,
    ) -> None:
        """Buffer execution metrics.

        Args:
            elapsed: Elapsed wall-clock time in seconds
            peak_memory_mb: Peak RSS memory usage in MB
            memory_limit_mb: Lambda memory limit in MB, or None for CLI
        """
        if self._blocks:
            self._blocks.append({"type": "divider"})

        if memory_limit_mb is not None:
            pct = peak_memory_mb / memory_limit_mb * 100
            memory_text = f"Memory: {peak_memory_mb:.0f} / {memory_limit_mb} MB ({pct:.0f}%)"
        else:
            memory_text = f"Memory: {peak_memory_mb:.0f} MB"
        self._blocks.append({
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": f"⏱️ {elapsed:.1f}s  |  💾 {memory_text}"}],
        })
        self._fallback_parts.append(f"📊 Execution Metrics: {elapsed:.1f}s, {peak_memory_mb:.0f}MB")

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
                    response.status_code, response.body,
                )
        except Exception as e:
            logger.exception("Failed to send Slack notification: %r", e)
