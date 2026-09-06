"""Tests for SlackClient message assembly and sending."""

from datetime import datetime
from unittest.mock import MagicMock

import pytest
import slack_sdk.errors

from config import CONFIG
from report import cost
from report.shared import dates
from report.shared.notice import Notice, NoticeSource
from report.slack import client as slack_client
from report.slack.blocks import (
    SECTION_TEXT_MAX,
    divider,
    header_block,
    section_block,
)
from report.summarizer import ValidationResult

from .. import _builders

CHANNEL = "C0TEST"
FIRST_TS = "1700000000.000100"
SECOND_TS = "1800000000.000200"
REPLY_TS = "1900000000.000300"
PAGE_URL = "https://notion.so/p"
VERSION = "0.2.1"
DAYS_IN_MARCH = 31


@pytest.fixture
def client():
    stub = _builders.stub(
        slack_client.SlackClient,
        client=MagicMock(),
        channel=CHANNEL,
        is_manual=False,
        _groups=[],
        _fallback_parts=[],
        parent_ts=None,
    )
    stub.client.chat_postMessage.return_value = {"ok": True, "ts": FIRST_TS}
    return stub


@pytest.fixture
def manual_client(client):
    """Return a client for a manual run, the state that adds `[manual]` to report headers."""
    client.is_manual = True
    return client


@pytest.fixture
def threaded_client(client):
    """Return a client that already has a parent message, the state notice replies require."""
    client.parent_ts = FIRST_TS
    return client


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
        client.notify_no_activity(datetime(2026, 3, day, tzinfo=dates.JST))


def _cost(spend_change_pct=8.0, call_count_change_pct=5.0):
    return cost.CostDisplay(
        current_run_spend_usd=0.0340,
        monthly_spend_usd=1.23,
        spend_change_pct=spend_change_pct,
        monthly_call_count=12,
        call_count_change_pct=call_count_change_pct,
    )


def _invalid_tags_result():
    result = ValidationResult()
    result.invalid_tags = {"repo": ["BadTag"]}
    return result


def _repo(name, summary_lines=None):
    return {
        "name": name,
        "summary": summary_lines or [],
        "achievements": [],
        "ongoing": [],
        "claude_code": "",
        "tags": [],
    }


