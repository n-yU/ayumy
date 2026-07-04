"""Tests for SlackClient notification formatting."""

from datetime import datetime
from unittest.mock import MagicMock

import pytest
from slack_sdk.errors import SlackApiError

from config import CONFIG
from report import JST
from report.cost import CostDisplay
from report.notice import Notice, NoticeSource
from report.slack import (
    SECTION_TEXT_MAX,
    SlackClient,
    _context_block,
    _divider,
    _escape_mrkdwn,
    _header_block,
    _section_block,
)
from report.summarizer import ValidationResult


def _make_client():
    client = SlackClient.__new__(SlackClient)
    client.client = MagicMock()
    client.client.chat_postMessage.return_value = {
        "ok": True,
        "ts": "1700000000.000100",
    }
    client.channel = "C0TEST"
    client._blocks = []
    client._fallback_parts = []
    client.parent_ts = None
    return client


def _get_send_kwargs(client):
    return client.client.chat_postMessage.call_args.kwargs


def _blocks_text(blocks):
    parts = []
    for block in blocks:
        if "text" in block and isinstance(block["text"], dict):
            parts.append(block["text"].get("text", ""))
        if "fields" in block:
            for field in block["fields"]:
                parts.append(field.get("text", ""))
        if "elements" in block:
            for el in block["elements"]:
                parts.append(el.get("text", ""))
    return "\n".join(parts)


def _repo(name, summary=None):
    return {
        "name": name,
        "summary": summary or [],
        "achievements": [],
        "ongoing": [],
        "claude_code": "",
        "tags": [],
    }


