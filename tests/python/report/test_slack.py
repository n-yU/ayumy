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
    BLOCKS_MAX,
    SECTION_TEXT_MAX,
    SlackClient,
    _chunk_blocks,
    _context_block,
    _divider,
    _escape_mrkdwn,
    _header_block,
    _pack_messages,
    _section_block,
)
from report.summarizer import ValidationResult

from ._builders import TARGET_DATE, make_stub

CHANNEL = "C0TEST"
FIRST_TS = "1700000000.000100"
SECOND_TS = "1800000000.000200"
PAGE_URL = "https://notion.so/p"
VERSION = "0.2.1"
DAYS_IN_MARCH = 31


@pytest.fixture
def slack_client():
    client = make_stub(
        SlackClient,
        client=MagicMock(),
        channel=CHANNEL,
        _groups=[],
        _fallback_parts=[],
        parent_ts=None,
    )
    client.client.chat_postMessage.return_value = {"ok": True, "ts": FIRST_TS}
    return client


@pytest.fixture
def threaded_client(slack_client):
    """Return a client that already has a parent message, the state notice replies require."""
    slack_client.parent_ts = FIRST_TS
    return slack_client


@pytest.fixture
def cost():
    return CostDisplay(
        current_run_spend_usd=0.0340,
        monthly_spend_usd=1.23,
        spend_change_pct=8.0,
        monthly_call_count=12,
        call_count_change_pct=5.0,
    )


def _get_send_kwargs(client):
    return client.client.chat_postMessage.call_args.kwargs


def _all_send_kwargs(client):
    return [call.kwargs for call in client.client.chat_postMessage.call_args_list]


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


def _queue_every_day(client):
    """Queue one section per day of March, enough groups to exceed the per-message block limit."""
    for day in range(1, DAYS_IN_MARCH + 1):
        client.notify_no_activity(datetime(2026, 3, day, tzinfo=JST))


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
    def test_sends_report_with_pages(self, slack_client):
        report = {"repositories": [_repo("my-repo", ["主要な作業を実施"])]}
        pages = [("my-repo", "https://notion.so/page1")]
        slack_client.notify(TARGET_DATE, report, pages)
        slack_client.flush()

        kwargs = _get_send_kwargs(slack_client)
        text = _blocks_text(kwargs["blocks"])
        assert "2026-03-28" in text
        assert (
            "<https://notion.so/page1|2026-03-28: my-repo> — 主要な作業を実施" in text
        )
        assert kwargs["text"]  # fallback text exists

    def test_sends_no_pages_message(self, slack_client):
        slack_client.notify(TARGET_DATE, {"repositories": []}, [])
        slack_client.flush()

        text = _blocks_text(_get_send_kwargs(slack_client)["blocks"])
        assert "No pages created" in text

    def test_includes_session_only_repos(self, slack_client):
        report = {"repositories": [_repo("repo", ["headline"])]}
        slack_client.notify(
            TARGET_DATE, report, [("repo", PAGE_URL)], session_only_repos=["notes-repo"]
        )
        slack_client.flush()

        text = _blocks_text(_get_send_kwargs(slack_client)["blocks"])
        assert "📓 Session-only: notes-repo" in text

    def test_session_only_repos_with_no_pages(self, slack_client):
        slack_client.notify(
            TARGET_DATE, {"repositories": []}, [], session_only_repos=["notes-repo"]
        )
        slack_client.flush()

        text = _blocks_text(_get_send_kwargs(slack_client)["blocks"])
        assert "📓 Session-only: notes-repo" in text

    def test_block_structure(self, slack_client):
        report = {"repositories": [_repo("repo", ["h"])]}
        slack_client.notify(
            TARGET_DATE, report, [("repo", PAGE_URL)], session_only_repos=["notes-repo"]
        )
        slack_client.flush()

        blocks = _get_send_kwargs(slack_client)["blocks"]
        assert blocks[0]["type"] == "header"
        assert blocks[1]["type"] == "section"  # page links with headlines
        assert "text" in blocks[1]
        assert "fields" not in blocks[1]
        assert blocks[2]["type"] == "context"  # session-only

    def test_page_line_omits_dash_when_no_headline(self, slack_client):
        report = {"repositories": [_repo("repo")]}
        slack_client.notify(TARGET_DATE, report, [("repo", PAGE_URL)])
        slack_client.flush()

        blocks = _get_send_kwargs(slack_client)["blocks"]
        assert blocks[1]["text"]["text"] == f"<{PAGE_URL}|2026-03-28: repo>"

    def test_multiple_pages_listed_with_headlines(self, slack_client):
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
        slack_client.notify(TARGET_DATE, report, pages)
        slack_client.flush()

        page_text = _get_send_kwargs(slack_client)["blocks"][1]["text"]["text"]
        lines = page_text.split("\n")
        assert len(lines) == 2
        assert "a> — first headline" in lines[0]
        assert "b> — second headline" in lines[1]

    def test_long_headline_is_truncated(self, slack_client):
        long_headline = "あ" * (CONFIG.slack.headline_max + 50)
        report = {"repositories": [_repo("repo", [long_headline])]}
        slack_client.notify(TARGET_DATE, report, [("repo", PAGE_URL)])
        slack_client.flush()

        page_text = _get_send_kwargs(slack_client)["blocks"][1]["text"]["text"]
        # headline portion after " — " should be truncated to CONFIG.slack.headline_max
        headline_part = page_text.split(" — ", 1)[1]
        assert len(headline_part) == CONFIG.slack.headline_max
        assert headline_part.endswith("…")

    def test_headline_newlines_are_collapsed(self, slack_client):
        headline = "first line\nsecond line\rthird line"
        report = {"repositories": [_repo("repo", [headline])]}
        slack_client.notify(TARGET_DATE, report, [("repo", PAGE_URL)])
        slack_client.flush()

        page_text = _get_send_kwargs(slack_client)["blocks"][1]["text"]["text"]
        # Only one rendered line (page link + headline), no stray newlines
        # from the headline itself.
        assert page_text.count("\n") == 0
        assert "first line second line third line" in page_text

    def test_headline_special_chars_are_escaped(self, slack_client):
        headline = "fix <!channel> & <T> generic leak"
        report = {"repositories": [_repo("repo", [headline])]}
        slack_client.notify(TARGET_DATE, report, [("repo", PAGE_URL)])
        slack_client.flush()

        page_text = _get_send_kwargs(slack_client)["blocks"][1]["text"]["text"]
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


