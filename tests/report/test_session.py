"""Tests for SessionClient."""

import json
from datetime import datetime
from unittest.mock import MagicMock, patch

from report import JST
from report.session import SessionClient


def _make_client():
    """Create a SessionClient with mocked boto3."""
    with patch("report.session.boto3"):
        client = SessionClient("bucket")
    client.s3 = MagicMock()
    return client


def _s3_body(text: str):
    """Create a mock S3 response body."""
    body = MagicMock()
    body.read.return_value = text.encode("utf-8")
    return {"Body": body}


class TestSnapshotAndRollback:
    def test_snapshot_returns_current_length(self):
        client = _make_client()
        assert client.snapshot_keys() == 0

        client._fetched_keys = ["key1", "key2"]
        assert client.snapshot_keys() == 2

    def test_rollback_discards_keys_after_snapshot(self):
        client = _make_client()
        client._fetched_keys = ["key1", "key2"]
        snapshot = client.snapshot_keys()

        client._fetched_keys.extend(["key3", "key4"])
        assert client.snapshot_keys() == 4

        client.rollback_keys(snapshot)
        assert client._fetched_keys == ["key1", "key2"]
        assert client.snapshot_keys() == 2


class TestParseSession:
    def test_parses_user_messages_and_tools(self):
        client = _make_client()
        since = datetime(2026, 3, 28, 0, 0, tzinfo=JST)
        until = datetime(2026, 3, 29, 0, 0, tzinfo=JST)
        lines = [
            json.dumps({
                "type": "user",
                "timestamp": "2026-03-28T10:00:00+09:00",
                "message": {"content": "Fix the bug"},
            }),
            json.dumps({
                "type": "assistant",
                "timestamp": "2026-03-28T10:01:00+09:00",
                "message": {"content": [
                    {"type": "tool_use", "name": "Read"},
                    {"type": "tool_use", "name": "Edit"},
                ]},
            }),
        ]
        client.s3.get_object.return_value = _s3_body("\n".join(lines))

        result = client.parse_session("claude-sessions/myproject/abc.jsonl", since, until)

        assert result is not None
        assert result["session_id"] == "abc"
        assert result["project"] == "myproject"
        assert result["user_messages"] == ["Fix the bug"]
        assert result["tools_used"] == ["Edit", "Read"]

    def test_filters_entries_outside_range(self):
        client = _make_client()
        since = datetime(2026, 3, 28, 0, 0, tzinfo=JST)
        until = datetime(2026, 3, 29, 0, 0, tzinfo=JST)
        lines = [
            json.dumps({
                "type": "user",
                "timestamp": "2026-03-27T23:00:00+09:00",
                "message": {"content": "Yesterday message"},
            }),
        ]
        client.s3.get_object.return_value = _s3_body("\n".join(lines))

        result = client.parse_session("claude-sessions/proj/s.jsonl", since, until)
        assert result is None

    def test_returns_none_when_no_user_messages(self):
        client = _make_client()
        since = datetime(2026, 3, 28, 0, 0, tzinfo=JST)
        until = datetime(2026, 3, 29, 0, 0, tzinfo=JST)
        lines = [
            json.dumps({
                "type": "assistant",
                "timestamp": "2026-03-28T10:00:00+09:00",
                "message": {"content": [{"type": "text", "text": "hello"}]},
            }),
        ]
        client.s3.get_object.return_value = _s3_body("\n".join(lines))

        result = client.parse_session("claude-sessions/proj/s.jsonl", since, until)
        assert result is None

    def test_skips_malformed_json(self):
        client = _make_client()
        since = datetime(2026, 3, 28, 0, 0, tzinfo=JST)
        until = datetime(2026, 3, 29, 0, 0, tzinfo=JST)
        lines = [
            "not valid json",
            json.dumps({
                "type": "user",
                "timestamp": "2026-03-28T10:00:00+09:00",
                "message": {"content": "Valid message"},
            }),
        ]
        client.s3.get_object.return_value = _s3_body("\n".join(lines))

        result = client.parse_session("claude-sessions/proj/s.jsonl", since, until)
        assert result is not None
        assert result["user_messages"] == ["Valid message"]
