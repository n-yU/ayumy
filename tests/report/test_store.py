"""Tests for SessionStore."""

import json
from datetime import date
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

        items, keys = store._build_items(client)

        assert len(items) == 2
        dates = {item["date"] for item in items}
        assert dates == {"2026-03-28", "2026-03-29"}

        for item in items:
            assert item["repo#session_id"] == "my-repo#s1"
            assert item["repo"] == "my-repo"
            assert item["project"] == "proj"

        assert keys == ["claude-sessions/proj/s1.jsonl"]

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

        items, keys = store._build_items(client)

        assert len(items) == 1
        item = items[0]
        assert item["user_messages"] == ["First message", "Second message"]
        assert item["tools_used"] == ["Bash", "Edit", "Read"]
        assert item["start_time"] == "2026-03-28T10:00:00+09:00"
        assert item["end_time"] == "2026-03-28T10:06:00+09:00"

    def test_extracts_commits_from_tool_result(self):
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
                "message": {"content": "Fix the bug"},
            },
            {
                "type": "user",
                "timestamp": "2026-03-28T10:05:00+09:00",
                "message": {"content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_123",
                        "content": "[feat/login a1b2c3d] Implement login flow\n 2 files changed",
                        "is_error": False,
                    },
                ]},
            },
            {
                "type": "user",
                "timestamp": "2026-03-28T10:10:00+09:00",
                "message": {"content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_456",
                        "content": "[feat/login e5f6a7b] Fix test failure\n 1 file changed",
                        "is_error": False,
                    },
                ]},
            },
        )
        client.s3.get_object.return_value = _s3_body(lines)

        items, keys = store._build_items(client)

        assert len(items) == 1
        assert items[0]["session_commits"] == [
            {"sha": "a1b2c3d", "message": "Implement login flow",
             "timestamp": "2026-03-28T10:05:00+09:00"},
            {"sha": "e5f6a7b", "message": "Fix test failure",
             "timestamp": "2026-03-28T10:10:00+09:00"},
        ]

    def test_extracts_root_and_detached_head_commits(self):
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
                "message": {"content": "Init repo"},
            },
            {
                "type": "user",
                "timestamp": "2026-03-28T10:05:00+09:00",
                "message": {"content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_root",
                        "content": "[main (root-commit) a1b2c3d] Initial commit\n 1 file changed",
                        "is_error": False,
                    },
                ]},
            },
            {
                "type": "user",
                "timestamp": "2026-03-28T10:10:00+09:00",
                "message": {"content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_detach",
                        "content": "[detached HEAD e5f6a7b] Hotfix\n 1 file changed",
                        "is_error": False,
                    },
                ]},
            },
        )
        client.s3.get_object.return_value = _s3_body(lines)

        items, keys = store._build_items(client)

        assert len(items) == 1
        assert items[0]["session_commits"] == [
            {"sha": "a1b2c3d", "message": "Initial commit",
             "timestamp": "2026-03-28T10:05:00+09:00"},
            {"sha": "e5f6a7b", "message": "Hotfix",
             "timestamp": "2026-03-28T10:10:00+09:00"},
        ]

    def test_extracts_commit_after_hook_output(self):
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
                "message": {"content": "Commit with hooks"},
            },
            {
                "type": "user",
                "timestamp": "2026-03-28T10:05:00+09:00",
                "message": {"content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_hook",
                        "content": "check formatting... ok\nrunning linter... passed\n[main a1b2c3d] Fix formatting\n 2 files changed",
                        "is_error": False,
                    },
                ]},
            },
        )
        client.s3.get_object.return_value = _s3_body(lines)

        items, keys = store._build_items(client)

        assert len(items) == 1
        assert items[0]["session_commits"] == [
            {"sha": "a1b2c3d", "message": "Fix formatting",
             "timestamp": "2026-03-28T10:05:00+09:00"},
        ]

    def test_extracts_multiple_commits_from_single_tool_result(self):
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
                "message": {"content": "Run commands"},
            },
            {
                "type": "user",
                "timestamp": "2026-03-28T10:05:00+09:00",
                "message": {"content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_multi",
                        "content": "[main abc1234] First commit\n 1 file changed\n[main def5678] Second commit\n 2 files changed",
                        "is_error": False,
                    },
                ]},
            },
        )
        client.s3.get_object.return_value = _s3_body(lines)

        items, keys = store._build_items(client)

        assert len(items) == 1
        assert items[0]["session_commits"] == [
            {"sha": "abc1234", "message": "First commit",
             "timestamp": "2026-03-28T10:05:00+09:00"},
            {"sha": "def5678", "message": "Second commit",
             "timestamp": "2026-03-28T10:05:00+09:00"},
        ]

    def test_ignores_error_tool_results(self):
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
                "message": {"content": "Try commit"},
            },
            {
                "type": "user",
                "timestamp": "2026-03-28T10:05:00+09:00",
                "message": {"content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_err",
                        "content": "[main abc1234] Some commit\n 1 file changed",
                        "is_error": True,
                    },
                ]},
            },
        )
        client.s3.get_object.return_value = _s3_body(lines)

        items, keys = store._build_items(client)

        assert len(items) == 1
        assert items[0]["session_commits"] == []

    def test_cross_midnight_commit_only_day(self):
        store = _make_store()
        client = _make_session_client()
        client.list_session_objects.return_value = [
            {"Key": "claude-sessions/proj/s1.jsonl"},
        ]
        client.read_repo_name.return_value = "repo"

        # Day 1 has a user message; Day 2 has only a tool_result with a commit
        lines = _jsonl_lines(
            {
                "type": "user",
                "timestamp": "2026-03-28T23:50:00+09:00",
                "message": {"content": "Fix the bug"},
            },
            {
                "type": "user",
                "timestamp": "2026-03-29T00:05:00+09:00",
                "message": {"content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_abc",
                        "content": "[main a1b2c3d] Apply fix\n 1 file changed",
                        "is_error": False,
                    },
                ]},
            },
        )
        client.s3.get_object.return_value = _s3_body(lines)

        items, keys = store._build_items(client)

        assert len(items) == 2
        day1 = next(i for i in items if i["date"] == "2026-03-28")
        day2 = next(i for i in items if i["date"] == "2026-03-29")

        assert day1["user_messages"] == ["Fix the bug"]
        assert day1["session_commits"] == []
        assert day2["user_messages"] == []
        assert day2["session_commits"] == [
            {"sha": "a1b2c3d", "message": "Apply fix",
             "timestamp": "2026-03-29T00:05:00+09:00"},
        ]
        assert keys == ["claude-sessions/proj/s1.jsonl"]

    def test_skips_no_repo(self):
        store = _make_store()
        client = _make_session_client()
        client.list_session_objects.return_value = [
            {"Key": "claude-sessions/proj/s1.jsonl"},
        ]
        client.read_repo_name.return_value = None

        items, keys = store._build_items(client)

        assert items == []
        assert keys == []
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

        items, keys = store._build_items(client)

        assert items == []
        assert keys == []