class TestAppendGroup:
    def test_buffers_blocks_and_fallback_as_one_group(self, slack_client):
        header = _header_block("📝 Daily Report (2026-03-28)")
        section = _section_block("body")
        slack_client._append_group([header, section], "1 page(s) created")

        assert slack_client._groups == [[header, section]]
        assert slack_client._fallback_parts == ["1 page(s) created"]

    def test_keeps_groups_separate(self, slack_client):
        first = _section_block("first")
        second = _section_block("second")
        slack_client._append_group([first], "a")
        slack_client._append_group([second], "b")

        assert slack_client._groups == [[first], [second]]
        assert slack_client._fallback_parts == ["a", "b"]


class TestChunkBlocks:
    def test_returns_single_chunk_within_limit(self):
        blocks = [_section_block(str(i)) for i in range(3)]

        assert _chunk_blocks(blocks, limit=3) == [blocks]

    def test_splits_into_chunks_of_limit(self):
        blocks = [_section_block(str(i)) for i in range(5)]

        assert _chunk_blocks(blocks, limit=2) == [blocks[:2], blocks[2:4], blocks[4:]]


class TestPackMessages:
    def test_returns_nothing_for_empty_buffer(self):
        assert _pack_messages([], []) == []

    def test_joins_groups_with_divider_without_leading_divider(self):
        first = _section_block("first")
        second = _section_block("second")

        messages = _pack_messages([[first], [second]], ["a", "b"])

        assert messages == [([first, _divider(), second], "a | b")]

    def test_starts_new_message_when_limit_exceeded(self):
        groups = [[_section_block(str(i))] for i in range(4)]
        fallbacks = [str(i) for i in range(4)]

        messages = _pack_messages(groups, fallbacks, limit=3)

        assert [len(blocks) for blocks, _ in messages] == [3, 3]
        assert [fallback for _, fallback in messages] == ["0 | 1", "2 | 3"]

    def test_never_splits_a_group_across_messages(self):
        pair = [_header_block("h"), _section_block("s")]
        groups = [list(pair) for _ in range(3)]

        messages = _pack_messages(groups, ["a", "b", "c"], limit=4)

        assert [len(blocks) for blocks, _ in messages] == [2, 2, 2]
        for blocks, _ in messages:
            assert blocks == pair


