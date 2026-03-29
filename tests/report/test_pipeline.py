"""Tests for report generation pipeline."""

from datetime import date, datetime
from unittest.mock import MagicMock, patch

import pytest

from report import GitHubActivity, JST, SessionActivity
from report.pipeline import MAX_BACKFILL, process_date, run


def _make_report(repos=None):
    """Create a minimal report dict."""
    return {
        "summary": "Daily summary",
        "repositories": repos or [],
    }


def _make_clients():
    """Create mocked client instances."""
    return {
        "session_client": MagicMock(),
        "github_client": MagicMock(),
        "notion_client": MagicMock(),
        "summary_client": MagicMock(),
        "slack_client": MagicMock(),
    }


def _empty_activity():
    """Create empty activity objects that behave as falsy."""
    session = SessionActivity({})
    github = GitHubActivity({})
    return session, github


def _nonempty_activity(repo="my-repo"):
    """Create real activity objects with minimal valid data."""
    session = SessionActivity({repo: [{
        "session_id": "s1", "project": repo,
        "start_time": "2026-03-28T10:00:00+09:00",
        "end_time": "2026-03-28T11:00:00+09:00",
        "user_messages": ["Fix bug"], "tools_used": ["Edit"],
    }]})
    github = GitHubActivity({repo: {
        "commits": [{"message": "Fix bug", "sha": "abc", "author": "user",
                      "date": "2026-03-28T10:00:00"}],
        "pulls": [], "issues": [],
    }})
    return session, github


class TestProcessDate:
    def test_skips_when_no_activity(self):
        clients = _make_clients()
        since = datetime(2026, 3, 28, 0, 0, tzinfo=JST)
        until = datetime(2026, 3, 29, 0, 0, tzinfo=JST)

        session, github = _empty_activity()
        clients["session_client"].fetch_sessions.return_value = session
        clients["github_client"].fetch_activity.return_value = github

        process_date(since, until, **clients, allowed_tags=[], allowed_statuses=[])

        clients["summary_client"].generate_summary.assert_not_called()
        clients["notion_client"].create_report_pages.assert_not_called()
        clients["slack_client"].notify.assert_not_called()

    def test_generates_report_and_publishes(self):
        clients = _make_clients()
        since = datetime(2026, 3, 28, 0, 0, tzinfo=JST)
        until = datetime(2026, 3, 29, 0, 0, tzinfo=JST)

        session, github = _nonempty_activity("my-repo")
        clients["session_client"].fetch_sessions.return_value = session
        clients["github_client"].fetch_activity.return_value = github

        report = _make_report([{
            "name": "my-repo", "summary": "work", "achievements": [],
            "ongoing": [], "claude_code": "", "tags": [], "status": "Active",
        }])
        clients["summary_client"].generate_summary.return_value = report
        clients["notion_client"].create_report_pages.return_value = [
            ("my-repo", "https://notion.so/page1"),
        ]

        process_date(
            since, until, **clients,
            allowed_tags=["CI/CD"], allowed_statuses=["Active"],
        )

        clients["summary_client"].generate_summary.assert_called_once()
        clients["notion_client"].create_report_pages.assert_called_once()
        clients["slack_client"].notify.assert_called_once()

    def test_notifies_validation_errors(self):
        clients = _make_clients()
        since = datetime(2026, 3, 28, 0, 0, tzinfo=JST)
        until = datetime(2026, 3, 29, 0, 0, tzinfo=JST)

        session, github = _nonempty_activity("repo")
        clients["session_client"].fetch_sessions.return_value = session
        clients["github_client"].fetch_activity.return_value = github

        report = _make_report([{
            "name": "repo", "summary": "", "achievements": [],
            "ongoing": [], "claude_code": "", "tags": ["BadTag"], "status": "Active",
        }])
        clients["summary_client"].generate_summary.return_value = report
        clients["notion_client"].create_report_pages.return_value = []

        process_date(
            since, until, **clients,
            allowed_tags=["CI/CD"], allowed_statuses=["Active"],
        )

        clients["slack_client"].notify_validation_errors.assert_called_once()

    def test_detects_skipped_repos(self):
        clients = _make_clients()
        since = datetime(2026, 3, 28, 0, 0, tzinfo=JST)
        until = datetime(2026, 3, 29, 0, 0, tzinfo=JST)

        session, github = _nonempty_activity("repo")
        clients["session_client"].fetch_sessions.return_value = session
        clients["github_client"].fetch_activity.return_value = github

        report = _make_report([{
            "name": "unknown-repo", "summary": "", "achievements": [],
            "ongoing": [], "claude_code": "", "tags": [], "status": "",
        }])
        clients["summary_client"].generate_summary.return_value = report
        clients["notion_client"].create_report_pages.return_value = []

        process_date(
            since, until, **clients,
            allowed_tags=[], allowed_statuses=[],
        )

        call_args = clients["slack_client"].notify.call_args
        skipped = call_args[0][3] if len(call_args[0]) > 3 else call_args[1].get("skipped_repos", [])
        assert "unknown-repo" in skipped


