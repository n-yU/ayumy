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
    return client


class TestNotify:
    def test_sends_report_with_pages(self):
        client = _make_client()
        target = datetime(2026, 3, 28, 0, 0, tzinfo=JST)
        report = {"summary": "Today's work", "repositories": []}
        pages = [("my-repo", "https://notion.so/page1")]

        client.notify(target, report, pages)

        text = client.client.send.call_args.kwargs["text"]
        assert "2026-03-28" in text
        assert "Today's work" in text
        assert "my-repo" in text
        assert "https://notion.so/page1" in text

    def test_sends_no_pages_message(self):
        client = _make_client()
        target = datetime(2026, 3, 28, 0, 0, tzinfo=JST)
        report = {"summary": "", "repositories": []}

        client.notify(target, report, [])

        text = client.client.send.call_args.kwargs["text"]
        assert "No pages created" in text

    def test_includes_skipped_repos(self):
        client = _make_client()
        target = datetime(2026, 3, 28, 0, 0, tzinfo=JST)
        report = {"summary": "summary", "repositories": []}
        pages = [("repo", "https://notion.so/p")]

        client.notify(target, report, pages, skipped_repos=["unknown-repo"])

        text = client.client.send.call_args.kwargs["text"]
        assert "unknown-repo" in text


class TestNotifyValidationErrors:
    def test_sends_invalid_tags_and_statuses(self):
        client = _make_client()
        target = datetime(2026, 3, 28, 0, 0, tzinfo=JST)
        result = ValidationResult()
        result.invalid_tags = {"repo": ["BadTag"]}
        result.invalid_statuses = {"repo": "BadStatus"}

        client.notify_validation_errors(target, result)

        text = client.client.send.call_args.kwargs["text"]
        assert "BadTag" in text
        assert "BadStatus" in text