class TestNotifyNoActivity:
    def test_sends_no_activity_message(self, slack_client):
        slack_client.notify_no_activity(TARGET_DATE)
        slack_client.flush()

        kwargs = _get_send_kwargs(slack_client)
        text = _blocks_text(kwargs["blocks"])
        assert "2026-03-28" in text
        assert "No activity" in text
        assert kwargs["text"]

    def test_block_structure(self, slack_client):
        slack_client.notify_no_activity(TARGET_DATE)
        slack_client.flush()

        blocks = _get_send_kwargs(slack_client)["blocks"]
        assert blocks[0]["type"] == "header"
        assert "💤" in blocks[0]["text"]["text"]
        assert blocks[1]["type"] == "section"


class TestNotifySessionOnly:
    def test_sends_session_only_message(self, slack_client):
        slack_client.notify_session_only(TARGET_DATE, ["repo-a", "repo-b"])
        slack_client.flush()

        kwargs = _get_send_kwargs(slack_client)
        text = _blocks_text(kwargs["blocks"])
        assert "2026-03-28" in text
        assert "Session-only: repo-a, repo-b" in text
        assert kwargs["text"]

    def test_block_structure(self, slack_client):
        slack_client.notify_session_only(TARGET_DATE, ["repo-a"])
        slack_client.flush()

        blocks = _get_send_kwargs(slack_client)["blocks"]
        assert blocks[0]["type"] == "header"
        assert "📓" in blocks[0]["text"]["text"]
        assert blocks[1]["type"] == "section"


class TestNotifyMetrics:
    def test_sends_metrics_with_memory_limit(self, slack_client):
        slack_client.notify_metrics(12.5, 128.0, VERSION, memory_limit_mb=256)
        slack_client.flush()

        text = _blocks_text(_get_send_kwargs(slack_client)["blocks"])
        assert "12.5s" in text
        assert "128 / 256 MB" in text
        assert "50%" in text

    def test_sends_metrics_without_memory_limit(self, slack_client):
        slack_client.notify_metrics(5.3, 64.0, VERSION)
        slack_client.flush()

        text = _blocks_text(_get_send_kwargs(slack_client)["blocks"])
        assert "5.3s" in text
        assert "64 MB" in text
        # No percent indicator when neither memory nor timeout limits are set
        assert "%" not in text

    def test_block_structure(self, slack_client):
        slack_client.notify_metrics(10.0, 100.0, VERSION, memory_limit_mb=512)
        slack_client.flush()

        blocks = _get_send_kwargs(slack_client)["blocks"]
        assert blocks[0]["type"] == "context"

    def test_includes_version(self, slack_client):
        slack_client.notify_metrics(10.0, 100.0, VERSION, memory_limit_mb=512)
        slack_client.flush()

        text = _blocks_text(_get_send_kwargs(slack_client)["blocks"])
        assert f"🔖 v{VERSION}" in text

    def test_includes_timeout_when_provided(self, slack_client):
        slack_client.notify_metrics(
            45.0, 100.0, VERSION, memory_limit_mb=512, timeout_seconds=300
        )
        slack_client.flush()

        text = _blocks_text(_get_send_kwargs(slack_client)["blocks"])
        assert "45.0 / 300s" in text
        assert "15%" in text

    def test_omits_timeout_when_not_provided(self, slack_client):
        slack_client.notify_metrics(45.0, 100.0, VERSION, memory_limit_mb=512)
        slack_client.flush()

        text = _blocks_text(_get_send_kwargs(slack_client)["blocks"])
        assert "45.0s" in text
        assert "/ 300s" not in text

    def test_includes_all_fields_together(self, slack_client):
        slack_client.notify_metrics(
            45.0, 128.0, VERSION, memory_limit_mb=256, timeout_seconds=300
        )
        slack_client.flush()

        text = _blocks_text(_get_send_kwargs(slack_client)["blocks"])
        assert f"🔖 v{VERSION}" in text
        assert "⏱️ 45.0 / 300s (15%)" in text
        assert "💾 128 / 256 MB (50%)" in text

    def test_fallback_text_includes_version_and_timeout(self, slack_client):
        slack_client.notify_metrics(
            45.0, 128.0, VERSION, memory_limit_mb=256, timeout_seconds=300
        )
        slack_client.flush()

        # `text` kwarg passed to chat_postMessage() carries the fallback string
        fallback = _get_send_kwargs(slack_client)["text"]
        assert f"v{VERSION}" in fallback
        assert "45.0 / 300s (15%)" in fallback
        assert "128 / 256 MB (50%)" in fallback