class TestNotify:
    def test_sends_report_with_pages(self):
        client = _make_client()
        target = datetime(2026, 3, 28, 0, 0, tzinfo=JST)
        report = {"repositories": [_repo("my-repo", ["主要な作業を実施"])]}
        pages = [("my-repo", "https://notion.so/page1")]

        client.notify(target, report, pages)
        client.flush()

        kwargs = _get_send_kwargs(client)
        blocks = kwargs["blocks"]
        text = _blocks_text(blocks)
        assert "2026-03-28" in text
        assert (
            "<https://notion.so/page1|2026-03-28: my-repo> — 主要な作業を実施" in text
        )
        assert kwargs["text"]  # fallback text exists

    def test_sends_no_pages_message(self):
        client = _make_client()
        target = datetime(2026, 3, 28, 0, 0, tzinfo=JST)
        report = {"repositories": []}

        client.notify(target, report, [])
        client.flush()

        kwargs = _get_send_kwargs(client)
        text = _blocks_text(kwargs["blocks"])
        assert "No pages created" in text

    def test_includes_skipped_repos(self):
        client = _make_client()
        target = datetime(2026, 3, 28, 0, 0, tzinfo=JST)
        report = {"repositories": [_repo("repo", ["headline"])]}
        pages = [("repo", "https://notion.so/p")]

        client.notify(target, report, pages, skipped_repos=["unknown-repo"])
        client.flush()

        kwargs = _get_send_kwargs(client)
        text = _blocks_text(kwargs["blocks"])
        assert "unknown-repo" in text

    def test_skipped_repos_with_no_pages(self):
        client = _make_client()
        target = datetime(2026, 3, 28, 0, 0, tzinfo=JST)
        report = {"repositories": []}

        client.notify(target, report, [], skipped_repos=["unknown-repo"])
        client.flush()

        kwargs = _get_send_kwargs(client)
        text = _blocks_text(kwargs["blocks"])
        assert "unknown-repo" in text

    def test_block_structure(self):
        client = _make_client()
        target = datetime(2026, 3, 28, 0, 0, tzinfo=JST)
        report = {"repositories": [_repo("repo", ["h"])]}
        pages = [("repo", "https://notion.so/p")]

        client.notify(target, report, pages, skipped_repos=["skipped"])
        client.flush()

        blocks = _get_send_kwargs(client)["blocks"]
        assert blocks[0]["type"] == "header"
        assert blocks[1]["type"] == "section"  # page links with headlines
        assert "text" in blocks[1]
        assert "fields" not in blocks[1]
        assert blocks[2]["type"] == "context"  # skipped

    def test_page_line_omits_dash_when_no_headline(self):
        client = _make_client()
        target = datetime(2026, 3, 28, 0, 0, tzinfo=JST)
        report = {"repositories": [_repo("repo")]}
        pages = [("repo", "https://notion.so/p")]

        client.notify(target, report, pages)
        client.flush()

        blocks = _get_send_kwargs(client)["blocks"]
        page_text = blocks[1]["text"]["text"]
        assert page_text == "<https://notion.so/p|2026-03-28: repo>"

    def test_multiple_pages_listed_with_headlines(self):
        client = _make_client()
        target = datetime(2026, 3, 28, 0, 0, tzinfo=JST)
        report = {
            "repositories": [
                _repo("a", ["first headline"]),
                _repo("b", ["second headline"]),
            ]
        }
        pages = [
            ("a", "https://notion.so/a"),
            ("b", "https://notion.so/b"),
        ]

        client.notify(target, report, pages)
        client.flush()

        blocks = _get_send_kwargs(client)["blocks"]
        page_text = blocks[1]["text"]["text"]
        lines = page_text.split("\n")
        assert len(lines) == 2
        assert "a> — first headline" in lines[0]
        assert "b> — second headline" in lines[1]

    def test_long_headline_is_truncated(self):
        client = _make_client()
        target = datetime(2026, 3, 28, 0, 0, tzinfo=JST)
        long_headline = "あ" * (CONFIG.slack.headline_max + 50)
        report = {"repositories": [_repo("repo", [long_headline])]}
        pages = [("repo", "https://notion.so/p")]

        client.notify(target, report, pages)
        client.flush()

        page_text = _get_send_kwargs(client)["blocks"][1]["text"]["text"]
        # headline portion after " — " should be truncated to CONFIG.slack.headline_max
        headline_part = page_text.split(" — ", 1)[1]
        assert len(headline_part) == CONFIG.slack.headline_max
        assert headline_part.endswith("…")

    def test_headline_newlines_are_collapsed(self):
        client = _make_client()
        target = datetime(2026, 3, 28, 0, 0, tzinfo=JST)
        headline = "first line\nsecond line\rthird line"
        report = {"repositories": [_repo("repo", [headline])]}
        pages = [("repo", "https://notion.so/p")]

        client.notify(target, report, pages)
        client.flush()

        page_text = _get_send_kwargs(client)["blocks"][1]["text"]["text"]
        # Only one rendered line (page link + headline), no stray newlines
        # from the headline itself.
        assert page_text.count("\n") == 0
        assert "first line second line third line" in page_text

    def test_headline_special_chars_are_escaped(self):
        client = _make_client()
        target = datetime(2026, 3, 28, 0, 0, tzinfo=JST)
        headline = "fix <!channel> & <T> generic leak"
        report = {"repositories": [_repo("repo", [headline])]}
        pages = [("repo", "https://notion.so/p")]

        client.notify(target, report, pages)
        client.flush()

        page_text = _get_send_kwargs(client)["blocks"][1]["text"]["text"]
        headline_part = page_text.split(" — ", 1)[1]
        # Raw special sequences must not reach Slack as-is
        assert "<!channel>" not in headline_part
        assert "<T>" not in headline_part
        # Escaped entities should be present instead
        assert "&lt;!channel&gt;" in headline_part
        assert "&amp;" in headline_part
        assert "&lt;T&gt;" in headline_part


class TestEscapeMrkdwn:
    def test_escapes_ampersand_and_angle_brackets(self):
        assert _escape_mrkdwn("a & b") == "a &amp; b"
        assert _escape_mrkdwn("<!channel>") == "&lt;!channel&gt;"
        assert _escape_mrkdwn("<@U123>") == "&lt;@U123&gt;"

    def test_passes_plain_text_through(self):
        assert _escape_mrkdwn("plain text 日本語") == "plain text 日本語"


