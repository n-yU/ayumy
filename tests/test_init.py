"""Tests for report package core utilities."""

from datetime import date, datetime, timedelta
from unittest.mock import patch

import pytest

from report import (
    JST,
    GitHubActivity,
    SessionActivity,
    date_to_range,
    get_target_date_range,
    require_env,
)


class TestRequireEnv:
    def test_returns_value_when_set(self, monkeypatch):
        monkeypatch.setenv("TEST_VAR", "hello")
        assert require_env("TEST_VAR") == "hello"

    def test_raises_when_missing(self, monkeypatch):
        monkeypatch.delenv("TEST_VAR", raising=False)
        with pytest.raises(ValueError, match="TEST_VAR is not set"):
            require_env("TEST_VAR")

    def test_raises_when_empty(self, monkeypatch):
        monkeypatch.setenv("TEST_VAR", "")
        with pytest.raises(ValueError, match="TEST_VAR is not set"):
            require_env("TEST_VAR")


class TestDateToRange:
    def test_returns_full_jst_day(self):
        since, until = date_to_range(date(2026, 3, 28))
        assert since == datetime(2026, 3, 28, 0, 0, tzinfo=JST)
        assert until == datetime(2026, 3, 29, 0, 0, tzinfo=JST)

    def test_range_spans_24_hours(self):
        since, until = date_to_range(date(2026, 1, 1))
        assert until - since == timedelta(days=1)


class TestGetTargetDateRange:
    @patch("report.datetime")
    def test_scheduled_returns_previous_day(self, mock_dt):
        mock_now = datetime(2026, 3, 28, 15, 30, 0, tzinfo=JST)
        mock_dt.now.return_value = mock_now
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

        since, until = get_target_date_range()
        assert since == datetime(2026, 3, 27, 0, 0, tzinfo=JST)
        assert until == datetime(2026, 3, 28, 0, 0, tzinfo=JST)

    @patch("report.datetime")
    def test_manual_returns_today_to_now(self, mock_dt):
        mock_now = datetime(2026, 3, 28, 15, 30, 0, tzinfo=JST)
        mock_dt.now.return_value = mock_now
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

        since, until = get_target_date_range("manual")
        assert since == datetime(2026, 3, 28, 0, 0, tzinfo=JST)
        assert until == mock_now


class TestGitHubActivityFormat:
    def test_empty_activity(self):
        activity = GitHubActivity({})
        assert activity.format() == "# GitHub アクティビティ\nアクティビティなし"

    def test_with_commits_prs_issues(self):
        data = {
            "my-repo": {
                "commits": [{"message": "Fix bug"}],
                "pulls": [{"number": 1, "title": "Add feature", "state": "merged", "labels": ["enhancement"]}],
                "issues": [{"number": 2, "title": "Bug report", "state": "closed", "labels": []}],
            },
        }
        result = GitHubActivity(data).format()
        assert "## my-repo" in result
        assert "- Fix bug" in result
        assert "- [merged] #1 Add feature (enhancement)" in result
        assert "- [closed] #2 Bug report" in result

    def test_repos_sorted_alphabetically(self):
        data = {
            "z-repo": {"commits": [{"message": "z"}], "pulls": [], "issues": []},
            "a-repo": {"commits": [{"message": "a"}], "pulls": [], "issues": []},
        }
        result = GitHubActivity(data).format()
        assert result.index("a-repo") < result.index("z-repo")

    def test_bool_and_contains(self):
        activity = GitHubActivity({"repo": {"commits": [], "pulls": [], "issues": []}})
        assert bool(activity)
        assert "repo" in activity
        assert "other" not in activity
        assert not bool(GitHubActivity({}))


class TestSessionActivityFormat:
    def test_empty_sessions(self):
        activity = SessionActivity({})
        assert activity.format() == "# Claude Code セッション\nセッションなし"

    def test_with_sessions(self):
        data = {
            "my-repo": [{
                "session_id": "abc",
                "project": "my-repo",
                "start_time": "2026-03-28T10:00:00+09:00",
                "end_time": "2026-03-28T11:30:00+09:00",
                "user_messages": ["Fix the bug"],
                "tools_used": ["Read", "Edit"],
            }],
        }
        result = SessionActivity(data).format()
        assert "## プロジェクト: my-repo" in result
        assert "### セッション 1 (10:00 - 11:30)" in result
        assert "- ユーザー: Fix the bug" in result
        assert "- ツール使用: Read, Edit" in result

    def test_format_time_empty(self):
        assert SessionActivity._format_time("") == "??:??"

    def test_format_time_utc_to_jst(self):
        # UTC 15:00 = JST 00:00
        assert SessionActivity._format_time("2026-03-28T15:00:00+00:00") == "00:00"

    def test_get_with_default(self):
        activity = SessionActivity({"repo": []})
        assert activity.get("repo") == []
        assert activity.get("missing") is None
        assert activity.get("missing", []) == []