class TestNotifyMetricsWithCost:
    def test_block_contains_cost_metrics(self, slack_client, cost):
        slack_client.notify_metrics(1.0, 100.0, VERSION, cost=cost)
        slack_client.flush()

        text = _blocks_text(_get_send_kwargs(slack_client)["blocks"])
        assert "🧾 $0.0340" in text
        assert "💰 MTD $1.23 (MoM +8%)" in text
        assert "🔁 12 calls (MoM +5%)" in text

    def test_none_change_pct_omits_mom_fragment(self, slack_client):
        cost = CostDisplay(
            current_run_spend_usd=0.0340,
            monthly_spend_usd=1.23,
            spend_change_pct=None,
            monthly_call_count=12,
            call_count_change_pct=None,
        )
        slack_client.notify_metrics(1.0, 100.0, VERSION, cost=cost)
        slack_client.flush()

        text = _blocks_text(_get_send_kwargs(slack_client)["blocks"])
        assert "MoM" not in text
        assert "MTD $1.23" in text
        assert "12 calls" in text

    def test_zero_change_pct_still_rendered(self, slack_client):
        cost = CostDisplay(
            current_run_spend_usd=0.0340,
            monthly_spend_usd=1.23,
            spend_change_pct=0.0,
            monthly_call_count=12,
            call_count_change_pct=0.0,
        )
        slack_client.notify_metrics(1.0, 100.0, VERSION, cost=cost)
        slack_client.flush()

        text = _blocks_text(_get_send_kwargs(slack_client)["blocks"])
        assert "MoM +0%" in text

    def test_metrics_and_cost_share_single_context_block(self, slack_client, cost):
        slack_client.notify_metrics(1.0, 100.0, VERSION, cost=cost)
        slack_client.flush()

        blocks = _get_send_kwargs(slack_client)["blocks"]
        assert [b["type"] for b in blocks] == ["context"]
        # All metrics and cost fields share the same caption line
        caption = blocks[0]["elements"][0]["text"]
        assert f"🔖 v{VERSION}" in caption
        assert "🧾 $0.0340" in caption
        assert "💰 MTD" in caption

    def test_fallback_text_includes_cost(self, slack_client, cost):
        slack_client.notify_metrics(1.0, 100.0, VERSION, cost=cost)
        slack_client.flush()

        fallback = _get_send_kwargs(slack_client)["text"]
        assert "run $0.0340" in fallback
        assert "MTD $1.23 (MoM +8%)" in fallback

    def test_cost_is_omitted_when_none(self, slack_client):
        slack_client.notify_metrics(1.0, 100.0, VERSION)
        slack_client.flush()

        text = _blocks_text(_get_send_kwargs(slack_client)["blocks"])
        assert "🧾" not in text
        assert "💰" not in text
        assert "🔁" not in text


class TestNotifyValidationErrors:
    def test_sends_invalid_tags(self, slack_client):
        result = ValidationResult()
        result.invalid_tags = {"repo": ["BadTag"]}
        slack_client.notify_validation_errors(TARGET_DATE, result)
        slack_client.flush()

        text = _blocks_text(_get_send_kwargs(slack_client)["blocks"])
        assert "BadTag" in text

    def test_block_structure(self, slack_client):
        result = ValidationResult()
        result.invalid_tags = {"repo": ["BadTag"]}
        slack_client.notify_validation_errors(TARGET_DATE, result)
        slack_client.flush()

        blocks = _get_send_kwargs(slack_client)["blocks"]
        assert blocks[0]["type"] == "header"
        assert "⚠️" in blocks[0]["text"]["text"]
        assert blocks[1]["type"] == "section"


class TestNotifyError:
    def test_sends_error_message(self, slack_client):
        slack_client.notify_error(TARGET_DATE, RuntimeError("something went wrong"))
        slack_client.flush()

        text = _blocks_text(_get_send_kwargs(slack_client)["blocks"])
        assert "something went wrong" in text

    def test_block_structure(self, slack_client):
        slack_client.notify_error(TARGET_DATE, RuntimeError("fail"))
        slack_client.flush()

        blocks = _get_send_kwargs(slack_client)["blocks"]
        assert blocks[0]["type"] == "header"
        assert "❌" in blocks[0]["text"]["text"]
        assert blocks[1]["type"] == "section"


