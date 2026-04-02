"""Tests for SessionStore."""

import json
from unittest.mock import MagicMock, patch

from report.store import SessionStore


def _make_store():
    """Create a SessionStore with mocked boto3."""
    with patch("report.store.boto3"):
        store = SessionStore("table")
    store.table = MagicMock()
    return store


def _make_session_client():
    """Create a mock SessionClient."""
    client = MagicMock()
    client.bucket = "bucket"
    return client


def _s3_body(text: str):
    """Create a mock S3 response body."""
    body = MagicMock()
    body.read.return_value = text.encode("utf-8")
    return {"Body": body}


def _jsonl_lines(*entries):
    """Build a JSONL string from entry dicts."""
    return "\n".join(json.dumps(e) for e in entries)


class TestBuildItems:
    def test_groups_by_date(self):
        store = _make_store()
        client = _make_session_client()
        client.list_session_objects.return_value = [
            {"Key": "claude-sessions/proj/s1.jsonl"},
        ]
        client.read_repo_name.return_value = "my-repo"

        lines = _jsonl_lines(
            {
                "type": "user",
                "timestamp": "2026-03-28T23:30:00+09:00",
                "message": {"content": "Day 1 message"},
            },
            {
                "type": "user",
                "timestamp": "2026-03-29T00:30:00+09:00",
                "message": {"content": "Day 2 message"},
            },
        )
        client.s3.get_object.return_value = _s3_body(lines)

        items = store._build_items(client)

        assert len(items) == 2
        dates = {item["date"] for item in items}
        assert dates == {"2026-03-28", "2026-03-29"}

        for item in items:
            assert item["repo#session_id"] == "my-repo#s1"
            assert item["repo"] == "my-repo"
            assert item["project"] == "proj"

    def test_aggregates_messages_and_tools(self):
        store = _make_store()
        client = _make_session_client()
        client.list_session_objects.return_value = [
            {"Key": "claude-sessions/proj/s1.jsonl"},
        ]
        client.read_repo_name.return_value = "repo"

        lines = _jsonl_lines(
            {
                "type": "user",
                "timestamp": "2026-03-28T10:00:00+09:00",
                "message": {"content": "First message"},
            },
            {
                "type": "assistant",
                "timestamp": "2026-03-28T10:01:00+09:00",
                "message": {"content": [
                    {"type": "tool_use", "name": "Read"},
                    {"type": "tool_use", "name": "Edit"},
                ]},
            },
            {
                "type": "user",
                "timestamp": "2026-03-28T10:05:00+09:00",
                "message": {"content": "Second message"},
            },
            {
                "type": "assistant",
                "timestamp": "2026-03-28T10:06:00+09:00",
                "message": {"content": [
                    {"type": "tool_use", "name": "Read"},
                    {"type": "tool_use", "name": "Bash"},
                ]},
            },
        )
        client.s3.get_object.return_value = _s3_body(lines)

        items = store._build_items(client)

        assert len(items) == 1
        item = items[0]
        assert item["user_messages"] == ["First message", "Second message"]
        assert item["tools_used"] == ["Bash", "Edit", "Read"]
        assert item["start_time"] == "2026-03-28T10:00:00+09:00"
        assert item["end_time"] == "2026-03-28T10:06:00+09:00"

    def test_skips_no_repo(self):
        store = _make_store()
        client = _make_session_client()
        client.list_session_objects.return_value = [
            {"Key": "claude-sessions/proj/s1.jsonl"},
        ]
        client.read_repo_name.return_value = None

        items = store._build_items(client)

        assert items == []
        client.s3.get_object.assert_not_called()

    def test_skips_no_user_messages(self):
        store = _make_store()
        client = _make_session_client()
        client.list_session_objects.return_value = [
            {"Key": "claude-sessions/proj/s1.jsonl"},
        ]
        client.read_repo_name.return_value = "repo"

        lines = _jsonl_lines(
            {
                "type": "assistant",
                "timestamp": "2026-03-28T10:00:00+09:00",
                "message": {"content": [{"type": "text", "text": "hello"}]},
            },
        )
        client.s3.get_object.return_value = _s3_body(lines)

        items = store._build_items(client)

        assert items == []


class TestWriteItems:
    def test_uses_batch_writer(self):
        store = _make_store()
        batch_writer = MagicMock()
        store.table.batch_writer.return_value.__enter__ = MagicMock(
            return_value=batch_writer,
        )
        store.table.batch_writer.return_value.__exit__ = MagicMock(
            return_value=False,
        )

        items = [
            {"date": "2026-03-28", "repo#session_id": "repo#s1"},
            {"date": "2026-03-29", "repo#session_id": "repo#s2"},
        ]

        count = store._write_items(items)

        assert count == 2
        assert batch_writer.put_item.call_count == 2


class TestIngest:
    def test_returns_count(self):
        store = _make_store()
        client = _make_session_client()
        client.list_session_objects.return_value = [
            {"Key": "claude-sessions/proj/s1.jsonl"},
        ]
        client.read_repo_name.return_value = "repo"

        lines = _jsonl_lines(
            {
                "type": "user",
                "timestamp": "2026-03-28T10:00:00+09:00",
                "message": {"content": "Hello"},
            },
        )
        client.s3.get_object.return_value = _s3_body(lines)

        batch_writer = MagicMock()
        store.table.batch_writer.return_value.__enter__ = MagicMock(
            return_value=batch_writer,
        )
        store.table.batch_writer.return_value.__exit__ = MagicMock(
            return_value=False,
        )

        count = store.ingest(client)

        assert count == 1
        batch_writer.put_item.assert_called_once()
