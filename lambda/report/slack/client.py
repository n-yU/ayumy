"""Slack notification client."""

import logging
from collections import defaultdict
from datetime import datetime

import slack_sdk
import slack_sdk.errors

from .. import cost
from ..domain import summary
from ..shared import dates
from ..shared.notice import Notice
from ..summarizer import ValidationResult
from .blocks import (
    SECTION_TEXT_MAX,
    chunk_lines,
    context_block,
    divider,
    escape_mrkdwn,
    header_block,
    mom_suffix,
    run_labels,
    section_block,
    to_mrkdwn,
    truncate_headline,
)

logger = logging.getLogger(__name__)

BLOCKS_MAX = 50  # Slack API limit itself; no margin needed unlike SECTION_TEXT_MAX


def _chunk_blocks(blocks: list[dict], limit: int = BLOCKS_MAX) -> list[list[dict]]:
    return [blocks[i : i + limit] for i in range(0, len(blocks), limit)]


def _pack_messages(
    groups: list[list[dict]], fallbacks: list[str], limit: int = BLOCKS_MAX
) -> list[tuple[list[dict], str]]:
    """Pack block groups into messages of at most `limit` blocks, keeping each group whole so one date's report never straddles two messages."""
    messages: list[tuple[list[dict], str]] = []
    current: list[dict] = []
    current_fallbacks: list[str] = []
    for blocks, fallback in zip(groups, fallbacks, strict=True):
        added = len(blocks) + (1 if current else 0)
        if current and len(current) + added > limit:
            messages.append((current, " | ".join(current_fallbacks)))
            current = list(blocks)
            current_fallbacks = [fallback]
        else:
            if current:
                current.append(divider())
            current.extend(blocks)
            current_fallbacks.append(fallback)
    if current:
        messages.append((current, " | ".join(current_fallbacks)))
    return messages