class TestFlush:
    def test_sends_combined_message(self, slack_client):
        report = {"summary": "summary", "repositories": []}
        slack_client.notify(TARGET_DATE, report, [("repo", PAGE_URL)])
        slack_client.notify_metrics(10.0, 100.0, VERSION, memory_limit_mb=512)
        slack_client.flush()

        # Single send call
        assert slack_client.client.chat_postMessage.call_count == 1
        blocks = _get_send_kwargs(slack_client)["blocks"]
        text = _blocks_text(blocks)
        assert "Daily Report" in text
        assert "10.0s" in text
        # Divider between sections
        assert any(b["type"] == "divider" for b in blocks)

    def test_does_not_send_when_empty(self, slack_client):
        slack_client.flush()

        slack_client.client.chat_postMessage.assert_not_called()

    def test_clears_buffer_after_flush(self, slack_client):
        slack_client.notify_error(TARGET_DATE, RuntimeError("fail"))
        slack_client.flush()
        slack_client.flush()

        assert slack_client.client.chat_postMessage.call_count == 1

    def test_captures_parent_ts_after_send(self, slack_client):
        slack_client.notify_error(TARGET_DATE, RuntimeError("fail"))
        slack_client.flush()

        assert slack_client.parent_ts == FIRST_TS

    def test_parent_ts_tracks_latest_top_level_message(self, slack_client):
        slack_client.notify_error(TARGET_DATE, RuntimeError("first"))
        slack_client.flush()

        slack_client.client.chat_postMessage.return_value = {
            "ok": True,
            "ts": SECOND_TS,
        }
        slack_client.notify_error(TARGET_DATE, RuntimeError("second"))
        slack_client.flush()

        assert slack_client.parent_ts == SECOND_TS

    def test_parent_ts_points_at_last_message_when_split(self, slack_client):
        timestamps = iter([FIRST_TS, SECOND_TS])
        slack_client.client.chat_postMessage.side_effect = lambda **_: {
            "ok": True,
            "ts": next(timestamps),
        }
        _queue_every_day(slack_client)
        slack_client.flush()

        assert slack_client.client.chat_postMessage.call_count == 2
        assert slack_client.parent_ts == SECOND_TS

    def test_sends_to_configured_channel(self, slack_client):
        slack_client.notify_error(TARGET_DATE, RuntimeError("fail"))
        slack_client.flush()

        assert _get_send_kwargs(slack_client)["channel"] == CHANNEL

    def test_suppresses_slack_sdk_errors(self, slack_client):
        slack_client.client.chat_postMessage.side_effect = SlackApiError(
            "rate_limited", response={"error": "rate_limited"}
        )
        slack_client.notify_error(TARGET_DATE, RuntimeError("fail"))
        slack_client.flush()

    def test_propagates_unrelated_errors(self, slack_client):
        slack_client.client.chat_postMessage.side_effect = RuntimeError("boom")
        slack_client.notify_error(TARGET_DATE, RuntimeError("fail"))

        with pytest.raises(RuntimeError, match="boom"):
            slack_client.flush()

    def test_splits_into_multiple_messages_over_block_limit(self, slack_client):
        _queue_every_day(slack_client)
        slack_client.notify_metrics(10.0, 100.0, VERSION, memory_limit_mb=512)
        slack_client.flush()

        sent = _all_send_kwargs(slack_client)
        assert len(sent) > 1
        for kwargs in sent:
            assert len(kwargs["blocks"]) <= BLOCKS_MAX
            assert kwargs["blocks"][0]["type"] != "divider"
        headers = [
            block
            for kwargs in sent
            for block in kwargs["blocks"]
            if block["type"] == "header"
        ]
        assert len(headers) == DAYS_IN_MARCH

    def test_splits_fallback_text_per_message(self, slack_client):
        _queue_every_day(slack_client)
        slack_client.flush()

        sent = _all_send_kwargs(slack_client)
        assert len(sent) > 1
        assert "2026-03-01" in sent[0]["text"]
        assert "2026-03-31" not in sent[0]["text"]
        assert "2026-03-31" in sent[-1]["text"]

    def test_keeps_day_blocks_in_one_message(self, slack_client):
        for day in range(1, DAYS_IN_MARCH + 1):
            slack_client.notify(
                datetime(2026, 3, day, tzinfo=JST),
                {"summary": "s", "repositories": [_repo("repo")]},
                [("repo", PAGE_URL)],
                session_only_repos=["other"],
            )
        slack_client.flush()

        for kwargs in _all_send_kwargs(slack_client):
            types = [block["type"] for block in kwargs["blocks"]]
            assert types.count("header") == types.count("section")
            assert types.count("header") == types.count("context")


