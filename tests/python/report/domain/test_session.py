"""Tests for SessionActivity formatting and repo exclusion."""

from datetime import UTC, datetime

from report.domain.session import SessionActivity

from .. import _builders


class TestSessionActivityFormat:
    def test_empty_sessions(self):
        activity = SessionActivity({})
        assert activity.format() == "# Claude Code セッション\nセッションなし"

    def test_with_sessions(self):
        data = {
            "my-repo": [
                _builders.session_entry(
                    session_id="abc",
                    project="my-repo",
                    start=_builders.jst(2026, 3, 28, 10),
                    end=_builders.jst(2026, 3, 28, 11, 30),
                    messages=("Fix the bug",),
                    tools=("Read", "Edit"),
                )
            ],
        }
        result = SessionActivity(data).format()
        assert "## プロジェクト: my-repo" in result
        assert "### セッション 1 (10:00 - 11:30)" in result
        assert "- ユーザー: Fix the bug" in result
        assert "- ツール使用: Read, Edit" in result

    def test_format_time_utc_to_jst(self):
        # UTC 15:00 = JST 00:00
        assert (
            SessionActivity._format_time(datetime(2026, 3, 28, 15, 0, tzinfo=UTC))
            == "00:00"
        )

    def test_get_with_default(self):
        activity = SessionActivity({"repo": []})
        assert activity.get("repo") == []
        assert activity.get("missing") is None
        assert activity.get("missing", []) == []


class TestSessionActivityWithout:
    def test_removes_named_repos(self):
        activity = SessionActivity({"a": [], "b": [], "c": []})
        result = activity.without(["b"])
        assert set(result.keys()) == {"a", "c"}

    def test_returns_new_instance(self):
        activity = SessionActivity({"a": [], "b": []})
        result = activity.without(["a"])
        assert result is not activity
        assert set(activity.keys()) == {"a", "b"}

    def test_ignores_unknown_names(self):
        activity = SessionActivity({"a": []})
        assert set(activity.without(["missing"]).keys()) == {"a"}

    def test_empty_exclusion_returns_full_copy(self):
        activity = SessionActivity({"a": [], "b": []})
        assert set(activity.without([]).keys()) == {"a", "b"}
