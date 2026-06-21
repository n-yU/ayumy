"""Tests for SessionActivity formatting."""

import pytest

from report import SessionActivity


class TestSessionActivityFormat:
    def test_empty_sessions(self):
        activity = SessionActivity({})
        assert activity.format() == "# Claude Code セッション\nセッションなし"

    def test_with_sessions(self):
        data = {
            "my-repo": [
                {
                    "session_id": "abc",
                    "project": "my-repo",
                    "start_time": "2026-03-28T10:00:00+09:00",
                    "end_time": "2026-03-28T11:30:00+09:00",
                    "user_messages": ["Fix the bug"],
                    "tools_used": ["Read", "Edit"],
                }
            ],
        }
        result = SessionActivity(data).format()
        assert "## プロジェクト: my-repo" in result
        assert "### セッション 1 (10:00 - 11:30)" in result
        assert "- ユーザー: Fix the bug" in result
        assert "- ツール使用: Read, Edit" in result

    def test_format_time_empty_raises(self):
        with pytest.raises(ValueError, match="Session timestamp is missing"):
            SessionActivity._format_time("")

    def test_format_time_utc_to_jst(self):
        # UTC 15:00 = JST 00:00
        assert SessionActivity._format_time("2026-03-28T15:00:00+00:00") == "00:00"

    def test_get_with_default(self):
        activity = SessionActivity({"repo": []})
        assert activity.get("repo") == []
        assert activity.get("missing") is None
        assert activity.get("missing", []) == []
