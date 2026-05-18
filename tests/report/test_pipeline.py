"""Tests for report generation pipeline."""

from datetime import date, datetime
from unittest.mock import MagicMock, patch

import pytest

from report import GitHubActivity, JST, SessionActivity
from report.pipeline import MAX_BACKFILL, process_date, run
from report.summarizer import ValidationResult

OWNER = "n-yU"

# Default JST day window used across most tests
SINCE = datetime(2026, 3, 28, 0, 0, tzinfo=JST)
UNTIL = datetime(2026, 3, 29, 0, 0, tzinfo=JST)


def _make_report(repos=None):
    """Create a minimal report dict."""
    return {
        "repositories": repos or [],
    }


def _make_clients():
    """Create mocked client instances."""
    github_client = MagicMock()
    github_client.owner = OWNER
    return {
        "github_client": github_client,
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
                      "date": "2026-03-28T10:00:00",
                      "url": f"https://github.com/{OWNER}/{repo}/commit/abc",
                      "pull_numbers": []}],
        "pulls": [], "issues": [],
    }})
    return session, github


class TestProcessDate:
    def setup_method(self):
        self.clients = _make_clients()
        # Default to a clean ValidationResult so notify_validation_errors is
        # only triggered when a test explicitly sets a truthy result.
        self.clients["summary_client"].validate_report.return_value = ValidationResult()

    def test_skips_when_no_activity(self):
        session, github = _empty_activity()
        self.clients["github_client"].fetch_activity.return_value = github

        process_date(SINCE, UNTIL, session, **self.clients)

        self.clients["summary_client"].generate_summary.assert_not_called()
        self.clients["notion_client"].create_report_pages.assert_not_called()
        self.clients["slack_client"].notify.assert_not_called()
        self.clients["slack_client"].notify_validation_errors.assert_not_called()
        self.clients["slack_client"].notify_no_activity.assert_called_once_with(SINCE)

    def test_generates_report_and_publishes(self):
        session, github = _nonempty_activity("my-repo")
        self.clients["github_client"].fetch_activity.return_value = github

        report = _make_report([{
            "name": "my-repo", "summary": ["work"], "tags": [],
        }])
        self.clients["summary_client"].generate_summary.return_value = report
        self.clients["notion_client"].create_report_pages.return_value = [
            ("my-repo", "https://notion.so/page1"),
        ]

        process_date(SINCE, UNTIL, session, **self.clients)

        self.clients["summary_client"].generate_summary.assert_called_once()
        self.clients["notion_client"].create_report_pages.assert_called_once()
        self.clients["slack_client"].notify.assert_called_once()
        self.clients["slack_client"].notify_validation_errors.assert_not_called()

        # Verify the (target_date, SINCE, UNTIL) trio is passed in order
        notion_args = self.clients["notion_client"].create_report_pages.call_args[0]
        assert notion_args[0] == SINCE
        assert notion_args[1] == SINCE
        assert notion_args[2] == UNTIL

    def test_notifies_validation_errors(self):
        session, github = _nonempty_activity("repo")
        self.clients["github_client"].fetch_activity.return_value = github

        report = _make_report([{
            "name": "repo", "summary": [], "tags": ["BadTag"],
        }])
        self.clients["summary_client"].generate_summary.return_value = report
        invalid = ValidationResult()
        invalid.invalid_tags = {"repo": ["BadTag"]}
        self.clients["summary_client"].validate_report.return_value = invalid
        self.clients["notion_client"].create_report_pages.return_value = []

        process_date(SINCE, UNTIL, session, **self.clients)

        self.clients["slack_client"].notify_validation_errors.assert_called_once()

    def test_supplements_session_commits_when_github_has_none(self):
        session = SessionActivity({"my-repo": [{
            "session_id": "s1", "project": "my-repo",
            "start_time": "2026-03-28T10:00:00+09:00",
            "end_time": "2026-03-28T11:00:00+09:00",
            "user_messages": ["Fix bug"], "tools_used": ["Bash"],
            "session_commits": [
                {"sha": "a1b2c3d", "message": "Fix login bug"},
            ],
        }]})
        # GitHub API has the repo but no commits
        github = GitHubActivity({"my-repo": {
            "commits": [], "pulls": [], "issues": [],
        }})
        self.clients["github_client"].fetch_activity.return_value = github

        report = _make_report([{
            "name": "my-repo", "summary": ["work"], "tags": [],
        }])
        self.clients["summary_client"].generate_summary.return_value = report
        self.clients["notion_client"].create_report_pages.return_value = []

        process_date(SINCE, UNTIL, session, **self.clients)

        # Session commits should be injected into github_activity
        call_args = self.clients["summary_client"].generate_summary.call_args[0]
        github_md = call_args[1]
        assert "Fix login bug" in github_md

        # Verify GitHubActivity passed to Notion also contains the commit
        # with normalized fields (date from session, constructed url, empty author)
        notion_args = self.clients["notion_client"].create_report_pages.call_args[0]
        activity = notion_args[4]
        assert "my-repo" in activity.repos()
        injected = next(
            c for c in activity.repos()["my-repo"]["commits"] if c["sha"] == "a1b2c3d"
        )
        assert injected["date"] == "2026-03-28T10:00:00+09:00"
        assert injected["url"] == f"https://github.com/{OWNER}/my-repo/commit/a1b2c3d"
        assert injected["author"] == ""

    def test_session_commit_uses_per_commit_timestamp_when_present(self):
        session = SessionActivity({"my-repo": [{
            "session_id": "s1", "project": "my-repo",
            "start_time": "2026-03-28T10:00:00+09:00",
            "end_time": "2026-03-28T12:00:00+09:00",
            "user_messages": ["Work"], "tools_used": ["Bash"],
            "session_commits": [
                {"sha": "aaa1111", "message": "Mid commit",
                 "timestamp": "2026-03-28T10:45:00+09:00"},
                {"sha": "bbb2222", "message": "Late commit"},  # legacy: no timestamp
            ],
        }]})
        github = GitHubActivity({"my-repo": {
            "commits": [], "pulls": [], "issues": [],
        }})
        self.clients["github_client"].fetch_activity.return_value = github

        report = _make_report([{
            "name": "my-repo", "summary": ["work"], "tags": [],
        }])
        self.clients["summary_client"].generate_summary.return_value = report
        self.clients["notion_client"].create_report_pages.return_value = []

        process_date(SINCE, UNTIL, session, **self.clients)

        notion_args = self.clients["notion_client"].create_report_pages.call_args[0]
        commits = notion_args[4].repos()["my-repo"]["commits"]
        # Per-commit timestamp wins; legacy entry falls back to session start_time
        first = next(c for c in commits if c["sha"] == "aaa1111")
        second = next(c for c in commits if c["sha"] == "bbb2222")
        assert first["date"] == "2026-03-28T10:45:00+09:00"
        assert second["date"] == "2026-03-28T10:00:00+09:00"

    def test_dedupes_session_commits_across_sessions(self):
        session = SessionActivity({"my-repo": [
            {
                "session_id": "s1", "project": "my-repo",
                "start_time": "2026-03-28T10:00:00+09:00",
                "end_time": "2026-03-28T11:00:00+09:00",
                "user_messages": ["Work"], "tools_used": ["Bash"],
                "session_commits": [
                    {"sha": "aaa1111", "message": "Shared commit",
                     "timestamp": "2026-03-28T10:30:00+09:00"},
                ],
            },
            {
                "session_id": "s2", "project": "my-repo",
                "start_time": "2026-03-28T14:00:00+09:00",
                "end_time": "2026-03-28T15:00:00+09:00",
                "user_messages": ["More work"], "tools_used": ["Bash"],
                "session_commits": [
                    {"sha": "aaa1111", "message": "Shared commit",
                     "timestamp": "2026-03-28T14:30:00+09:00"},
                ],
            },
        ]})
        github = GitHubActivity({"my-repo": {
            "commits": [], "pulls": [], "issues": [],
        }})
        self.clients["github_client"].fetch_activity.return_value = github

        report = _make_report([{
            "name": "my-repo", "summary": ["work"], "tags": [],
        }])
        self.clients["summary_client"].generate_summary.return_value = report
        self.clients["notion_client"].create_report_pages.return_value = []

        process_date(SINCE, UNTIL, session, **self.clients)

        notion_args = self.clients["notion_client"].create_report_pages.call_args[0]
        commits = notion_args[4].repos()["my-repo"]["commits"]
        # Same SHA appearing in two sessions should appear only once,
        # with the earliest occurrence (s1) winning
        assert len(commits) == 1
        assert commits[0]["sha"] == "aaa1111"
        assert commits[0]["date"] == "2026-03-28T10:30:00+09:00"

    def test_supplements_session_commits_for_missing_repo(self):
        session = SessionActivity({"my-repo": [{
            "session_id": "s1", "project": "my-repo",
            "start_time": "2026-03-28T10:00:00+09:00",
            "end_time": "2026-03-28T11:00:00+09:00",
            "user_messages": ["Fix bug"], "tools_used": ["Bash"],
            "session_commits": [
                {"sha": "a1b2c3d", "message": "Fix login bug"},
            ],
        }]})
        # GitHub API returned no data for this repo at all
        github = GitHubActivity({})
        self.clients["github_client"].fetch_activity.return_value = github

        report = _make_report([{
            "name": "my-repo", "summary": ["work"], "tags": [],
        }])
        self.clients["summary_client"].generate_summary.return_value = report
        self.clients["notion_client"].create_report_pages.return_value = []

        process_date(SINCE, UNTIL, session, **self.clients)

        call_args = self.clients["summary_client"].generate_summary.call_args[0]
        github_md = call_args[1]
        assert "Fix login bug" in github_md

        # Verify GitHubActivity passed to Notion contains the injected repo
        # with the same URL construction as when the repo was already present
        notion_args = self.clients["notion_client"].create_report_pages.call_args[0]
        activity = notion_args[4]
        assert "my-repo" in activity.repos()
        commits = activity.repos()["my-repo"]["commits"]
        assert len(commits) == 1
        assert commits[0]["url"] == f"https://github.com/{OWNER}/my-repo/commit/a1b2c3d"

    def test_merges_and_deduplicates_session_commits(self):
        session = SessionActivity({"my-repo": [{
            "session_id": "s1", "project": "my-repo",
            "start_time": "2026-03-28T10:00:00+09:00",
            "end_time": "2026-03-28T11:00:00+09:00",
            "user_messages": ["Work"], "tools_used": ["Bash"],
            "session_commits": [
                {"sha": "abc1234", "message": "Existing commit"},
                {"sha": "def5678", "message": "Squash-lost commit"},
            ],
        }]})
        # GitHub API found one commit with full SHA that overlaps with session
        github = GitHubActivity({"my-repo": {
            "commits": [{
                "sha": "abc1234abcdef1234abcdef1234abcdef12345678",
                "message": "Existing commit",
                "author": "user", "date": "2026-03-28T10:00:00",
                "url": f"https://github.com/{OWNER}/my-repo/commit/abc1234",
                "pull_numbers": [],
            }],
            "pulls": [], "issues": [],
        }})
        self.clients["github_client"].fetch_activity.return_value = github

        report = _make_report([{
            "name": "my-repo", "summary": ["work"], "tags": [],
        }])
        self.clients["summary_client"].generate_summary.return_value = report
        self.clients["notion_client"].create_report_pages.return_value = []

        process_date(SINCE, UNTIL, session, **self.clients)

        call_args = self.clients["summary_client"].generate_summary.call_args[0]
        github_md = call_args[1]
        # Both commits present, no duplicates
        assert "Existing commit" in github_md
        assert "Squash-lost commit" in github_md
        assert github_md.count("Existing commit") == 1

        # Verify GitHubActivity passed to Notion has exactly 2 commits (no duplication)
        notion_args = self.clients["notion_client"].create_report_pages.call_args[0]
        activity = notion_args[4]
        assert len(activity.repos()["my-repo"]["commits"]) == 2

    def test_detects_skipped_repos(self):
        session, github = _nonempty_activity("repo")
        self.clients["github_client"].fetch_activity.return_value = github

        report = _make_report([{
            "name": "unknown-repo", "summary": [], "tags": [],
        }])
        self.clients["summary_client"].generate_summary.return_value = report
        self.clients["notion_client"].create_report_pages.return_value = []

        process_date(SINCE, UNTIL, session, **self.clients)

        call_args = self.clients["slack_client"].notify.call_args
        skipped = call_args[0][3] if len(call_args[0]) > 3 else call_args[1].get("skipped_repos", [])
        assert "unknown-repo" in skipped

    def test_passes_session_refs_to_github_client_when_backfill(self):
        session = SessionActivity({"repo-a": [
            {
                "session_id": "s1", "project": "repo-a",
                "start_time": "2026-03-28T10:00:00+09:00",
                "end_time": "2026-03-28T11:00:00+09:00",
                "user_messages": ["msg"], "tools_used": [],
                "session_pulls": [10, 20],
                "session_issues": [30],
            },
            {
                "session_id": "s2", "project": "repo-a",
                "start_time": "2026-03-28T12:00:00+09:00",
                "end_time": "2026-03-28T13:00:00+09:00",
                "user_messages": ["msg"], "tools_used": [],
                "session_pulls": [20, 21],
                "session_issues": [],
            },
        ]})
        self.clients["github_client"].fetch_activity.return_value = GitHubActivity({})

        process_date(SINCE, UNTIL, session, **self.clients, is_backfill=True)

        kwargs = self.clients["github_client"].fetch_activity.call_args.kwargs
        assert kwargs["is_backfill"] is True
        assert kwargs["session_pulls"] == {"repo-a": [10, 20, 21]}
        assert kwargs["session_issues"] == {"repo-a": [30]}

    def test_omits_session_refs_when_not_backfill(self):
        session = SessionActivity({"repo-a": [{
            "session_id": "s1", "project": "repo-a",
            "start_time": "2026-03-28T10:00:00+09:00",
            "end_time": "2026-03-28T11:00:00+09:00",
            "user_messages": ["msg"], "tools_used": [],
            "session_pulls": [10],
            "session_issues": [20],
        }]})
        self.clients["github_client"].fetch_activity.return_value = GitHubActivity({})

        process_date(SINCE, UNTIL, session, **self.clients)

        kwargs = self.clients["github_client"].fetch_activity.call_args.kwargs
        assert kwargs["session_pulls"] == {}
        assert kwargs["session_issues"] == {}