class TestBlockPrimitives:
    def test_divider(self):
        assert _divider() == {"type": "divider"}

    def test_header_block(self):
        assert _header_block("📝 Daily Report (2026-03-28)") == {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "📝 Daily Report (2026-03-28)",
            },
        }

    def test_section_block(self):
        assert _section_block("hello") == {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "hello"},
        }

    def test_context_block(self):
        assert _context_block("ctx") == {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": "ctx"}],
        }


class TestAppendWithDivider:
    def test_appends_blocks_without_divider_when_buffer_empty(self):
        client = _make_client()
        block = _section_block("first")

        client._append_with_divider(block)

        assert client._blocks == [block]

    def test_prepends_divider_when_buffer_has_content(self):
        client = _make_client()
        first = _section_block("first")
        second = _section_block("second")
        client._blocks.append(first)

        client._append_with_divider(second)

        assert client._blocks == [first, _divider(), second]

    def test_appends_multiple_blocks_after_divider(self):
        client = _make_client()
        client._blocks.append(_section_block("existing"))
        header = _header_block("📝 Daily Report (2026-03-28)")
        section = _section_block("body")

        client._append_with_divider(header, section)

        assert client._blocks[-3] == _divider()
        assert client._blocks[-2] == header
        assert client._blocks[-1] == section


class TestNotifyNoActivity:
    def test_sends_no_activity_message(self):
        client = _make_client()
        target = datetime(2026, 3, 28, 0, 0, tzinfo=JST)

        client.notify_no_activity(target)
        client.flush()

        kwargs = _get_send_kwargs(client)
        text = _blocks_text(kwargs["blocks"])
        assert "2026-03-28" in text
        assert "No activity" in text
        assert kwargs["text"]

    def test_block_structure(self):
        client = _make_client()
        target = datetime(2026, 3, 28, 0, 0, tzinfo=JST)

        client.notify_no_activity(target)
        client.flush()

        blocks = _get_send_kwargs(client)["blocks"]
        assert blocks[0]["type"] == "header"
        assert "💤" in blocks[0]["text"]["text"]
        assert blocks[1]["type"] == "section"


class TestNotifyMetrics:
    def test_sends_metrics_with_memory_limit(self):
        client = _make_client()

        client.notify_metrics(12.5, 128.0, "0.2.1", memory_limit_mb=256)
        client.flush()

        kwargs = _get_send_kwargs(client)
        text = _blocks_text(kwargs["blocks"])
        assert "12.5s" in text
        assert "128 / 256 MB" in text
        assert "50%" in text

    def test_sends_metrics_without_memory_limit(self):
        client = _make_client()

        client.notify_metrics(5.3, 64.0, "0.2.1")
        client.flush()

        kwargs = _get_send_kwargs(client)
        text = _blocks_text(kwargs["blocks"])
        assert "5.3s" in text
        assert "64 MB" in text
        # No percent indicator when neither memory nor timeout limits are set
        assert "%" not in text

    def test_block_structure(self):
        client = _make_client()

        client.notify_metrics(10.0, 100.0, "0.2.1", memory_limit_mb=512)
        client.flush()

        blocks = _get_send_kwargs(client)["blocks"]
        assert blocks[0]["type"] == "context"

    def test_includes_version(self):
        client = _make_client()

        client.notify_metrics(10.0, 100.0, "0.2.1", memory_limit_mb=512)
        client.flush()

        kwargs = _get_send_kwargs(client)
        text = _blocks_text(kwargs["blocks"])
        assert "🔖 v0.2.1" in text

    def test_includes_timeout_when_provided(self):
        client = _make_client()

        client.notify_metrics(
            45.0,
            100.0,
            "0.2.1",
            memory_limit_mb=512,
            timeout_seconds=300,
        )
        client.flush()

        kwargs = _get_send_kwargs(client)
        text = _blocks_text(kwargs["blocks"])
        assert "45.0 / 300s" in text
        assert "15%" in text

    def test_omits_timeout_when_not_provided(self):
        client = _make_client()

        client.notify_metrics(45.0, 100.0, "0.2.1", memory_limit_mb=512)
        client.flush()

        kwargs = _get_send_kwargs(client)
        text = _blocks_text(kwargs["blocks"])
        assert "45.0s" in text
        assert "/ 300s" not in text

    def test_includes_all_fields_together(self):
        client = _make_client()

        client.notify_metrics(
            45.0,
            128.0,
            "0.2.1",
            memory_limit_mb=256,
            timeout_seconds=300,
        )
        client.flush()

        kwargs = _get_send_kwargs(client)
        text = _blocks_text(kwargs["blocks"])
        assert "🔖 v0.2.1" in text
        assert "⏱️ 45.0 / 300s (15%)" in text
        assert "💾 128 / 256 MB (50%)" in text

    def test_fallback_text_includes_version_and_timeout(self):
        client = _make_client()

        client.notify_metrics(
            45.0,
            128.0,
            "0.2.1",
            memory_limit_mb=256,
            timeout_seconds=300,
        )
        client.flush()

        # `text` kwarg passed to chat_postMessage() carries the fallback string
        fallback = client.client.chat_postMessage.call_args.kwargs["text"]
        assert "v0.2.1" in fallback
        assert "45.0 / 300s (15%)" in fallback
        assert "128 / 256 MB (50%)" in fallback


