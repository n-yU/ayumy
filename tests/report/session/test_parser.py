"""Tests for SessionLogParser."""

import json
from unittest.mock import MagicMock

from report.session.parser import SessionLogParser


def _make_session_client():
    """Create a mock SessionClient."""
    client = MagicMock()
    client.bucket = "bucket"
    return client


def _s3_body(text: str):
    body = MagicMock()
    body.read.return_value = text.encode("utf-8")
    return {"Body": body}


def _jsonl_lines(*entries):
    return "\n".join(json.dumps(e) for e in entries)


class TestBuildItemsViaParser:
    def test_returns_item_and_processed_key(self):
        parser = SessionLogParser()
        client = _make_session_client()
        client.list_session_objects.return_value = [
            {"Key": "claude-sessions/proj/s1.jsonl"},
        ]
        client.read_repo_name.return_value = "my-repo"
        client.s3.get_object.return_value = _s3_body(
            _jsonl_lines(
                {
                    "type": "user",
                    "timestamp": "2026-03-28T10:00:00+09:00",
                    "message": {"content": "Hello"},
                },
            )
        )

        items, keys = parser.build_items(client)

        assert len(items) == 1
        item = items[0]
        assert item["date"] == "2026-03-28"
        assert item["repo"] == "my-repo"
        assert item["repo#session_id"] == "my-repo#s1"
        assert item["user_messages"] == ["Hello"]
        assert keys == ["claude-sessions/proj/s1.jsonl"]

    def test_skips_project_without_repo(self):
        parser = SessionLogParser()
        client = _make_session_client()
        client.list_session_objects.return_value = [
            {"Key": "claude-sessions/proj/s1.jsonl"},
        ]
        client.read_repo_name.return_value = None

        items, keys = parser.build_items(client)

        assert items == []
        assert keys == []
        client.s3.get_object.assert_not_called()