class TestSessionCommitPullNumbersPopulation:
    def setup_method(self):
        self.clients = _make_clients()
        self.report = _make_report([
            {"name": "my-repo", "summary": [], "tags": []},
        ])
        self.clients["summary_client"].generate_summary.return_value = self.report
        self.clients["notion_client"].create_report_pages.return_value = []

    def _session(self, session_commits):
        return SessionActivity({"my-repo": [{
            "session_id": "s1", "project": "my-repo",
            "start_time": "2026-03-28T10:00:00+09:00",
            "end_time": "2026-03-28T11:00:00+09:00",
            "user_messages": ["Work"], "tools_used": ["Bash"],
            "session_commits": session_commits,
        }]})

    def test_populates_for_new_session_commits_only(self):
        session = self._session([
            {"sha": "abc1234", "message": "Existing"},
            {"sha": "def5678", "message": "Squash-lost"},
        ])
        github = GitHubActivity({"my-repo": {
            "commits": [{
                "sha": "abc1234abcdef1234abcdef1234abcdef12345678",
                "message": "Existing", "author": "user",
                "date": "2026-03-28T10:00:00",
                "url": f"https://github.com/{OWNER}/my-repo/commit/abc1234",
                "pull_numbers": [],
            }],
            "pulls": [], "issues": [],
        }})
        self.clients["github_client"].fetch_activity.return_value = github

        process_date(SINCE, UNTIL, session, **self.clients)

        populate = self.clients["github_client"].populate_commit_pull_numbers
        populate.assert_called_once()
        args = populate.call_args.args
        assert args[0] == "my-repo"
        assert [c["sha"] for c in args[1]] == ["def5678"]

    def test_populates_all_session_commits_when_repo_missing(self):
        session = self._session([
            {"sha": "aaa1111", "message": "C1"},
            {"sha": "bbb2222", "message": "C2"},
        ])
        self.clients["github_client"].fetch_activity.return_value = GitHubActivity({})

        process_date(SINCE, UNTIL, session, **self.clients)

        populate = self.clients["github_client"].populate_commit_pull_numbers
        populate.assert_called_once()
        args = populate.call_args.args
        assert args[0] == "my-repo"
        assert [c["sha"] for c in args[1]] == ["aaa1111", "bbb2222"]

    def test_skips_populate_when_all_session_commits_overlap_with_search(self):
        session = self._session([{"sha": "abc1234", "message": "Existing"}])
        github = GitHubActivity({"my-repo": {
            "commits": [{
                "sha": "abc1234abcdef1234abcdef1234abcdef12345678",
                "message": "Existing", "author": "user",
                "date": "2026-03-28T10:00:00",
                "url": f"https://github.com/{OWNER}/my-repo/commit/abc1234",
                "pull_numbers": [],
            }],
            "pulls": [], "issues": [],
        }})
        self.clients["github_client"].fetch_activity.return_value = github

        process_date(SINCE, UNTIL, session, **self.clients)

        self.clients["github_client"].populate_commit_pull_numbers.assert_not_called()