class TestNotify:
    def test_sends_report_with_pages(self, client):
        report = {"repositories": [_repo("my-repo", ["主要な作業を実施"])]}
        pages = [("my-repo", "https://notion.so/page1")]
        client.notify(_builders.TARGET_DATE, report, pages, owner=_builders.OWNER)
        client.flush()

        kwargs = _get_send_kwargs(client)
        text = _blocks_text(kwargs["blocks"])
        assert "2026-03-28" in text
        assert (
            "<https://notion.so/page1|2026-03-28: my-repo> — 主要な作業を実施" in text
        )
        assert kwargs["text"]  # fallback text exists

    def test_sends_no_pages_message(self, client):
        client.notify(
            _builders.TARGET_DATE, {"repositories": []}, [], owner=_builders.OWNER
        )
        client.flush()

        text = _blocks_text(_get_send_kwargs(client)["blocks"])
        assert "No pages created" in text

    def test_includes_session_only_repos(self, client):
        report = {"repositories": [_repo("repo", ["headline"])]}
        client.notify(
            _builders.TARGET_DATE,
            report,
            [("repo", PAGE_URL)],
            session_only_repos=["notes-repo"],
            owner=_builders.OWNER,
        )
        client.flush()

        text = _blocks_text(_get_send_kwargs(client)["blocks"])
        assert "📓 Session-only: notes-repo" in text

    def test_session_only_repos_with_no_pages(self, client):
        client.notify(
            _builders.TARGET_DATE,
            {"repositories": []},
            [],
            session_only_repos=["notes-repo"],
            owner=_builders.OWNER,
        )
        client.flush()

        text = _blocks_text(_get_send_kwargs(client)["blocks"])
        assert "📓 Session-only: notes-repo" in text

    def test_page_line_omits_dash_when_no_headline(self, client):
        report = {"repositories": [_repo("repo")]}
        client.notify(
            _builders.TARGET_DATE, report, [("repo", PAGE_URL)], owner=_builders.OWNER
        )
        client.flush()

        blocks = _get_send_kwargs(client)["blocks"]
        assert blocks[1]["text"]["text"] == f"<{PAGE_URL}|2026-03-28: repo>"

    def test_multiple_pages_listed_with_headlines(self, client):
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
        client.notify(_builders.TARGET_DATE, report, pages, owner=_builders.OWNER)
        client.flush()

        page_text = _get_send_kwargs(client)["blocks"][1]["text"]["text"]
        lines = page_text.split("\n")
        assert len(lines) == 2
        assert "a> — first headline" in lines[0]
        assert "b> — second headline" in lines[1]

    def test_long_headline_is_truncated(self, client):
        long_headline = "あ" * (CONFIG.slack.headline_max + 50)
        report = {"repositories": [_repo("repo", [long_headline])]}
        client.notify(
            _builders.TARGET_DATE, report, [("repo", PAGE_URL)], owner=_builders.OWNER
        )
        client.flush()

        page_text = _get_send_kwargs(client)["blocks"][1]["text"]["text"]
        # headline portion after " — " should be truncated to CONFIG.slack.headline_max
        headline_part = page_text.split(" — ", 1)[1]
        assert len(headline_part) == CONFIG.slack.headline_max
        assert headline_part.endswith("…")

    def test_headline_newlines_are_collapsed(self, client):
        headline = "first line\nsecond line\rthird line"
        report = {"repositories": [_repo("repo", [headline])]}
        client.notify(
            _builders.TARGET_DATE, report, [("repo", PAGE_URL)], owner=_builders.OWNER
        )
        client.flush()

        page_text = _get_send_kwargs(client)["blocks"][1]["text"]["text"]
        # A newline inside the headline would break the one-line-per-repo layout
        assert page_text.count("\n") == 0
        assert "first line second line third line" in page_text

    def test_headline_number_reference_links_to_page_repository(self, client):
        report = {"repositories": [_repo("repo", ["マージ #155"])]}
        client.notify(
            _builders.TARGET_DATE, report, [("repo", PAGE_URL)], owner=_builders.OWNER
        )
        client.flush()

        page_text = _get_send_kwargs(client)["blocks"][1]["text"]["text"]
        url = f"https://github.com/{_builders.OWNER}/repo/issues/155"
        assert page_text.endswith(f"— マージ <{url}|#155>")

    def test_headline_special_chars_are_escaped(self, client):
        headline = "fix <!channel> & <T> generic leak"
        report = {"repositories": [_repo("repo", [headline])]}
        client.notify(
            _builders.TARGET_DATE, report, [("repo", PAGE_URL)], owner=_builders.OWNER
        )
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


class TestAppendGroup:
    def test_buffers_blocks_and_fallback_as_one_group(self, client):
        header = header_block("📝 Daily Report (2026-03-28)")
        section = section_block("body")
        client._append_group([header, section], "1 page(s) created")

        assert client._groups == [[header, section]]
        assert client._fallback_parts == ["1 page(s) created"]

    def test_keeps_groups_separate(self, client):
        first = section_block("first")
        second = section_block("second")
        client._append_group([first], "a")
        client._append_group([second], "b")

        assert client._groups == [[first], [second]]
        assert client._fallback_parts == ["a", "b"]


class TestChunkBlocks:
    def test_returns_single_chunk_within_limit(self):
        blocks = [section_block(str(i)) for i in range(3)]
        assert slack_client._chunk_blocks(blocks, limit=3) == [blocks]

    def test_splits_into_chunks_of_limit(self):
        blocks = [section_block(str(i)) for i in range(5)]
        assert slack_client._chunk_blocks(blocks, limit=2) == [
            blocks[:2],
            blocks[2:4],
            blocks[4:],
        ]