class TestRun:
    @patch("report.pipeline.get_target_date_range")
    @patch("report.pipeline.require_env")
    @patch("report.pipeline.SlackClient")
    @patch("report.pipeline.SessionClient")
    @patch("report.pipeline.GitHubClient")
    @patch("report.pipeline.NotionClient")
    @patch("report.pipeline.SummaryClient")
    def test_processes_primary_date(
        self, MockSummary, MockNotion, MockGitHub, MockSession,
        MockSlack, mock_require_env, mock_date_range,
    ):
        since = datetime(2026, 3, 28, 0, 0, tzinfo=JST)
        until = datetime(2026, 3, 29, 0, 0, tzinfo=JST)
        mock_date_range.return_value = (since, until)
        mock_require_env.side_effect = lambda k: f"fake-{k}"

        session_client = MockSession.return_value
        session_client.scan_entry_dates.return_value = {}
        session_client.fetch_sessions.return_value = SessionActivity({})
        session_client.snapshot_keys.return_value = 0
        session_client.archive_sessions.return_value = 0

        github_client = MockGitHub.return_value
        github_client.fetch_activity.return_value = GitHubActivity({})

        notion_client = MockNotion.return_value
        notion_client.fetch_allowlists.return_value = ([], [])

        run(source=None)

        session_client.scan_entry_dates.assert_called_once()
        session_client.archive_sessions.assert_called_once()
        slack_client = MockSlack.return_value
        slack_client = MockSlack.return_value
        slack_client.notify_metrics.assert_called_once()
        elapsed, peak_mb, limit_mb = slack_client.notify_metrics.call_args[0]
        assert elapsed >= 0
        assert peak_mb >= 0
        assert limit_mb is None

    @patch("report.pipeline.get_target_date_range")
    @patch("report.pipeline.require_env")
    @patch("report.pipeline.SlackClient")
    @patch("report.pipeline.SessionClient")
    @patch("report.pipeline.GitHubClient")
    @patch("report.pipeline.NotionClient")
    @patch("report.pipeline.SummaryClient")
    def test_passes_memory_limit_to_metrics(
        self, MockSummary, MockNotion, MockGitHub, MockSession,
        MockSlack, mock_require_env, mock_date_range,
    ):
        since = datetime(2026, 3, 28, 0, 0, tzinfo=JST)
        until = datetime(2026, 3, 29, 0, 0, tzinfo=JST)
        mock_date_range.return_value = (since, until)
        mock_require_env.side_effect = lambda k: f"fake-{k}"

        session_client = MockSession.return_value
        session_client.scan_entry_dates.return_value = {}
        session_client.fetch_sessions.return_value = SessionActivity({})
        session_client.snapshot_keys.return_value = 0
        session_client.archive_sessions.return_value = 0

        github_client = MockGitHub.return_value
        github_client.fetch_activity.return_value = GitHubActivity({})

        notion_client = MockNotion.return_value
        notion_client.fetch_allowlists.return_value = ([], [])

        slack_client = MockSlack.return_value

        run(source=None, memory_limit_mb=512)

        slack_client.notify_metrics.assert_called_once()
        _, _, limit_mb = slack_client.notify_metrics.call_args[0]
        assert limit_mb == 512

    @patch("report.pipeline.get_target_date_range")
    @patch("report.pipeline.require_env")
    @patch("report.pipeline.SlackClient")
    @patch("report.pipeline.SessionClient")
    @patch("report.pipeline.GitHubClient")
    @patch("report.pipeline.NotionClient")
    @patch("report.pipeline.SummaryClient")
    def test_backfills_past_dates(
        self, MockSummary, MockNotion, MockGitHub, MockSession,
        MockSlack, mock_require_env, mock_date_range,
    ):
        since = datetime(2026, 3, 28, 0, 0, tzinfo=JST)
        until = datetime(2026, 3, 29, 0, 0, tzinfo=JST)
        mock_date_range.return_value = (since, until)
        mock_require_env.side_effect = lambda k: f"fake-{k}"

        session_client = MockSession.return_value
        # Two past dates detected from session logs
        session_client.scan_entry_dates.return_value = {
            "key1": {date(2026, 3, 26), date(2026, 3, 27)},
        }
        session_client.fetch_sessions.return_value = SessionActivity({})
        session_client.snapshot_keys.return_value = 0
        session_client.archive_sessions.return_value = 2

        github_client = MockGitHub.return_value
        github_client.fetch_activity.return_value = GitHubActivity({})

        notion_client = MockNotion.return_value
        notion_client.fetch_allowlists.return_value = ([], [])

        run(source=None)

        # 2 backfill dates + 1 primary = 3 calls
        assert session_client.fetch_sessions.call_count == 3

    @patch("report.pipeline.get_target_date_range")
    @patch("report.pipeline.require_env")
    @patch("report.pipeline.SlackClient")
    @patch("report.pipeline.SessionClient")
    @patch("report.pipeline.GitHubClient")
    @patch("report.pipeline.NotionClient")
    @patch("report.pipeline.SummaryClient")
    def test_backfill_limited_to_max(
        self, MockSummary, MockNotion, MockGitHub, MockSession,
        MockSlack, mock_require_env, mock_date_range,
    ):
        since = datetime(2026, 3, 28, 0, 0, tzinfo=JST)
        until = datetime(2026, 3, 29, 0, 0, tzinfo=JST)
        mock_date_range.return_value = (since, until)
        mock_require_env.side_effect = lambda k: f"fake-{k}"

        session_client = MockSession.return_value
        # 5 past dates, but only MAX_BACKFILL most recent should be processed
        session_client.scan_entry_dates.return_value = {
            "key1": {
                date(2026, 3, 23), date(2026, 3, 24), date(2026, 3, 25),
                date(2026, 3, 26), date(2026, 3, 27),
            },
        }
        session_client.fetch_sessions.return_value = SessionActivity({})
        session_client.snapshot_keys.return_value = 0
        session_client.archive_sessions.return_value = 0

        github_client = MockGitHub.return_value
        github_client.fetch_activity.return_value = GitHubActivity({})

        notion_client = MockNotion.return_value
        notion_client.fetch_allowlists.return_value = ([], [])

        run(source=None)

        # MAX_BACKFILL + 1 primary
        assert session_client.fetch_sessions.call_count == MAX_BACKFILL + 1

    @patch("report.pipeline.get_target_date_range")
    @patch("report.pipeline.require_env")
    @patch("report.pipeline.SlackClient")
    @patch("report.pipeline.SessionClient")
    @patch("report.pipeline.GitHubClient")
    @patch("report.pipeline.NotionClient")
    @patch("report.pipeline.SummaryClient")
    def test_rollback_and_notify_on_failure(
        self, MockSummary, MockNotion, MockGitHub, MockSession,
        MockSlack, mock_require_env, mock_date_range,
    ):
        since = datetime(2026, 3, 28, 0, 0, tzinfo=JST)
        until = datetime(2026, 3, 29, 0, 0, tzinfo=JST)
        mock_date_range.return_value = (since, until)
        mock_require_env.side_effect = lambda k: f"fake-{k}"

        session_client = MockSession.return_value
        session_client.scan_entry_dates.return_value = {}
        session_client.fetch_sessions.side_effect = RuntimeError("S3 error")
        session_client.snapshot_keys.return_value = 0

        github_client = MockGitHub.return_value

        notion_client = MockNotion.return_value
        notion_client.fetch_allowlists.return_value = ([], [])

        slack_client = MockSlack.return_value

        with pytest.raises(RuntimeError, match="S3 error"):
            run(source=None)

        session_client.rollback_keys.assert_called_once_with(0)
        slack_client.notify_error.assert_called_once()
        # Should not archive on failure
        session_client.archive_sessions.assert_not_called()
        # Metrics should still be sent on failure (finally block)
        slack_client.notify_metrics.assert_called_once()

    @patch("report.pipeline.get_target_date_range")
    @patch("report.pipeline.require_env")
    @patch("report.pipeline.SlackClient")
    @patch("report.pipeline.SessionClient")
    @patch("report.pipeline.GitHubClient")
    @patch("report.pipeline.NotionClient")
    @patch("report.pipeline.SummaryClient")
    def test_backfill_failure_does_not_stop_primary(
        self, MockSummary, MockNotion, MockGitHub, MockSession,
        MockSlack, mock_require_env, mock_date_range,
    ):
        since = datetime(2026, 3, 28, 0, 0, tzinfo=JST)
        until = datetime(2026, 3, 29, 0, 0, tzinfo=JST)
        mock_date_range.return_value = (since, until)
        mock_require_env.side_effect = lambda k: f"fake-{k}"

        session_client = MockSession.return_value
        session_client.scan_entry_dates.return_value = {
            "key1": {date(2026, 3, 27)},
        }

        call_count = 0

        def fetch_sessions_side_effect(s, u):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Backfill date fails
                raise RuntimeError("backfill error")
            return SessionActivity({})

        session_client.fetch_sessions.side_effect = fetch_sessions_side_effect
        session_client.snapshot_keys.return_value = 0
        session_client.archive_sessions.return_value = 0

        github_client = MockGitHub.return_value
        github_client.fetch_activity.return_value = GitHubActivity({})

        notion_client = MockNotion.return_value
        notion_client.fetch_allowlists.return_value = ([], [])

        slack_client = MockSlack.return_value

        run(source=None)

        # Backfill failure notified but primary still processed
        assert session_client.fetch_sessions.call_count == 2
        slack_client.notify_error.assert_called_once()
        backfill_since = slack_client.notify_error.call_args[0][0]
        assert backfill_since.date() == date(2026, 3, 27)
        session_client.archive_sessions.assert_called_once()