class TestSendNoticeThread:
    def test_does_not_send_when_notice_empty(self, threaded_client):
        threaded_client.send_notice_thread(Notice())

        threaded_client.client.chat_postMessage.assert_not_called()

    def test_does_not_send_when_parent_ts_missing(self, threaded_client):
        threaded_client.parent_ts = None
        notice = Notice()
        notice.add(NoticeSource.SESSION, "Malformed JSONL line skipped", key="x")
        threaded_client.send_notice_thread(notice)

        threaded_client.client.chat_postMessage.assert_not_called()

    def test_posts_as_thread_reply(self, threaded_client):
        notice = Notice()
        notice.add(NoticeSource.GITHUB, "Commit not found (404)", sha="abc1234")
        threaded_client.send_notice_thread(notice)

        kwargs = _get_send_kwargs(threaded_client)
        assert kwargs["thread_ts"] == FIRST_TS
        assert kwargs["channel"] == CHANNEL

    def test_groups_entries_by_source(self, threaded_client):
        notice = Notice()
        notice.add(NoticeSource.GITHUB, "Commit not found (404)", sha="abc1234")
        notice.add(NoticeSource.GITHUB, "PR not found (404)", number="42")
        notice.add(NoticeSource.SESSION, "Malformed JSONL line skipped", key="x")
        threaded_client.send_notice_thread(notice)

        text = _blocks_text(_get_send_kwargs(threaded_client)["blocks"])
        assert "Warnings (3)" in text
        assert "*github* (2)" in text
        assert "*session* (1)" in text
        assert "Commit not found (404)" in text
        assert "Malformed JSONL line skipped" in text

    def test_does_not_overwrite_parent_ts(self, threaded_client):
        notice = Notice()
        notice.add(NoticeSource.SESSION, "Malformed JSONL line skipped", key="x")
        threaded_client.client.chat_postMessage.return_value = {
            "ok": True,
            "ts": "1900000000.000300",
        }
        threaded_client.send_notice_thread(notice)

        assert threaded_client.parent_ts == FIRST_TS

    def test_splits_long_source_into_multiple_sections(self, threaded_client):
        notice = Notice()
        # Each line is ~100 chars; 60 lines exceed the section text cap and force splitting
        long_title = "Malformed JSONL line skipped " + "x" * 70
        for i in range(60):
            notice.add(NoticeSource.SESSION, long_title, key=f"k{i}")
        threaded_client.send_notice_thread(notice)

        blocks = _get_send_kwargs(threaded_client)["blocks"]
        section_blocks = [b for b in blocks if b["type"] == "section"]
        assert len(section_blocks) >= 2
        for block in section_blocks:
            text = block["text"]["text"]
            assert len(text) <= SECTION_TEXT_MAX
            assert text.startswith("*session*")

    def test_splits_into_multiple_replies_over_block_limit(self, threaded_client):
        notice = Notice()
        # Each title nearly fills a section, so every entry lands in its own block
        for i in range(BLOCKS_MAX + 5):
            notice.add(NoticeSource.SESSION, "x" * 2850, key=f"k{i}")
        threaded_client.send_notice_thread(notice)

        sent = _all_send_kwargs(threaded_client)
        assert len(sent) > 1
        for kwargs in sent:
            assert len(kwargs["blocks"]) <= BLOCKS_MAX
            assert kwargs["thread_ts"] == FIRST_TS

    def test_truncates_single_line_exceeding_section_limit(self, threaded_client):
        notice = Notice()
        # Single line longer than SECTION_TEXT_MAX must be truncated to keep the section within the limit
        notice.add(NoticeSource.SESSION, "Oversized warning " + "x" * 4000)
        threaded_client.send_notice_thread(notice)

        blocks = _get_send_kwargs(threaded_client)["blocks"]
        section_blocks = [b for b in blocks if b["type"] == "section"]
        assert len(section_blocks) == 1
        text = section_blocks[0]["text"]["text"]
        assert len(text) <= SECTION_TEXT_MAX
        assert text.endswith("…")