class Client:
    """Client for sending daily report notifications via Slack chat.postMessage."""

    def __init__(self, token: str, channel: str, *, is_manual: bool = False) -> None:
        self.client = slack_sdk.WebClient(token=token)
        self.channel = channel
        self.is_manual = is_manual
        self._groups: list[list[dict]] = []
        self._fallback_parts: list[str] = []
        self._delivery_failed = False
        self.parent_ts: str | None = None

    def _append_group(self, blocks: list[dict], fallback: str) -> None:
        self._groups.append(blocks)
        self._fallback_parts.append(fallback)

    def _append_report_section(
        self,
        emoji: str,
        date_str: str,
        body_text: str,
        fallback_suffix: str,
        session_only_repos: list[str] | None = None,
        is_backfill: bool = False,
    ) -> None:
        title = (
            f"{emoji} Daily Report ({date_str})"
            f"{run_labels(self.is_manual, is_backfill)}"
        )
        blocks = [header_block(title), section_block(body_text)]
        if session_only_repos:
            blocks.append(
                context_block(f"📓 Session-only: {', '.join(session_only_repos)}")
            )
        self._append_group(blocks, f"{title}: {fallback_suffix}")

    def notify(
        self,
        target_date: datetime,
        report: summary.Report,
        pages: list[tuple[str, str]],
        session_only_repos: list[str] | None = None,
        *,
        owner: str,
        is_backfill: bool = False,
    ) -> None:
        """Queue the daily report section for `target_date`.

        `session_only_repos` are repos that had Claude Code sessions but no GitHub activity; they were excluded from the Claude summary input and get surfaced here as a context row.
        `owner` resolves the number references in each headline to GitHub URLs.
        """
        date_str = target_date.astimezone(dates.JST).strftime("%Y-%m-%d")

        if pages:
            repo_map = {r["name"]: r for r in report["repositories"]}
            page_lines = []
            for name, url in pages:
                repo = repo_map.get(name)
                raw_headline = repo["summary"][0] if repo and repo["summary"] else ""
                # Collapse newlines so a multi-line headline cannot break the one-line-per-repo layout of the Slack section.
                raw_headline = raw_headline.replace("\n", " ").replace("\r", " ")
                headline = to_mrkdwn(truncate_headline(raw_headline), owner, name)
                link = f"<{url}|{date_str}: {name}>"
                page_lines.append(f"{link} — {headline}" if headline else link)

            self._append_report_section(
                "📝",
                date_str,
                "\n".join(page_lines),
                f"{len(pages)} page(s) created",
                session_only_repos=session_only_repos,
                is_backfill=is_backfill,
            )
        else:
            self._append_report_section(
                "✅",
                date_str,
                "No pages created",
                "No pages created",
                session_only_repos=session_only_repos,
                is_backfill=is_backfill,
            )

    def notify_no_activity(
        self, target_date: datetime, *, is_backfill: bool = False
    ) -> None:
        date_str = target_date.astimezone(dates.JST).strftime("%Y-%m-%d")
        self._append_report_section(
            "💤", date_str, "No activity", "No activity", is_backfill=is_backfill
        )

    def notify_session_only(
        self,
        target_date: datetime,
        session_only_repos: list[str],
        *,
        is_backfill: bool = False,
    ) -> None:
        """Send when every repo on this date is session-only; Claude summary is skipped and no Notion pages exist."""
        date_str = target_date.astimezone(dates.JST).strftime("%Y-%m-%d")
        repos_text = ", ".join(session_only_repos)
        body = f"Session-only: {repos_text}"
        self._append_report_section(
            "📓",
            date_str,
            body,
            f"Session-only: {repos_text}",
            is_backfill=is_backfill,
        )

    def notify_validation_errors(
        self,
        target_date: datetime,
        result: ValidationResult,
        *,
        is_backfill: bool = False,
    ) -> None:
        date_str = target_date.astimezone(dates.JST).strftime("%Y-%m-%d")
        lines = [f"• {name}: tags={tags}" for name, tags in result.invalid_tags.items()]
        body = f"Invalid tags detected\n{'\n'.join(lines)}"
        self._append_report_section(
            "⚠️", date_str, body, "Invalid tags detected", is_backfill=is_backfill
        )

    def notify_error(
        self, target_date: datetime, error: Exception, *, is_backfill: bool = False
    ) -> None:
        date_str = target_date.astimezone(dates.JST).strftime("%Y-%m-%d")
        self._append_report_section(
            "❌", date_str, str(error), str(error), is_backfill=is_backfill
        )

    def notify_timeout(
        self, target_date: datetime, reason: str, *, is_backfill: bool = False
    ) -> None:
        """Send when the run stops itself short of the Lambda timeout, which leaves this date and any later ones unreported."""
        date_str = target_date.astimezone(dates.JST).strftime("%Y-%m-%d")
        body = (
            f"Aborted before timeout ({reason})\n"
            "Unreported dates are picked up by a later run"
        )
        self._append_report_section(
            "⏱️", date_str, body, "Aborted before timeout", is_backfill=is_backfill
        )

    def notify_metrics(
        self,
        elapsed: float,
        peak_memory_mb: float,
        version: str,
        cost_display: cost.Display | None = None,
        memory_limit_mb: int | None = None,
        timeout_seconds: int | None = None,
    ) -> None:
        """Queue the execution metrics section; `memory_limit_mb` / `timeout_seconds` are None from CLI, and ratios against limits are then omitted from the rendered text."""
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

        parts = [f"🔖 v{version}", f"⏱️ {elapsed_text}", f"💾 {memory_text}"]
        fallback = f"📊 Execution Metrics: v{version}, {elapsed_text}, {memory_text}"
        if cost_display is not None:
            run_text = f"🧾 ${cost_display.current_run_spend_usd:.4f}"
            mtd_text = f"💰 MTD ${cost_display.monthly_spend_usd:.2f}{mom_suffix(cost_display.spend_change_pct)}"
            calls_text = f"🔁 {cost_display.monthly_call_count} calls{mom_suffix(cost_display.call_count_change_pct)}"
            parts.extend([run_text, mtd_text, calls_text])
            fallback += (
                f", run ${cost_display.current_run_spend_usd:.4f}"
                f", MTD ${cost_display.monthly_spend_usd:.2f}{mom_suffix(cost_display.spend_change_pct)}"
                f", {cost_display.monthly_call_count} calls{mom_suffix(cost_display.call_count_change_pct)}"
            )

        self._append_group([context_block("  |  ".join(parts))], fallback)

    def has_pending(self) -> bool:
        return bool(self._groups)

    def has_delivery_failure(self) -> bool:
        """Return whether any send was dropped, since queueing a notification is no proof that Slack received it."""
        return self._delivery_failed

    def flush(self) -> None:
        for blocks, fallback in _pack_messages(self._groups, self._fallback_parts):
            self._send(fallback, blocks)
        self._groups = []
        self._fallback_parts = []

    def send_notice_thread(self, notice: Notice) -> None:
        """Post the warning digest as a reply to `parent_ts`, or as its own message when no report was sent.

        A top-level send sets `parent_ts`, so chunks after the first thread under the digest itself.
        """
        if not notice:
            return
        grouped: dict[str, list[str]] = defaultdict(list)
        for entry in notice.entries():
            line = f"• {escape_mrkdwn(entry.title)}"
            if entry.details:
                detail_str = ", ".join(
                    f"{k}={escape_mrkdwn(v)}" for k, v in entry.details.items()
                )
                line += f"  `{detail_str}`"
            grouped[entry.source].append(line)
        total = len(notice)
        blocks: list[dict] = [header_block(f"⚠️ Warnings ({total})")]
        for source, lines in grouped.items():
            header = f"*{source}* ({len(lines)})"
            for chunk in chunk_lines(lines, SECTION_TEXT_MAX - len(header) - 1):
                blocks.append(section_block(f"{header}\n{chunk}"))
        fallback = f"⚠️ {total} warning(s) emitted"
        for chunk in _chunk_blocks(blocks):
            self._send(fallback, chunk, thread_ts=self.parent_ts)

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
            if thread_ts is None:
                # Warnings thread under the latest top-level message, which carries the execution metrics when a report was sent
                self.parent_ts = response.get("ts")
        except slack_sdk.errors.SlackClientError as e:
            # Broad within Slack SDK errors: best-effort notification must not abort the pipeline
            logger.exception("Failed to send Slack notification: %r", e)
            self._delivery_failed = True
