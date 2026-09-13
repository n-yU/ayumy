"""Tests for the session DynamoDB store."""

import logging
from datetime import date, datetime

import botocore.exceptions
import pytest

from report.shared import dates

from . import _builders


def _client_error(code):
    return botocore.exceptions.ClientError({"Error": {"Code": code}}, "UpdateItem")


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


def _write_item(**overrides):
    """Minimal `_write_items` input; overrides replace any default."""
    base = {
        "date": "2026-03-28",
        "repo#session_id": "repo#s1",
        "repo": "repo",
        "user_messages": ["msg"],
        "updated_at": "2026-03-28T00:00:00+00:00",
    }
    base.update(overrides)
    return base


@pytest.fixture
def written_hashes(store):
    """Return a callable that writes the given items and collects the content hashes sent to DynamoDB."""

    def _collect(*items):
        store._write_items(list(items))
        return {
            c.kwargs["ExpressionAttributeValues"][":v_content_hash"]
            for c in store.table.update_item.call_args_list
        }

    return _collect


class TestWriteItems:
    def test_uses_update_item(self, store):
        items = [
            _write_item(),
            _write_item(
                **{"date": "2026-03-29", "repo#session_id": "repo#s2"},
                updated_at="2026-03-29T00:00:00+00:00",
            ),
        ]

        store._write_items(items)

        assert store.table.update_item.call_count == 2
        first_call = store.table.update_item.call_args_list[0]
        assert first_call.kwargs["Key"] == {
            "date": "2026-03-28",
            "repo#session_id": "repo#s1",
        }
        assert "UpdateExpression" in first_call.kwargs

    def test_writes_content_hash_under_condition(self, store):
        store._write_items([_write_item()])

        kwargs = store.table.update_item.call_args.kwargs
        assert kwargs["ExpressionAttributeNames"]["#f_content_hash"] == "content_hash"
        assert kwargs["ExpressionAttributeValues"][":v_content_hash"]
        assert kwargs["ConditionExpression"] == (
            "attribute_not_exists(#f_content_hash) OR #f_content_hash <> :v_content_hash"
        )

    @pytest.mark.parametrize(
        ("second_item", "distinct_hashes"),
        [
            pytest.param(
                {"updated_at": "2026-03-29T00:00:00+00:00"},
                1,
                id="write_time_excluded",
            ),
            pytest.param({"user_messages": ["msg", "more"]}, 2, id="content_included"),
        ],
    )
    def test_content_hash_covers_report_content_only(
        self, written_hashes, second_item, distinct_hashes
    ):
        hashes = written_hashes(_write_item(), _write_item(**second_item))

        assert len(hashes) == distinct_hashes

    def test_skips_items_rejected_by_condition(self, store, caplog):
        store.table.update_item.side_effect = [
            _client_error("ConditionalCheckFailedException"),
            None,
        ]

        with caplog.at_level(logging.INFO, logger="report.session.store"):
            store._write_items(
                [
                    _write_item(),
                    _write_item(**{"repo#session_id": "repo#s2"}),
                ]
            )

        assert store.table.update_item.call_count == 2
        assert "Skipped 1 unchanged item(s)" in caplog.text

    def test_reraises_other_client_errors(self, store):
        store.table.update_item.side_effect = _client_error(
            "ProvisionedThroughputExceededException"
        )

        with pytest.raises(botocore.exceptions.ClientError):
            store._write_items([_write_item()])


class TestIngest:
    def test_returns_processed_keys(self, store, stub_session_log):
        client = stub_session_log(_builders.user("2026-03-28T10:00:00+09:00", "Hello"))

        keys = store.ingest(client)

        assert keys == [_builders.SESSION_KEY]
        store.table.update_item.assert_called_once()

    def test_returns_processed_keys_for_unchanged_items(self, store, stub_session_log):
        client = stub_session_log(_builders.user("2026-03-28T10:00:00+09:00", "Hello"))
        store.table.update_item.side_effect = _client_error(
            "ConditionalCheckFailedException"
        )

        assert store.ingest(client) == [_builders.SESSION_KEY]


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
        assert session["session_commits"] == [
            {
                "sha": "a1b2c3d",
                "message": "Fix the bug",
                "timestamp": datetime(2026, 3, 28, 10, 30, tzinfo=dates.JST),
            },
        ]
        assert session["session_pulls"] == [87, 82]
        assert session["session_issues"] == [84]

    def test_parses_session_timestamps(self, store):
        store.table.query.return_value = {"Items": [_item()]}

        session = store.fetch_sessions("2026-03-28").get("repo")[0]

        assert session["start_time"] == datetime(2026, 3, 28, 10, 0, tzinfo=dates.JST)
        assert session["end_time"] == datetime(2026, 3, 28, 11, 0, tzinfo=dates.JST)

    def test_leaves_timestamp_absent_on_legacy_session_commit(self, store):
        legacy_commit = {"sha": "a1b2c3d", "message": "Fix the bug"}
        store.table.query.return_value = {
            "Items": [_item(session_commits=[legacy_commit])]
        }

        session = store.fetch_sessions("2026-03-28").get("repo")[0]

        assert session["session_commits"] == [legacy_commit]

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