class TestRun:
    @patch("report.pipeline.get_target_date_range")
    @patch("report.pipeline.require_env")
    @patch("report.pipeline.SlackClient")
    @patch("report.pipeline.SessionStore")
    @patch("report.pipeline.SessionClient")
    @patch("report.pipeline.GitHubClient")
    @patch("report.pipeline.NotionClient")
    @patch("report.pipeline.SummaryClient")
    def test_processes_primary_date(
        self, MockSummary, MockNotion, MockGitHub, MockSession,
        MockStore, MockSlack, mock_require_env, mock_date_range,
    ):
        mock_date_range.return_value = (SINCE, UNTIL)
        mock_require_env.side_effect = lambda k: f"fake-{k}"

        store = MockStore.return_value
        store.ingest.return_value = []
        store.scan_backfill_dates.return_value = []
        store.fetch_sessions.return_value = SessionActivity({})

        session_client = MockSession.return_value
        session_client.delete_sessions.return_value = 0

        github_client = MockGitHub.return_value
        github_client.fetch_activity.return_value = GitHubActivity({})

        run(source=None)

        store.scan_backfill_dates.assert_called_once()
        store.fetch_sessions.assert_called_once_with("2026-03-28")
        slack_client = MockSlack.return_value
        slack_client.notify_metrics.assert_called_once()
        args = slack_client.notify_metrics.call_args
        elapsed, peak_mb, _version = args.args
        assert elapsed >= 0
        assert peak_mb >= 0
        assert args.kwargs.get("memory_limit_mb") is None
        slack_client.flush.assert_called_once()

    @patch("report.pipeline.get_target_date_range")
    @patch("report.pipeline.require_env")
    @patch("report.pipeline.SlackClient")
    @patch("report.pipeline.SessionStore")
    @patch("report.pipeline.SessionClient")
    @patch("report.pipeline.GitHubClient")
    @patch("report.pipeline.NotionClient")
    @patch("report.pipeline.SummaryClient")
    def test_passes_memory_limit_to_metrics(
        self, MockSummary, MockNotion, MockGitHub, MockSession,
        MockStore, MockSlack, mock_require_env, mock_date_range,
    ):
        mock_date_range.return_value = (SINCE, UNTIL)
        mock_require_env.side_effect = lambda k: f"fake-{k}"

        store = MockStore.return_value
        store.ingest.return_value = []
        store.scan_backfill_dates.return_value = []
        store.fetch_sessions.return_value = SessionActivity({})

        session_client = MockSession.return_value
        session_client.delete_sessions.return_value = 0

        github_client = MockGitHub.return_value
        github_client.fetch_activity.return_value = GitHubActivity({})

        slack_client = MockSlack.return_value

        run(source=None, memory_limit_mb=512)

        slack_client.notify_metrics.assert_called_once()
        assert slack_client.notify_metrics.call_args.kwargs["memory_limit_mb"] == 512
        slack_client.flush.assert_called_once()

    @patch("report.pipeline.get_version")
    @patch("report.pipeline.get_target_date_range")
    @patch("report.pipeline.require_env")
    @patch("report.pipeline.SlackClient")
    @patch("report.pipeline.SessionStore")
    @patch("report.pipeline.SessionClient")
    @patch("report.pipeline.GitHubClient")
    @patch("report.pipeline.NotionClient")
    @patch("report.pipeline.SummaryClient")
    def test_passes_version_and_timeout_to_metrics(
        self, MockSummary, MockNotion, MockGitHub, MockSession,
        MockStore, MockSlack, mock_require_env, mock_date_range,
        mock_get_version,
    ):
        mock_date_range.return_value = (SINCE, UNTIL)
        mock_require_env.side_effect = lambda k: f"fake-{k}"
        mock_get_version.return_value = "0.2.1"

        store = MockStore.return_value
        store.ingest.return_value = []
        store.scan_backfill_dates.return_value = []
        store.fetch_sessions.return_value = SessionActivity({})

        session_client = MockSession.return_value
        session_client.delete_sessions.return_value = 0

        github_client = MockGitHub.return_value
        github_client.fetch_activity.return_value = GitHubActivity({})

        slack_client = MockSlack.return_value

        run(source=None, memory_limit_mb=512, timeout_seconds=300)

        slack_client.notify_metrics.assert_called_once()
        call = slack_client.notify_metrics.call_args
        # version is the third positional argument
        assert call.args[2] == "0.2.1"
        assert call.kwargs["timeout_seconds"] == 300
        slack_client.flush.assert_called_once()

    @patch("report.pipeline.get_target_date_range")
    @patch("report.pipeline.require_env")
    @patch("report.pipeline.SlackClient")
    @patch("report.pipeline.SessionStore")
    @patch("report.pipeline.SessionClient")
    @patch("report.pipeline.GitHubClient")
    @patch("report.pipeline.NotionClient")
    @patch("report.pipeline.SummaryClient")
    def test_backfills_past_dates(
        self, MockSummary, MockNotion, MockGitHub, MockSession,
        MockStore, MockSlack, mock_require_env, mock_date_range,
    ):
        mock_date_range.return_value = (SINCE, UNTIL)
        mock_require_env.side_effect = lambda k: f"fake-{k}"

        store = MockStore.return_value
        store.ingest.return_value = []
        store.scan_backfill_dates.return_value = [
            date(2026, 3, 26), date(2026, 3, 27),
        ]
        store.fetch_sessions.return_value = SessionActivity({})

        session_client = MockSession.return_value
        session_client.delete_sessions.return_value = 0

        github_client = MockGitHub.return_value
        github_client.fetch_activity.return_value = GitHubActivity({})

        run(source=None)

        # 2 backfill dates + 1 primary = 3 calls
        assert store.fetch_sessions.call_count == 3

    @patch("report.pipeline.get_target_date_range")
    @patch("report.pipeline.require_env")
    @patch("report.pipeline.SlackClient")
    @patch("report.pipeline.SessionStore")
    @patch("report.pipeline.SessionClient")
    @patch("report.pipeline.GitHubClient")
    @patch("report.pipeline.NotionClient")
    @patch("report.pipeline.SummaryClient")
    def test_backfill_limited_to_max(
        self, MockSummary, MockNotion, MockGitHub, MockSession,
        MockStore, MockSlack, mock_require_env, mock_date_range,
    ):
        mock_date_range.return_value = (SINCE, UNTIL)
        mock_require_env.side_effect = lambda k: f"fake-{k}"

        store = MockStore.return_value
        store.ingest.return_value = []
        # 5 past dates, but only MAX_BACKFILL most recent should be processed
        store.scan_backfill_dates.return_value = [
            date(2026, 3, 23), date(2026, 3, 24), date(2026, 3, 25),
            date(2026, 3, 26), date(2026, 3, 27),
        ]
        store.fetch_sessions.return_value = SessionActivity({})

        session_client = MockSession.return_value
        session_client.delete_sessions.return_value = 0

        github_client = MockGitHub.return_value
        github_client.fetch_activity.return_value = GitHubActivity({})

        run(source=None)

        # MAX_BACKFILL + 1 primary
        assert store.fetch_sessions.call_count == MAX_BACKFILL + 1

    @patch("report.pipeline.get_target_date_range")
    @patch("report.pipeline.require_env")
    @patch("report.pipeline.SlackClient")
    @patch("report.pipeline.SessionStore")
    @patch("report.pipeline.SessionClient")
    @patch("report.pipeline.GitHubClient")
    @patch("report.pipeline.NotionClient")
    @patch("report.pipeline.SummaryClient")
    def test_notify_on_primary_failure(
        self, MockSummary, MockNotion, MockGitHub, MockSession,
        MockStore, MockSlack, mock_require_env, mock_date_range,
    ):
        mock_date_range.return_value = (SINCE, UNTIL)
        mock_require_env.side_effect = lambda k: f"fake-{k}"

        store = MockStore.return_value
        store.ingest.return_value = []
        store.scan_backfill_dates.return_value = []
        store.fetch_sessions.side_effect = RuntimeError("DynamoDB error")

        session_client = MockSession.return_value
        session_client.delete_sessions.return_value = 0

        github_client = MockGitHub.return_value

        slack_client = MockSlack.return_value

        with pytest.raises(RuntimeError, match="DynamoDB error"):
            run(source=None)

        slack_client.notify_error.assert_called_once()
        # Metrics should still be sent on failure (finally block)
        slack_client.notify_metrics.assert_called_once()
        slack_client.flush.assert_called_once()

    @patch("report.pipeline.get_target_date_range")
    @patch("report.pipeline.require_env")
    @patch("report.pipeline.SlackClient")
    @patch("report.pipeline.SessionStore")
    @patch("report.pipeline.SessionClient")
    @patch("report.pipeline.GitHubClient")
    @patch("report.pipeline.NotionClient")
    @patch("report.pipeline.SummaryClient")
    def test_backfill_failure_does_not_stop_primary(
        self, MockSummary, MockNotion, MockGitHub, MockSession,
        MockStore, MockSlack, mock_require_env, mock_date_range,
    ):
        mock_date_range.return_value = (SINCE, UNTIL)
        mock_require_env.side_effect = lambda k: f"fake-{k}"

        store = MockStore.return_value
        store.ingest.return_value = []
        store.scan_backfill_dates.return_value = [date(2026, 3, 27)]

        call_count = 0

        def fetch_sessions_side_effect(date_str):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("backfill error")
            return SessionActivity({})

        store.fetch_sessions.side_effect = fetch_sessions_side_effect

        session_client = MockSession.return_value
        session_client.delete_sessions.return_value = 0

        github_client = MockGitHub.return_value
        github_client.fetch_activity.return_value = GitHubActivity({})

        slack_client = MockSlack.return_value

        run(source=None)

        # Backfill failure notified but primary still processed
        assert store.fetch_sessions.call_count == 2
        slack_client.notify_error.assert_called_once()
        backfill_since = slack_client.notify_error.call_args[0][0]
        assert backfill_since.date() == date(2026, 3, 27)

    @patch("report.pipeline.get_target_date_range")
    @patch("report.pipeline.require_env")
    @patch("report.pipeline.SlackClient")
    @patch("report.pipeline.SessionStore")
    @patch("report.pipeline.SessionClient")
    @patch("report.pipeline.GitHubClient")
    @patch("report.pipeline.NotionClient")
    @patch("report.pipeline.SummaryClient")
    def test_continues_when_ingestion_fails(
        self, MockSummary, MockNotion, MockGitHub, MockSession,
        MockStore, MockSlack, mock_require_env, mock_date_range,
    ):
        mock_date_range.return_value = (SINCE, UNTIL)
        mock_require_env.side_effect = lambda k: f"fake-{k}"

        store = MockStore.return_value
        store.ingest.side_effect = RuntimeError("DynamoDB error")
        store.scan_backfill_dates.return_value = []
        store.fetch_sessions.return_value = SessionActivity({})

        session_client = MockSession.return_value

        github_client = MockGitHub.return_value
        github_client.fetch_activity.return_value = GitHubActivity({})

        slack_client = MockSlack.return_value

        run(source=None)

        # Ingestion error notified but pipeline continues
        slack_client.notify_error.assert_called_once()
        store.scan_backfill_dates.assert_called_once()

    @patch("report.pipeline.get_target_date_range")
    @patch("report.pipeline.require_env")
    @patch("report.pipeline.SlackClient")
    @patch("report.pipeline.SessionStore")
    @patch("report.pipeline.SessionClient")
    @patch("report.pipeline.GitHubClient")
    @patch("report.pipeline.NotionClient")
    @patch("report.pipeline.SummaryClient")
    def test_marks_reported_after_success(
        self, MockSummary, MockNotion, MockGitHub, MockSession,
        MockStore, MockSlack, mock_require_env, mock_date_range,
    ):
        mock_date_range.return_value = (SINCE, UNTIL)
        mock_require_env.side_effect = lambda k: f"fake-{k}"

        store = MockStore.return_value
        store.ingest.return_value = []
        store.scan_backfill_dates.return_value = []
        store.fetch_sessions.return_value = SessionActivity({})

        session_client = MockSession.return_value
        session_client.delete_sessions.return_value = 0

        github_client = MockGitHub.return_value
        github_client.fetch_activity.return_value = GitHubActivity({})

        run(source=None)

        store.mark_reported.assert_called_once_with("2026-03-28")

    @patch("report.pipeline.get_target_date_range")
    @patch("report.pipeline.require_env")
    @patch("report.pipeline.SlackClient")
    @patch("report.pipeline.SessionStore")
    @patch("report.pipeline.SessionClient")
    @patch("report.pipeline.GitHubClient")
    @patch("report.pipeline.NotionClient")
    @patch("report.pipeline.SummaryClient")
    def test_passes_target_date_to_date_range(
        self, MockSummary, MockNotion, MockGitHub, MockSession,
        MockStore, MockSlack, mock_require_env, mock_date_range,
    ):
        target_since = datetime(2026, 3, 25, 0, 0, tzinfo=JST)
        target_until = datetime(2026, 3, 26, 0, 0, tzinfo=JST)
        mock_date_range.return_value = (target_since, target_until)
        mock_require_env.side_effect = lambda k: f"fake-{k}"

        store = MockStore.return_value
        store.ingest.return_value = []
        store.scan_backfill_dates.return_value = []
        store.fetch_sessions.return_value = SessionActivity({})

        session_client = MockSession.return_value
        session_client.delete_sessions.return_value = 0

        github_client = MockGitHub.return_value
        github_client.fetch_activity.return_value = GitHubActivity({})

        run(source="manual", target_date="2026-03-25")

        mock_date_range.assert_called_once_with("manual", target_date="2026-03-25")

    @patch("report.pipeline.get_target_date_range")
    @patch("report.pipeline.require_env")
    @patch("report.pipeline.SlackClient")
    @patch("report.pipeline.SessionStore")
    @patch("report.pipeline.SessionClient")
    @patch("report.pipeline.GitHubClient")
    @patch("report.pipeline.NotionClient")
    @patch("report.pipeline.SummaryClient")
    def test_deletes_s3_after_ingest(
        self, MockSummary, MockNotion, MockGitHub, MockSession,
        MockStore, MockSlack, mock_require_env, mock_date_range,
    ):
        mock_date_range.return_value = (SINCE, UNTIL)
        mock_require_env.side_effect = lambda k: f"fake-{k}"

        store = MockStore.return_value
        store.ingest.return_value = ["claude-sessions/proj/s1.jsonl"]
        store.scan_backfill_dates.return_value = []
        store.fetch_sessions.return_value = SessionActivity({})

        session_client = MockSession.return_value
        session_client.delete_sessions.return_value = 1

        github_client = MockGitHub.return_value
        github_client.fetch_activity.return_value = GitHubActivity({})

        run(source=None)

        session_client.delete_sessions.assert_called_once_with(
            ["claude-sessions/proj/s1.jsonl"],
        )

    @patch("report.pipeline.process_date")
    @patch("report.pipeline.get_target_date_range")
    @patch("report.pipeline.require_env")
    @patch("report.pipeline.SlackClient")
    @patch("report.pipeline.SessionStore")
    @patch("report.pipeline.SessionClient")
    @patch("report.pipeline.GitHubClient")
    @patch("report.pipeline.NotionClient")
    @patch("report.pipeline.SummaryClient")
    def test_manual_run_uses_original_until(
        self, MockSummary, MockNotion, MockGitHub, MockSession,
        MockStore, MockSlack, mock_require_env, mock_date_range,
        mock_process_date,
    ):
        """Manual run without --date passes get_target_date_range's partial_until (not full-day)."""
        partial_until = datetime(2026, 3, 28, 15, 30, tzinfo=JST)
        mock_date_range.return_value = (SINCE, partial_until)
        mock_require_env.side_effect = lambda k: f"fake-{k}"

        store = MockStore.return_value
        store.ingest.return_value = []
        store.scan_backfill_dates.return_value = []
        store.fetch_sessions.return_value = SessionActivity({})

        session_client = MockSession.return_value
        session_client.delete_sessions.return_value = 0

        run(source="manual")

        mock_process_date.assert_called_once()
        call_args = mock_process_date.call_args
        assert call_args[0][0] == SINCE
        assert call_args[0][1] == partial_until

    @patch("report.pipeline.get_target_date_range")
    @patch("report.pipeline.require_env")
    @patch("report.pipeline.SlackClient")
    @patch("report.pipeline.SessionStore")
    @patch("report.pipeline.SessionClient")
    @patch("report.pipeline.GitHubClient")
    @patch("report.pipeline.NotionClient")
    @patch("report.pipeline.SummaryClient")
    def test_target_date_skips_backfill(
        self, MockSummary, MockNotion, MockGitHub, MockSession,
        MockStore, MockSlack, mock_require_env, mock_date_range,
    ):
        target_since = datetime(2026, 3, 25, 0, 0, tzinfo=JST)
        target_until = datetime(2026, 3, 26, 0, 0, tzinfo=JST)
        mock_date_range.return_value = (target_since, target_until)
        mock_require_env.side_effect = lambda k: f"fake-{k}"

        store = MockStore.return_value
        store.ingest.return_value = []
        store.fetch_sessions.return_value = SessionActivity({})

        session_client = MockSession.return_value
        session_client.delete_sessions.return_value = 0

        github_client = MockGitHub.return_value
        github_client.fetch_activity.return_value = GitHubActivity({})

        run(source="manual", target_date="2026-03-25")

        store.scan_backfill_dates.assert_not_called()
        store.fetch_sessions.assert_called_once_with("2026-03-25")

    @patch("report.pipeline.get_target_date_range")
    @patch("report.pipeline.require_env")
    @patch("report.pipeline.SlackClient")
    @patch("report.pipeline.SessionStore")
    @patch("report.pipeline.SessionClient")
    @patch("report.pipeline.GitHubClient")
    @patch("report.pipeline.NotionClient")
    @patch("report.pipeline.SummaryClient")
    def test_date_range_processes_all_dates(
        self, MockSummary, MockNotion, MockGitHub, MockSession,
        MockStore, MockSlack, mock_require_env, mock_date_range,
    ):
        target_since = datetime(2026, 3, 25, 0, 0, tzinfo=JST)
        target_until = datetime(2026, 3, 26, 0, 0, tzinfo=JST)
        mock_date_range.return_value = (target_since, target_until)
        mock_require_env.side_effect = lambda k: f"fake-{k}"

        store = MockStore.return_value
        store.ingest.return_value = []
        store.fetch_sessions.return_value = SessionActivity({})

        session_client = MockSession.return_value
        session_client.delete_sessions.return_value = 0

        github_client = MockGitHub.return_value
        github_client.fetch_activity.return_value = GitHubActivity({})

        run(source="manual", target_date="2026-03-25..2026-03-28")

        store.scan_backfill_dates.assert_not_called()
        assert store.fetch_sessions.call_count == 4
        store.fetch_sessions.assert_any_call("2026-03-25")
        store.fetch_sessions.assert_any_call("2026-03-26")
        store.fetch_sessions.assert_any_call("2026-03-27")
        store.fetch_sessions.assert_any_call("2026-03-28")

    @patch("report.pipeline.get_target_date_range")
    @patch("report.pipeline.require_env")
    @patch("report.pipeline.SlackClient")
    @patch("report.pipeline.SessionStore")
    @patch("report.pipeline.SessionClient")
    @patch("report.pipeline.GitHubClient")
    @patch("report.pipeline.NotionClient")
    @patch("report.pipeline.SummaryClient")
    def test_target_date_uses_backfill_fetch(
        self, MockSummary, MockNotion, MockGitHub, MockSession,
        MockStore, MockSlack, mock_require_env, mock_date_range,
    ):
        """target_date 指定時は Hybrid 経路（is_backfill=True）になる"""
        target_since = datetime(2026, 3, 25, 0, 0, tzinfo=JST)
        target_until = datetime(2026, 3, 26, 0, 0, tzinfo=JST)
        mock_date_range.return_value = (target_since, target_until)
        mock_require_env.side_effect = lambda k: f"fake-{k}"

        store = MockStore.return_value
        store.ingest.return_value = []
        store.fetch_sessions.return_value = SessionActivity({})

        session_client = MockSession.return_value
        session_client.delete_sessions.return_value = 0

        github_client = MockGitHub.return_value
        github_client.fetch_activity.return_value = GitHubActivity({})

        run(source="manual", target_date="2026-03-25")

        for call in github_client.fetch_activity.call_args_list:
            assert call.kwargs.get("is_backfill") is True

    @patch("report.pipeline.get_target_date_range")
    @patch("report.pipeline.require_env")
    @patch("report.pipeline.SlackClient")
    @patch("report.pipeline.SessionStore")
    @patch("report.pipeline.SessionClient")
    @patch("report.pipeline.GitHubClient")
    @patch("report.pipeline.NotionClient")
    @patch("report.pipeline.SummaryClient")
    def test_scan_backfill_dates_use_hybrid_but_primary_does_not(
        self, MockSummary, MockNotion, MockGitHub, MockSession,
        MockStore, MockSlack, mock_require_env, mock_date_range,
    ):
        """scan_backfill_dates 由来の日付のみ is_backfill=True、primary は False"""
        mock_date_range.return_value = (SINCE, UNTIL)
        mock_require_env.side_effect = lambda k: f"fake-{k}"

        store = MockStore.return_value
        store.ingest.return_value = []
        store.scan_backfill_dates.return_value = [
            date(2026, 3, 26), date(2026, 3, 27),
        ]
        store.fetch_sessions.return_value = SessionActivity({})

        session_client = MockSession.return_value
        session_client.delete_sessions.return_value = 0

        github_client = MockGitHub.return_value
        github_client.fetch_activity.return_value = GitHubActivity({})

        run(source=None)

        flags = [
            (call.args[0], call.kwargs.get("is_backfill"))
            for call in github_client.fetch_activity.call_args_list
        ]
        # 2 backfill + 1 primary
        backfill_flags = [f for s, f in flags if s != SINCE]
        primary_flags = [f for s, f in flags if s == SINCE]
        assert backfill_flags == [True, True]
        assert primary_flags == [False]
