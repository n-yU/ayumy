"""Tests for SessionStore."""

from datetime import date
from unittest.mock import MagicMock

import pytest

from ._builders import SESSION_KEY, jsonl, user


def _item(**overrides):
    """DynamoDB item with `SessionInfo` required keys filled in; overrides replace any default."""
    base = {
        "date": "2026-03-28",
        "repo#session_id": "repo#s1",
        "repo": "repo",
        "project": "proj",
        "start_time": "2026-03-28T10:00:00+09:00",
        "end_time": "2026-03-28T11:00:00+09:00",
        "user_messages": ["msg"],
        "tools_used": [],
        "session_commits": [],
        "session_pulls": [],
        "session_issues": [],
    }
    base.update(overrides)
    return base


class TestWriteItems:
    def test_uses_update_item(self, store):
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
    def test_returns_processed_keys(self, store, session_client):
        session_client.list_session_objects.return_value = [{"Key": SESSION_KEY}]
        session_client.read_repo_name.return_value = "repo"
        body = MagicMock()
        body.read.return_value = jsonl(
            user("2026-03-28T10:00:00+09:00", "Hello")
        ).encode("utf-8")
        session_client.s3.get_object.return_value = {"Body": body}

        keys = store.ingest(session_client)

        assert keys == [SESSION_KEY]
        store.table.update_item.assert_called_once()


class TestFetchSessions:
    def test_returns_session_activity(self, store):
        store.table.query.return_value = {
            "Items": [
                _item(
                    **{"repo#session_id": "my-repo#s1"},
                    repo="my-repo",
                    user_messages=["Fix bug"],
                    tools_used=["Edit", "Read"],
                ),
            ],
        }

        activity = store.fetch_sessions("2026-03-28")

        assert bool(activity)
        assert "my-repo" in activity
        sessions = activity.get("my-repo")
        assert len(sessions) == 1
        assert sessions[0]["session_id"] == "s1"
        assert sessions[0]["user_messages"] == ["Fix bug"]

    def test_groups_by_repo(self, store):
        store.table.query.return_value = {
            "Items": [
                _item(
                    **{"repo#session_id": "repo-a#s1"},
                    repo="repo-a",
                    project="proj-a",
                    user_messages=["msg1"],
                ),
                _item(
                    **{"repo#session_id": "repo-b#s2"},
                    repo="repo-b",
                    project="proj-b",
                    start_time="2026-03-28T12:00:00+09:00",
                    end_time="2026-03-28T13:00:00+09:00",
                    user_messages=["msg2"],
                ),
            ],
        }

        activity = store.fetch_sessions("2026-03-28")

        assert set(activity.keys()) == {"repo-a", "repo-b"}

    def test_returns_empty_for_no_data(self, store):
        store.table.query.return_value = {"Items": []}

        activity = store.fetch_sessions("2026-03-28")

        assert not activity

    def test_sorts_sessions_by_start_time(self, store):
        store.table.query.return_value = {
            "Items": [
                _item(
                    **{"repo#session_id": "repo#s2"},
                    start_time="2026-03-28T14:00:00+09:00",
                    end_time="2026-03-28T15:00:00+09:00",
                    user_messages=["later"],
                ),
                _item(user_messages=["earlier"]),
            ],
        }

        activity = store.fetch_sessions("2026-03-28")

        sessions = activity.get("repo")
        assert sessions[0]["user_messages"] == ["earlier"]
        assert sessions[1]["user_messages"] == ["later"]

    def test_carries_session_refs_into_session_info(self, store):
        commits = [
            {
                "sha": "a1b2c3d",
                "message": "Fix the bug",
                "timestamp": "2026-03-28T10:30:00+09:00",
            },
        ]
        store.table.query.return_value = {
            "Items": [
                _item(
                    session_commits=commits,
                    session_pulls=[87, 82],
                    session_issues=[84],
                ),
            ],
        }

        activity = store.fetch_sessions("2026-03-28")

        session = activity.get("repo")[0]
        assert session["session_commits"] == commits
        assert session["session_pulls"] == [87, 82]
        assert session["session_issues"] == [84]

    def test_raises_when_session_keys_missing(self, store):
        item = _item()
        del item["session_commits"]
        store.table.query.return_value = {"Items": [item]}

        with pytest.raises(KeyError):
            store.fetch_sessions("2026-03-28")


class TestScanBackfillDates:
    @pytest.mark.parametrize(
        ("scanned_dates", "expected"),
        [
            pytest.param(
                ["2026-03-26", "2026-03-27"],
                [date(2026, 3, 26), date(2026, 3, 27)],
                id="unreported_dates",
            ),
            pytest.param(["2026-03-28"], [], id="primary_date_excluded"),
            pytest.param(
                ["2026-03-27", "2026-03-27"],
                [date(2026, 3, 27)],
                id="duplicates_merged",
            ),
            pytest.param([], [], id="all_reported"),
        ],
    )
    def test_returns_past_dates_needing_report(self, store, scanned_dates, expected):
        store.table.scan.return_value = {"Items": [{"date": d} for d in scanned_dates]}

        assert store.scan_backfill_dates(date(2026, 3, 28)) == expected


class TestMarkReported:
    def test_updates_all_items_for_date(self, store):
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

    def test_no_op_for_empty_date(self, store):
        store.table.query.return_value = {"Items": []}

        store.mark_reported("2026-03-28")

        store.table.update_item.assert_not_called()