class TestPackMessages:
    def test_returns_nothing_for_empty_buffer(self):
        assert slack_client._pack_messages([], []) == []

    def test_joins_groups_with_divider_without_leading_divider(self):
        first = section_block("first")
        second = section_block("second")
        messages = slack_client._pack_messages([[first], [second]], ["a", "b"])

        assert messages == [([first, divider(), second], "a | b")]

    def test_starts_new_message_when_limit_exceeded(self):
        groups = [[section_block(str(i))] for i in range(4)]
        fallbacks = [str(i) for i in range(4)]
        messages = slack_client._pack_messages(groups, fallbacks, limit=3)

        assert [len(blocks) for blocks, _ in messages] == [3, 3]
        assert [fallback for _, fallback in messages] == ["0 | 1", "2 | 3"]

    def test_never_splits_a_group_across_messages(self):
        pair = [header_block("h"), section_block("s")]
        groups = [list(pair) for _ in range(3)]
        messages = slack_client._pack_messages(groups, ["a", "b", "c"], limit=4)

        assert [len(blocks) for blocks, _ in messages] == [2, 2, 2]
        for blocks, _ in messages:
            assert blocks == pair


class TestNotifyNoActivity:
    def test_sends_no_activity_message(self, client):
        client.notify_no_activity(_builders.TARGET_DATE)
        client.flush()

        kwargs = _get_send_kwargs(client)
        text = _blocks_text(kwargs["blocks"])
        assert "2026-03-28" in text
        assert "No activity" in text
        assert kwargs["text"]


class TestNotifySessionOnly:
    def test_sends_session_only_message(self, client):
        client.notify_session_only(_builders.TARGET_DATE, ["repo-a", "repo-b"])
        client.flush()

        kwargs = _get_send_kwargs(client)
        text = _blocks_text(kwargs["blocks"])
        assert "2026-03-28" in text
        assert "Session-only: repo-a, repo-b" in text
        assert kwargs["text"]


class TestNotifyMetrics:
    @pytest.mark.parametrize(
        "elapsed,peak_memory,limits,expected,absent",
        [
            pytest.param(
                12.5,
                128.0,
                {"memory_limit_mb": 256},
                ["⏱️ 12.5s", "💾 128 / 256 MB (50%)"],
                (),
                id="memory_ratio",
            ),
            pytest.param(
                5.3,
                64.0,
                {},
                ["⏱️ 5.3s", "💾 64 MB"],
                ("%",),
                id="no_limits",
            ),
            pytest.param(
                45.0,
                100.0,
                {"memory_limit_mb": 512, "timeout_seconds": 300},
                ["⏱️ 45.0 / 300s (15%)"],
                (),
                id="timeout_ratio",
            ),
            pytest.param(
                45.0,
                100.0,
                {"memory_limit_mb": 512},
                ["⏱️ 45.0s"],
                ("/ 300s",),
                id="no_timeout",
            ),
            pytest.param(
                45.0,
                128.0,
                {"memory_limit_mb": 256, "timeout_seconds": 300},
                [f"🔖 v{VERSION}", "⏱️ 45.0 / 300s (15%)", "💾 128 / 256 MB (50%)"],
                (),
                id="all_fields",
            ),
        ],
    )
    def test_renders_metrics_caption(
        self, client, elapsed, peak_memory, limits, expected, absent
    ):
        client.notify_metrics(elapsed, peak_memory, VERSION, **limits)
        client.flush()

        text = _blocks_text(_get_send_kwargs(client)["blocks"])
        for fragment in expected:
            assert fragment in text
        for fragment in absent:
            assert fragment not in text

    def test_fallback_text_includes_version_and_timeout(self, client):
        client.notify_metrics(
            45.0, 128.0, VERSION, memory_limit_mb=256, timeout_seconds=300
        )
        client.flush()

        # `text` kwarg passed to chat_postMessage() carries the fallback string
        fallback = _get_send_kwargs(client)["text"]
        assert f"v{VERSION}" in fallback
        assert "45.0 / 300s (15%)" in fallback
        assert "128 / 256 MB (50%)" in fallback