class TestNotifyMetricsWithCost:
    def setup_method(self):
        self.client = _make_client()
        self.cost = CostDisplay(
            current_run_spend_usd=0.0340,
            monthly_spend_usd=1.23,
            spend_change_pct=8.0,
            monthly_report_count=12,
            report_count_change_pct=5.0,
        )

    def test_block_contains_cost_metrics(self):
        self.client.notify_metrics(1.0, 100.0, "0.3.2", cost=self.cost)
        self.client.flush()

        text = _blocks_text(_get_send_kwargs(self.client)["blocks"])
        assert "🧾 $0.0340" in text
        assert "💰 MTD $1.23 (MoM +8%)" in text
        assert "🔁 12 reports (MoM +5%)" in text

    def test_none_change_pct_omits_mom_fragment(self):
        cost = CostDisplay(
            current_run_spend_usd=0.0340,
            monthly_spend_usd=1.23,
            spend_change_pct=None,
            monthly_report_count=12,
            report_count_change_pct=None,
        )

        self.client.notify_metrics(1.0, 100.0, "0.3.2", cost=cost)
        self.client.flush()

        text = _blocks_text(_get_send_kwargs(self.client)["blocks"])
        assert "MoM" not in text
        assert "MTD $1.23" in text
        assert "12 reports" in text

    def test_zero_change_pct_still_rendered(self):
        cost = CostDisplay(
            current_run_spend_usd=0.0340,
            monthly_spend_usd=1.23,
            spend_change_pct=0.0,
            monthly_report_count=12,
            report_count_change_pct=0.0,
        )

        self.client.notify_metrics(1.0, 100.0, "0.3.2", cost=cost)
        self.client.flush()

        text = _blocks_text(_get_send_kwargs(self.client)["blocks"])
        assert "MoM +0%" in text

    def test_metrics_and_cost_share_single_context_block(self):
        self.client.notify_metrics(1.0, 100.0, "0.3.2", cost=self.cost)
        self.client.flush()

        blocks = _get_send_kwargs(self.client)["blocks"]
        assert [b["type"] for b in blocks] == ["context"]
        # All metrics and cost fields share the same caption line
        caption = blocks[0]["elements"][0]["text"]
        assert "🔖 v0.3.2" in caption
        assert "🧾 $0.0340" in caption
        assert "💰 MTD" in caption

    def test_fallback_text_includes_cost(self):
        self.client.notify_metrics(1.0, 100.0, "0.3.2", cost=self.cost)
        self.client.flush()

        fallback = self.client.client.chat_postMessage.call_args.kwargs["text"]
        assert "run $0.0340" in fallback
        assert "MTD $1.23 (MoM +8%)" in fallback

    def test_cost_is_omitted_when_none(self):
        self.client.notify_metrics(1.0, 100.0, "0.3.2")
        self.client.flush()

        text = _blocks_text(_get_send_kwargs(self.client)["blocks"])
        assert "🧾" not in text
        assert "💰" not in text
        assert "🔁" not in text


