"""Tests for SlackClient notification formatting."""

from datetime import datetime
from unittest.mock import MagicMock

from report import JST
from report.slack import HEADLINE_MAX, SlackClient, _escape_mrkdwn
from report.summarizer import ValidationResult


def _make_client():
    """Create a SlackClient with mocked webhook."""
    client = SlackClient.__new__(SlackClient)
    client.client = MagicMock()
    client.client.send.return_value = MagicMock(status_code=200)
    client._blocks = []
    client._fallback_parts = []
    return client


def _get_send_kwargs(client):
    """Extract kwargs from the most recent send() call."""
    return client.client.send.call_args.kwargs


def _blocks_text(blocks):
    """Concatenate all text content from blocks for easy assertion."""
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
    """Build a minimal RepoSummary for tests."""
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
        assert "<https://notion.so/page1|2026-03-28: my-repo> — 主要な作業を実施" in text
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
        report = {"repositories": [
            _repo("a", ["first headline"]),
            _repo("b", ["second headline"]),
        ]}
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
        long_headline = "あ" * (HEADLINE_MAX + 50)
        report = {"repositories": [_repo("repo", [long_headline])]}
        pages = [("repo", "https://notion.so/p")]

        client.notify(target, report, pages)
        client.flush()

        page_text = _get_send_kwargs(client)["blocks"][1]["text"]["text"]
        # headline portion after " — " should be truncated to HEADLINE_MAX
        headline_part = page_text.split(" — ", 1)[1]
        assert len(headline_part) == HEADLINE_MAX
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

        client.notify_metrics(12.5, 128.0, memory_limit_mb=256)
        client.flush()

        kwargs = _get_send_kwargs(client)
        text = _blocks_text(kwargs["blocks"])
        assert "12.5s" in text
        assert "128 / 256 MB" in text
        assert "50%" in text

    def test_sends_metrics_without_memory_limit(self):
        client = _make_client()

        client.notify_metrics(5.3, 64.0)
        client.flush()

        kwargs = _get_send_kwargs(client)
        text = _blocks_text(kwargs["blocks"])
        assert "5.3s" in text
        assert "64 MB" in text
        assert "%" not in text

    def test_block_structure(self):
        client = _make_client()

        client.notify_metrics(10.0, 100.0, memory_limit_mb=512)
        client.flush()

        blocks = _get_send_kwargs(client)["blocks"]
        assert blocks[0]["type"] == "context"


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
    def test_sends_combined_message(self):
        client = _make_client()
        target = datetime(2026, 3, 28, 0, 0, tzinfo=JST)
        report = {"summary": "summary", "repositories": []}
        pages = [("repo", "https://notion.so/p")]

        client.notify(target, report, pages)
        client.notify_metrics(10.0, 100.0, memory_limit_mb=512)
        client.flush()

        # Single send call
        assert client.client.send.call_count == 1
        blocks = _get_send_kwargs(client)["blocks"]
        text = _blocks_text(blocks)
        assert "Daily Report" in text
        assert "10.0s" in text
        # Divider between sections
        assert any(b["type"] == "divider" for b in blocks)

    def test_does_not_send_when_empty(self):
        client = _make_client()

        client.flush()

        client.client.send.assert_not_called()

    def test_clears_buffer_after_flush(self):
        client = _make_client()
        target = datetime(2026, 3, 28, 0, 0, tzinfo=JST)

        client.notify_error(target, RuntimeError("fail"))
        client.flush()
        client.flush()

        assert client.client.send.call_count == 1