class TestNotifyMetricsWithCost:
    @pytest.mark.parametrize(
        "spend_change_pct,call_count_change_pct,expected,absent",
        [
            pytest.param(
                8.0,
                5.0,
                ["🧾 $0.0340", "💰 MTD $1.23 (MoM +8%)", "🔁 12 calls (MoM +5%)"],
                (),
                id="distinct_changes",
            ),
            pytest.param(
                0.0,
                0.0,
                ["💰 MTD $1.23 (MoM +0%)", "🔁 12 calls (MoM +0%)"],
                (),
                id="zero_change",
            ),
            pytest.param(
                None,
                None,
                ["💰 MTD $1.23", "🔁 12 calls"],
                ("MoM",),
                id="uncomputable_change",
            ),
        ],
    )
    def test_renders_cost_caption(
        self, client, spend_change_pct, call_count_change_pct, expected, absent
    ):
        cost_display = _cost(spend_change_pct, call_count_change_pct)
        client.notify_metrics(1.0, 100.0, VERSION, cost_display=cost_display)
        client.flush()

        text = _blocks_text(_get_send_kwargs(client)["blocks"])
        for fragment in expected:
            assert fragment in text
        for fragment in absent:
            assert fragment not in text

    def test_metrics_and_cost_share_single_context_block(self, client):
        client.notify_metrics(1.0, 100.0, VERSION, cost_display=_cost())
        client.flush()

        blocks = _get_send_kwargs(client)["blocks"]
        assert [b["type"] for b in blocks] == ["context"]
        # All metrics and cost fields share the same caption line
        caption = blocks[0]["elements"][0]["text"]
        assert f"🔖 v{VERSION}" in caption
        assert "🧾 $0.0340" in caption
        assert "💰 MTD" in caption

    def test_fallback_text_includes_cost(self, client):
        client.notify_metrics(1.0, 100.0, VERSION, cost_display=_cost())
        client.flush()

        fallback = _get_send_kwargs(client)["text"]
        assert "run $0.0340" in fallback
        assert "MTD $1.23 (MoM +8%)" in fallback

    def test_cost_is_omitted_when_none(self, client):
        client.notify_metrics(1.0, 100.0, VERSION)
        client.flush()

        text = _blocks_text(_get_send_kwargs(client)["blocks"])
        assert "🧾" not in text
        assert "💰" not in text
        assert "🔁" not in text


class TestNotifyValidationErrors:
    def test_sends_invalid_tags(self, client):
        client.notify_validation_errors(_builders.TARGET_DATE, _invalid_tags_result())
        client.flush()

        text = _blocks_text(_get_send_kwargs(client)["blocks"])
        assert "BadTag" in text


class TestNotifyError:
    def test_sends_error_message(self, client):
        client.notify_error(_builders.TARGET_DATE, RuntimeError("something went wrong"))
        client.flush()

        text = _blocks_text(_get_send_kwargs(client)["blocks"])
        assert "something went wrong" in text