class TestNotifyValidationErrors:
    def test_sends_invalid_tags(self):
        client = _make_client()
        target = datetime(2026, 3, 28, 0, 0, tzinfo=JST)
        result = ValidationResult()
        result.invalid_tags = {"repo": ["BadTag"]}

        client.notify_validation_errors(target, result)
        client.flush()

        kwargs = _get_send_kwargs(client)
        text = _blocks_text(kwargs["blocks"])
        assert "BadTag" in text

    def test_block_structure(self):
        client = _make_client()
        target = datetime(2026, 3, 28, 0, 0, tzinfo=JST)
        result = ValidationResult()
        result.invalid_tags = {"repo": ["BadTag"]}

        client.notify_validation_errors(target, result)
        client.flush()

        blocks = _get_send_kwargs(client)["blocks"]
        assert blocks[0]["type"] == "header"
        assert "⚠️" in blocks[0]["text"]["text"]
        assert blocks[1]["type"] == "section"


class TestNotifyError:
    def test_sends_error_message(self):
        client = _make_client()
        target = datetime(2026, 3, 28, 0, 0, tzinfo=JST)

        client.notify_error(target, RuntimeError("something went wrong"))
        client.flush()

        kwargs = _get_send_kwargs(client)
        text = _blocks_text(kwargs["blocks"])
        assert "something went wrong" in text

    def test_block_structure(self):
        client = _make_client()
        target = datetime(2026, 3, 28, 0, 0, tzinfo=JST)

        client.notify_error(target, RuntimeError("fail"))
        client.flush()

        blocks = _get_send_kwargs(client)["blocks"]
        assert blocks[0]["type"] == "header"
        assert "❌" in blocks[0]["text"]["text"]
        assert blocks[1]["type"] == "section"


class TestFlush:
    def setup_method(self):
        self.client = _make_client()
        self.target = datetime(2026, 3, 28, 0, 0, tzinfo=JST)

    def test_sends_combined_message(self):
        report = {"summary": "summary", "repositories": []}
        pages = [("repo", "https://notion.so/p")]

        self.client.notify(self.target, report, pages)
        self.client.notify_metrics(10.0, 100.0, "0.2.1", memory_limit_mb=512)
        self.client.flush()

        # Single send call
        assert self.client.client.chat_postMessage.call_count == 1
        blocks = _get_send_kwargs(self.client)["blocks"]
        text = _blocks_text(blocks)
        assert "Daily Report" in text
        assert "10.0s" in text
        # Divider between sections
        assert any(b["type"] == "divider" for b in blocks)

    def test_does_not_send_when_empty(self):
        self.client.flush()

        self.client.client.chat_postMessage.assert_not_called()

    def test_clears_buffer_after_flush(self):
        self.client.notify_error(self.target, RuntimeError("fail"))
        self.client.flush()
        self.client.flush()

        assert self.client.client.chat_postMessage.call_count == 1

    def test_captures_parent_ts_after_send(self):
        self.client.notify_error(self.target, RuntimeError("fail"))
        self.client.flush()

        assert self.client.parent_ts == "1700000000.000100"

    def test_parent_ts_not_overwritten_on_subsequent_sends(self):
        self.client.notify_error(self.target, RuntimeError("first"))
        self.client.flush()
        initial_ts = self.client.parent_ts

        self.client.client.chat_postMessage.return_value = {
            "ok": True,
            "ts": "1800000000.000200",
        }
        self.client.notify_error(self.target, RuntimeError("second"))
        self.client.flush()

        assert self.client.parent_ts == initial_ts

    def test_sends_to_configured_channel(self):
        self.client.notify_error(self.target, RuntimeError("fail"))
        self.client.flush()

        kwargs = _get_send_kwargs(self.client)
        assert kwargs["channel"] == "C0TEST"

    def test_suppresses_slack_sdk_errors(self):
        self.client.client.chat_postMessage.side_effect = SlackApiError(
            "rate_limited", response={"error": "rate_limited"}
        )
        self.client.notify_error(self.target, RuntimeError("fail"))

        self.client.flush()

    def test_propagates_unrelated_errors(self):
        self.client.client.chat_postMessage.side_effect = RuntimeError("boom")
        self.client.notify_error(self.target, RuntimeError("fail"))

        with pytest.raises(RuntimeError, match="boom"):
            self.client.flush()


