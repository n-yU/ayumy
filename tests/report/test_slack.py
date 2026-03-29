"""Tests for SlackClient notification formatting."""

from datetime import datetime
from unittest.mock import MagicMock

from report import JST
from report.slack import SlackClient
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


class TestNotify:
    def test_sends_report_with_pages(self):
        client = _make_client()
        target = datetime(2026, 3, 28, 0, 0, tzinfo=JST)
        report = {"summary": "Today's work", "repositories": []}
        pages = [("my-repo", "https://notion.so/page1")]

        client.notify(target, report, pages)
        client.flush()

        kwargs = _get_send_kwargs(client)
        blocks = kwargs["blocks"]
        text = _blocks_text(blocks)
        assert "2026-03-28" in text
        assert "Today's work" in text
        assert "<https://notion.so/page1|2026-03-28: my-repo>" in text
        assert kwargs["text"]  # fallback text exists

    def test_sends_no_pages_message(self):
        client = _make_client()
        target = datetime(2026, 3, 28, 0, 0, tzinfo=JST)
        report = {"summary": "", "repositories": []}

        client.notify(target, report, [])
        client.flush()

        kwargs = _get_send_kwargs(client)
        text = _blocks_text(kwargs["blocks"])
        assert "No pages created" in text

    def test_includes_skipped_repos(self):
        client = _make_client()
        target = datetime(2026, 3, 28, 0, 0, tzinfo=JST)
        report = {"summary": "summary", "repositories": []}
        pages = [("repo", "https://notion.so/p")]

        client.notify(target, report, pages, skipped_repos=["unknown-repo"])
        client.flush()

        kwargs = _get_send_kwargs(client)
        text = _blocks_text(kwargs["blocks"])
        assert "unknown-repo" in text

    def test_skipped_repos_with_no_pages(self):
        client = _make_client()
        target = datetime(2026, 3, 28, 0, 0, tzinfo=JST)
        report = {"summary": "", "repositories": []}

        client.notify(target, report, [], skipped_repos=["unknown-repo"])
        client.flush()

        kwargs = _get_send_kwargs(client)
        text = _blocks_text(kwargs["blocks"])
        assert "unknown-repo" in text

    def test_block_structure(self):
        client = _make_client()
        target = datetime(2026, 3, 28, 0, 0, tzinfo=JST)
        report = {"summary": "summary", "repositories": []}
        pages = [("repo", "https://notion.so/p")]

        client.notify(target, report, pages, skipped_repos=["skipped"])
        client.flush()

        blocks = _get_send_kwargs(client)["blocks"]
        assert blocks[0]["type"] == "header"
        assert blocks[1]["type"] == "section"  # summary
        assert blocks[2]["type"] == "divider"
        assert blocks[3]["type"] == "section"  # page links as fields
        assert "fields" in blocks[3]
        assert "text" not in blocks[3]
        assert blocks[4]["type"] == "context"  # skipped

    def test_falls_back_to_list_when_over_10_pages(self):
        client = _make_client()
        target = datetime(2026, 3, 28, 0, 0, tzinfo=JST)
        report = {"summary": "summary", "repositories": []}
        pages = [(f"repo-{i}", f"https://notion.so/p{i}") for i in range(11)]

        client.notify(target, report, pages)
        client.flush()

        blocks = _get_send_kwargs(client)["blocks"]
        page_block = blocks[3]
        assert "fields" not in page_block
        assert "text" in page_block
        assert "repo-0" in page_block["text"]["text"]
        assert "repo-10" in page_block["text"]["text"]


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
    def test_sends_invalid_tags_and_statuses(self):
        client = _make_client()
        target = datetime(2026, 3, 28, 0, 0, tzinfo=JST)
        result = ValidationResult()
        result.invalid_tags = {"repo": ["BadTag"]}
        result.invalid_statuses = {"repo": "BadStatus"}

        client.notify_validation_errors(target, result)
        client.flush()

        kwargs = _get_send_kwargs(client)
        text = _blocks_text(kwargs["blocks"])
        assert "BadTag" in text
        assert "BadStatus" in text

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