class TestRunLabels:
    @pytest.mark.parametrize(
        "is_manual,is_backfill,expected",
        [
            pytest.param(False, False, "💤 Daily Report (2026-03-28)", id="scheduled"),
            pytest.param(
                True, False, "💤 Daily Report (2026-03-28) [manual]", id="manual"
            ),
            pytest.param(
                False, True, "💤 Daily Report (2026-03-28) [backfill]", id="backfill"
            ),
            pytest.param(
                True,
                True,
                "💤 Daily Report (2026-03-28) [manual] [backfill]",
                id="manual-backfill",
            ),
        ],
    )
    def test_header_marks_run_origin(self, client, is_manual, is_backfill, expected):
        client.is_manual = is_manual
        client.notify_no_activity(_builders.TARGET_DATE, is_backfill=is_backfill)
        client.flush()

        kwargs = _get_send_kwargs(client)
        assert kwargs["blocks"][0]["text"]["text"] == expected
        assert kwargs["text"].startswith(expected)

    @pytest.mark.parametrize(
        "queue",
        [
            pytest.param(
                lambda c: c.notify(
                    _builders.TARGET_DATE,
                    {"repositories": []},
                    [],
                    owner=_builders.OWNER,
                    is_backfill=True,
                ),
                id="report",
            ),
            pytest.param(
                lambda c: c.notify_no_activity(_builders.TARGET_DATE, is_backfill=True),
                id="no-activity",
            ),
            pytest.param(
                lambda c: c.notify_session_only(
                    _builders.TARGET_DATE, ["repo"], is_backfill=True
                ),
                id="session-only",
            ),
            pytest.param(
                lambda c: c.notify_validation_errors(
                    _builders.TARGET_DATE, _invalid_tags_result(), is_backfill=True
                ),
                id="validation-errors",
            ),
            pytest.param(
                lambda c: c.notify_error(
                    _builders.TARGET_DATE, RuntimeError("boom"), is_backfill=True
                ),
                id="error",
            ),
        ],
    )
    def test_every_notification_type_is_labeled(self, manual_client, queue):
        queue(manual_client)
        manual_client.flush()

        header = _get_send_kwargs(manual_client)["blocks"][0]["text"]["text"]
        assert header.endswith("[manual] [backfill]")


class TestBlockStructure:
    @pytest.mark.parametrize(
        "queue,expected_types,expected_emoji",
        [
            pytest.param(
                lambda c: c.notify(
                    _builders.TARGET_DATE,
                    {"repositories": [_repo("repo", ["h"])]},
                    [("repo", PAGE_URL)],
                    session_only_repos=["notes-repo"],
                    owner=_builders.OWNER,
                ),
                ["header", "section", "context"],
                "📝",
                id="report",
            ),
            pytest.param(
                lambda c: c.notify_no_activity(_builders.TARGET_DATE),
                ["header", "section"],
                "💤",
                id="no_activity",
            ),
            pytest.param(
                lambda c: c.notify_session_only(_builders.TARGET_DATE, ["repo-a"]),
                ["header", "section"],
                "📓",
                id="session_only",
            ),
            pytest.param(
                lambda c: c.notify_validation_errors(
                    _builders.TARGET_DATE, _invalid_tags_result()
                ),
                ["header", "section"],
                "⚠️",
                id="validation_errors",
            ),
            pytest.param(
                lambda c: c.notify_error(_builders.TARGET_DATE, RuntimeError("fail")),
                ["header", "section"],
                "❌",
                id="error",
            ),
            pytest.param(
                lambda c: c.notify_metrics(10.0, 100.0, VERSION, memory_limit_mb=512),
                ["context"],
                None,
                id="metrics",
            ),
        ],
    )
    def test_matches_notification_kind(
        self, client, queue, expected_types, expected_emoji
    ):
        queue(client)
        client.flush()

        blocks = _get_send_kwargs(client)["blocks"]
        assert [b["type"] for b in blocks] == expected_types
        if expected_emoji is not None:
            assert expected_emoji in blocks[0]["text"]["text"]
        for block in blocks:
            if block["type"] == "section":
                # Bodies are a single mrkdwn text, never the field columns Slack also allows
                assert "text" in block
                assert "fields" not in block