class TestWriteItems:
    def test_uses_update_item(self):
        store = _make_store()

        items = [
            {
                "date": "2026-03-28",
                "repo#session_id": "repo#s1",
                "repo": "repo",
                "updated_at": "2026-03-28T00:00:00+00:00",
            },
            {
                "date": "2026-03-29",
                "repo#session_id": "repo#s2",
                "repo": "repo",
                "updated_at": "2026-03-29T00:00:00+00:00",
            },
        ]

        store._write_items(items)

        assert store.table.update_item.call_count == 2
        first_call = store.table.update_item.call_args_list[0]
        assert first_call.kwargs["Key"] == {
            "date": "2026-03-28",
            "repo#session_id": "repo#s1",
        }
        assert "UpdateExpression" in first_call.kwargs


class TestIngest:
    def test_returns_processed_keys(self):
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

        keys = store.ingest(client)

        assert keys == ["claude-sessions/proj/s1.jsonl"]
        store.table.update_item.assert_called_once()


class TestFetchSessions:
    def test_returns_session_activity(self):
        store = _make_store()
        store.table.query.return_value = {
            "Items": [
                {
                    "date": "2026-03-28",
                    "repo#session_id": "my-repo#s1",
                    "repo": "my-repo",
                    "project": "proj",
                    "start_time": "2026-03-28T10:00:00+09:00",
                    "end_time": "2026-03-28T11:00:00+09:00",
                    "user_messages": ["Fix bug"],
                    "tools_used": ["Edit", "Read"],
                },
            ],
        }

        activity = store.fetch_sessions("2026-03-28")

        assert bool(activity)
        assert "my-repo" in activity
        sessions = activity.get("my-repo")
        assert len(sessions) == 1
        assert sessions[0]["session_id"] == "s1"
        assert sessions[0]["user_messages"] == ["Fix bug"]

    def test_groups_by_repo(self):
        store = _make_store()
        store.table.query.return_value = {
            "Items": [
                {
                    "date": "2026-03-28",
                    "repo#session_id": "repo-a#s1",
                    "repo": "repo-a",
                    "project": "proj-a",
                    "start_time": "2026-03-28T10:00:00+09:00",
                    "end_time": "2026-03-28T11:00:00+09:00",
                    "user_messages": ["msg1"],
                    "tools_used": [],
                },
                {
                    "date": "2026-03-28",
                    "repo#session_id": "repo-b#s2",
                    "repo": "repo-b",
                    "project": "proj-b",
                    "start_time": "2026-03-28T12:00:00+09:00",
                    "end_time": "2026-03-28T13:00:00+09:00",
                    "user_messages": ["msg2"],
                    "tools_used": [],
                },
            ],
        }

        activity = store.fetch_sessions("2026-03-28")

        assert set(activity.keys()) == {"repo-a", "repo-b"}

    def test_returns_empty_for_no_data(self):
        store = _make_store()
        store.table.query.return_value = {"Items": []}

        activity = store.fetch_sessions("2026-03-28")

        assert not activity

    def test_sorts_sessions_by_start_time(self):
        store = _make_store()
        store.table.query.return_value = {
            "Items": [
                {
                    "date": "2026-03-28",
                    "repo#session_id": "repo#s2",
                    "repo": "repo",
                    "project": "proj",
                    "start_time": "2026-03-28T14:00:00+09:00",
                    "end_time": "2026-03-28T15:00:00+09:00",
                    "user_messages": ["later"],
                    "tools_used": [],
                },
                {
                    "date": "2026-03-28",
                    "repo#session_id": "repo#s1",
                    "repo": "repo",
                    "project": "proj",
                    "start_time": "2026-03-28T10:00:00+09:00",
                    "end_time": "2026-03-28T11:00:00+09:00",
                    "user_messages": ["earlier"],
                    "tools_used": [],
                },
            ],
        }

        activity = store.fetch_sessions("2026-03-28")

        sessions = activity.get("repo")
        assert sessions[0]["user_messages"] == ["earlier"]
        assert sessions[1]["user_messages"] == ["later"]

    def test_includes_session_commits(self):
        store = _make_store()
        store.table.query.return_value = {
            "Items": [
                {
                    "date": "2026-03-28",
                    "repo#session_id": "repo#s1",
                    "repo": "repo",
                    "project": "proj",
                    "start_time": "2026-03-28T10:00:00+09:00",
                    "end_time": "2026-03-28T11:00:00+09:00",
                    "user_messages": ["Fix bug"],
                    "tools_used": ["Edit"],
                    "session_commits": [
                        {"sha": "a1b2c3d", "message": "Fix the bug"},
                    ],
                },
            ],
        }

        activity = store.fetch_sessions("2026-03-28")

        sessions = activity.get("repo")
        assert sessions[0]["session_commits"] == [
            {"sha": "a1b2c3d", "message": "Fix the bug"},
        ]

    def test_defaults_session_commits_when_missing(self):
        store = _make_store()
        store.table.query.return_value = {
            "Items": [
                {
                    "date": "2026-03-28",
                    "repo#session_id": "repo#s1",
                    "repo": "repo",
                    "project": "proj",
                    "start_time": "2026-03-28T10:00:00+09:00",
                    "end_time": "2026-03-28T11:00:00+09:00",
                    "user_messages": ["msg"],
                    "tools_used": [],
                },
            ],
        }

        activity = store.fetch_sessions("2026-03-28")

        sessions = activity.get("repo")
        assert sessions[0]["session_commits"] == []