class TestSendNoticeThread:
    def setup_method(self):
        self.client = _make_client()
        self.client.parent_ts = "1700000000.000100"

    def test_does_not_send_when_notice_empty(self):
        self.client.send_notice_thread(Notice())

        self.client.client.chat_postMessage.assert_not_called()

    def test_does_not_send_when_parent_ts_missing(self):
        self.client.parent_ts = None
        notice = Notice()
        notice.add(NoticeSource.SESSION, "Malformed JSONL line skipped", key="x")

        self.client.send_notice_thread(notice)

        self.client.client.chat_postMessage.assert_not_called()

    def test_posts_as_thread_reply(self):
        notice = Notice()
        notice.add(NoticeSource.GITHUB, "Commit not found (404)", sha="abc1234")

        self.client.send_notice_thread(notice)

        kwargs = _get_send_kwargs(self.client)
        assert kwargs["thread_ts"] == "1700000000.000100"
        assert kwargs["channel"] == "C0TEST"

    def test_groups_entries_by_source(self):
        notice = Notice()
        notice.add(NoticeSource.GITHUB, "Commit not found (404)", sha="abc1234")
        notice.add(NoticeSource.GITHUB, "PR not found (404)", number="42")
        notice.add(NoticeSource.SESSION, "Malformed JSONL line skipped", key="x")

        self.client.send_notice_thread(notice)

        blocks = _get_send_kwargs(self.client)["blocks"]
        text = _blocks_text(blocks)
        assert "Warnings (3)" in text
        assert "*github* (2)" in text
        assert "*session* (1)" in text
        assert "Commit not found (404)" in text
        assert "Malformed JSONL line skipped" in text

    def test_does_not_overwrite_parent_ts(self):
        notice = Notice()
        notice.add(NoticeSource.SESSION, "Malformed JSONL line skipped", key="x")
        self.client.client.chat_postMessage.return_value = {
            "ok": True,
            "ts": "1900000000.000300",
        }

        self.client.send_notice_thread(notice)

        assert self.client.parent_ts == "1700000000.000100"

    def test_splits_long_source_into_multiple_sections(self):
        notice = Notice()
        # Each line is ~100 chars; 60 lines exceed the section text cap and force splitting
        long_title = "Malformed JSONL line skipped " + "x" * 70
        for i in range(60):
            notice.add(NoticeSource.SESSION, long_title, key=f"k{i}")

        self.client.send_notice_thread(notice)

        blocks = _get_send_kwargs(self.client)["blocks"]
        section_blocks = [b for b in blocks if b["type"] == "section"]
        assert len(section_blocks) >= 2
        for block in section_blocks:
            text = block["text"]["text"]
            assert len(text) <= SECTION_TEXT_MAX
            assert text.startswith("*session*")

    def test_truncates_single_line_exceeding_section_limit(self):
        notice = Notice()
        # Single line longer than SECTION_TEXT_MAX must be truncated to keep the section within the limit
        notice.add(NoticeSource.SESSION, "Oversized warning " + "x" * 4000)

        self.client.send_notice_thread(notice)

        blocks = _get_send_kwargs(self.client)["blocks"]
        section_blocks = [b for b in blocks if b["type"] == "section"]
        assert len(section_blocks) == 1
        text = section_blocks[0]["text"]["text"]
        assert len(text) <= SECTION_TEXT_MAX
        assert text.endswith("…")