class TestFlush:
    def test_sends_combined_message(self, client):
        report = {"summary": "summary", "repositories": []}
        client.notify(
            _builders.TARGET_DATE, report, [("repo", PAGE_URL)], owner=_builders.OWNER
        )
        client.notify_metrics(10.0, 100.0, VERSION, memory_limit_mb=512)
        client.flush()

        # Single send call
        assert client.client.chat_postMessage.call_count == 1
        blocks = _get_send_kwargs(client)["blocks"]
        text = _blocks_text(blocks)
        assert "Daily Report" in text
        assert "10.0s" in text
        # Divider between sections
        assert any(b["type"] == "divider" for b in blocks)

    def test_does_not_send_when_empty(self, client):
        client.flush()

        client.client.chat_postMessage.assert_not_called()

    def test_clears_buffer_after_flush(self, client):
        client.notify_error(_builders.TARGET_DATE, RuntimeError("fail"))
        client.flush()
        client.flush()

        assert client.client.chat_postMessage.call_count == 1

    def test_captures_parent_ts_after_send(self, client):
        client.notify_error(_builders.TARGET_DATE, RuntimeError("fail"))
        client.flush()

        assert client.parent_ts == FIRST_TS

    def test_parent_ts_tracks_latest_top_level_message(self, client):
        client.notify_error(_builders.TARGET_DATE, RuntimeError("first"))
        client.flush()

        client.client.chat_postMessage.return_value = {
            "ok": True,
            "ts": SECOND_TS,
        }
        client.notify_error(_builders.TARGET_DATE, RuntimeError("second"))
        client.flush()

        assert client.parent_ts == SECOND_TS

    def test_parent_ts_points_at_last_message_when_split(self, client):
        timestamps = iter([FIRST_TS, SECOND_TS])
        client.client.chat_postMessage.side_effect = lambda **_: {
            "ok": True,
            "ts": next(timestamps),
        }
        _queue_every_day(client)
        client.flush()

        assert client.client.chat_postMessage.call_count == 2
        assert client.parent_ts == SECOND_TS

    def test_sends_to_configured_channel(self, client):
        client.notify_error(_builders.TARGET_DATE, RuntimeError("fail"))
        client.flush()

        assert _get_send_kwargs(client)["channel"] == CHANNEL

    def test_suppresses_slack_sdk_errors(self, client):
        client.client.chat_postMessage.side_effect = slack_sdk.errors.SlackApiError(
            "rate_limited", response={"error": "rate_limited"}
        )
        client.notify_error(_builders.TARGET_DATE, RuntimeError("fail"))
        client.flush()

    def test_propagates_unrelated_errors(self, client):
        client.client.chat_postMessage.side_effect = RuntimeError("boom")
        client.notify_error(_builders.TARGET_DATE, RuntimeError("fail"))

        with pytest.raises(RuntimeError, match="boom"):
            client.flush()

    def test_splits_into_multiple_messages_over_block_limit(self, client):
        _queue_every_day(client)
        client.notify_metrics(10.0, 100.0, VERSION, memory_limit_mb=512)
        client.flush()

        sent = _all_send_kwargs(client)
        assert len(sent) > 1
        for kwargs in sent:
            assert len(kwargs["blocks"]) <= slack_client.BLOCKS_MAX
            assert kwargs["blocks"][0]["type"] != "divider"
        headers = [
            block
            for kwargs in sent
            for block in kwargs["blocks"]
            if block["type"] == "header"
        ]
        assert len(headers) == DAYS_IN_MARCH

    def test_splits_fallback_text_per_message(self, client):
        _queue_every_day(client)
        client.flush()

        sent = _all_send_kwargs(client)
        assert len(sent) > 1
        assert "2026-03-01" in sent[0]["text"]
        assert "2026-03-31" not in sent[0]["text"]
        assert "2026-03-31" in sent[-1]["text"]

    def test_keeps_day_blocks_in_one_message(self, client):
        for day in range(1, DAYS_IN_MARCH + 1):
            client.notify(
                datetime(2026, 3, day, tzinfo=dates.JST),
                {"summary": "s", "repositories": [_repo("repo")]},
                [("repo", PAGE_URL)],
                session_only_repos=["other"],
                owner=_builders.OWNER,
            )
        client.flush()

        for kwargs in _all_send_kwargs(client):
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
            "ts": REPLY_TS,
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
        for i in range(slack_client.BLOCKS_MAX + 5):
            notice.add(NoticeSource.SESSION, "x" * 2850, key=f"k{i}")
        threaded_client.send_notice_thread(notice)

        sent = _all_send_kwargs(threaded_client)
        assert len(sent) > 1
        for kwargs in sent:
            assert len(kwargs["blocks"]) <= slack_client.BLOCKS_MAX
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