class TestScanBackfillDates:
    def test_returns_unreported_dates(self):
        store = _make_store()
        store.table.scan.return_value = {
            "Items": [
                {"date": "2026-03-26"},
                {"date": "2026-03-27"},
            ],
        }

        dates = store.scan_backfill_dates(date(2026, 3, 28))

        assert dates == [date(2026, 3, 26), date(2026, 3, 27)]

    def test_excludes_primary_date(self):
        store = _make_store()
        store.table.scan.return_value = {
            "Items": [
                {"date": "2026-03-28"},
            ],
        }

        dates = store.scan_backfill_dates(date(2026, 3, 28))

        assert dates == []

    def test_deduplicates_dates(self):
        store = _make_store()
        store.table.scan.return_value = {
            "Items": [
                {"date": "2026-03-27"},
                {"date": "2026-03-27"},
            ],
        }

        dates = store.scan_backfill_dates(date(2026, 3, 28))

        assert dates == [date(2026, 3, 27)]

    def test_returns_empty_when_all_reported(self):
        store = _make_store()
        store.table.scan.return_value = {"Items": []}

        dates = store.scan_backfill_dates(date(2026, 3, 28))

        assert dates == []


class TestMarkReported:
    def test_updates_all_items_for_date(self):
        store = _make_store()
        store.table.query.return_value = {
            "Items": [
                {"date": "2026-03-28", "repo#session_id": "repo#s1"},
                {"date": "2026-03-28", "repo#session_id": "repo#s2"},
            ],
        }

        store.mark_reported("2026-03-28")

        assert store.table.update_item.call_count == 2
        for c in store.table.update_item.call_args_list:
            assert c.kwargs["UpdateExpression"] == "SET reported_at = :ts"
            assert ":ts" in c.kwargs["ExpressionAttributeValues"]

    def test_no_op_for_empty_date(self):
        store = _make_store()
        store.table.query.return_value = {"Items": []}

        store.mark_reported("2026-03-28")

        store.table.update_item.assert_not_called()
