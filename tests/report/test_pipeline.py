"""Tests for report generation pipeline."""

from contextlib import ExitStack
from datetime import date, datetime
from unittest.mock import patch

import pytest

from report import JST, SessionActivity
from report.github import GitHubActivity
from report.pipeline import MAX_BACKFILL, process_date, run
from report.summarizer import ValidationResult

from ._builders import (
    OWNER,
    assert_published,
    assert_skipped,
    make_commit,
    make_github,
    make_session,
    make_session_entry,
)

# Default JST day window used across most tests
SINCE = datetime(2026, 3, 28, 0, 0, tzinfo=JST)
UNTIL = datetime(2026, 3, 29, 0, 0, tzinfo=JST)


def _make_report(repos=None):
    """Create a minimal report dict."""
    return {
        "repositories": repos or [],
    }


class TestProcessDate:
    def test_skips_when_no_activity(self, pipeline_clients):
        pipeline_clients["github_client"].fetch_activity.return_value = GitHubActivity(
            {}
        )

        process_date(SINCE, UNTIL, SessionActivity({}), **pipeline_clients)

        assert_skipped(pipeline_clients, SINCE)

    def test_generates_report_and_publishes(self, pipeline_clients):
        session = make_session("my-repo")
        pipeline_clients["github_client"].fetch_activity.return_value = make_github(
            "my-repo", commits=[make_commit(sha="abc")]
        )

        report = _make_report(
            [
                {
                    "name": "my-repo",
                    "summary": ["work"],
                    "tags": [],
                }
            ]
        )
        pipeline_clients["summary_client"].generate_summary.return_value = report
        pipeline_clients["notion_client"].create_report_pages.return_value = [
            ("my-repo", "https://notion.so/page1"),
        ]

        process_date(SINCE, UNTIL, session, **pipeline_clients)

        assert_published(pipeline_clients)

        # Verify the (target_date, SINCE, UNTIL) trio is passed in order
        notion_args = pipeline_clients["notion_client"].create_report_pages.call_args[0]
        assert notion_args[0] == SINCE
        assert notion_args[1] == SINCE
        assert notion_args[2] == UNTIL

    def test_notifies_validation_errors(self, pipeline_clients):
        session = make_session("repo")
        pipeline_clients["github_client"].fetch_activity.return_value = make_github(
            "repo", commits=[make_commit(sha="abc", repo="repo")]
        )

        report = _make_report(
            [
                {
                    "name": "repo",
                    "summary": [],
                    "tags": ["BadTag"],
                }
            ]
        )
        pipeline_clients["summary_client"].generate_summary.return_value = report
        invalid = ValidationResult()
        invalid.invalid_tags = {"repo": ["BadTag"]}
        pipeline_clients["summary_client"].validate_report.return_value = invalid
        pipeline_clients["notion_client"].create_report_pages.return_value = []

        process_date(SINCE, UNTIL, session, **pipeline_clients)

        pipeline_clients["slack_client"].notify_validation_errors.assert_called_once()

    def test_supplements_session_commits_when_github_has_none(self, pipeline_clients):
        session = make_session(
            "my-repo",
            tools=("Bash",),
            session_commits=[
                {"sha": "a1b2c3d", "message": "Fix login bug"},
            ],
        )
        # GitHub API has the repo but no commits
        pipeline_clients["github_client"].fetch_activity.return_value = make_github(
            "my-repo"
        )

        report = _make_report(
            [
                {
                    "name": "my-repo",
                    "summary": ["work"],
                    "tags": [],
                }
            ]
        )
        pipeline_clients["summary_client"].generate_summary.return_value = report
        pipeline_clients["notion_client"].create_report_pages.return_value = []

        process_date(SINCE, UNTIL, session, **pipeline_clients)

        # Session commits should be injected into github_activity
        call_args = pipeline_clients["summary_client"].generate_summary.call_args[0]
        github_md = call_args[1]
        assert "Fix login bug" in github_md

        # Verify GitHubActivity passed to Notion also contains the commit
        # with normalized fields (date from session, constructed url, empty author)
        notion_args = pipeline_clients["notion_client"].create_report_pages.call_args[0]
        activity = notion_args[4]
        assert "my-repo" in activity.repos()
        injected = next(
            c for c in activity.repos()["my-repo"]["commits"] if c.sha == "a1b2c3d"
        )
        assert injected.date == "2026-03-28T10:00:00+09:00"
        assert injected.url == f"https://github.com/{OWNER}/my-repo/commit/a1b2c3d"
        assert injected.author == ""

    def test_session_commit_uses_per_commit_timestamp_when_present(
        self, pipeline_clients
    ):
        session = make_session(
            "my-repo",
            end="2026-03-28T12:00:00+09:00",
            messages=("Work",),
            tools=("Bash",),
            session_commits=[
                {
                    "sha": "aaa1111",
                    "message": "Mid commit",
                    "timestamp": "2026-03-28T10:45:00+09:00",
                },
                {
                    "sha": "bbb2222",
                    "message": "Late commit",
                },  # legacy: no timestamp
            ],
        )
        pipeline_clients["github_client"].fetch_activity.return_value = make_github(
            "my-repo"
        )

        report = _make_report(
            [
                {
                    "name": "my-repo",
                    "summary": ["work"],
                    "tags": [],
                }
            ]
        )
        pipeline_clients["summary_client"].generate_summary.return_value = report
        pipeline_clients["notion_client"].create_report_pages.return_value = []

        process_date(SINCE, UNTIL, session, **pipeline_clients)

        notion_args = pipeline_clients["notion_client"].create_report_pages.call_args[0]
        commits = notion_args[4].repos()["my-repo"]["commits"]
        # Per-commit timestamp wins; legacy entry falls back to session start_time
        first = next(c for c in commits if c.sha == "aaa1111")
        second = next(c for c in commits if c.sha == "bbb2222")
        assert first.date == "2026-03-28T10:45:00+09:00"
        assert second.date == "2026-03-28T10:00:00+09:00"

    def test_dedupes_session_commits_across_sessions(self, pipeline_clients):
        session = make_session(
            "my-repo",
            entries=[
                make_session_entry(
                    session_id="s1",
                    project="my-repo",
                    messages=("Work",),
                    tools=("Bash",),
                    session_commits=[
                        {
                            "sha": "aaa1111",
                            "message": "Shared commit",
                            "timestamp": "2026-03-28T10:30:00+09:00",
                        },
                    ],
                ),
                make_session_entry(
                    session_id="s2",
                    project="my-repo",
                    start="2026-03-28T14:00:00+09:00",
                    end="2026-03-28T15:00:00+09:00",
                    messages=("More work",),
                    tools=("Bash",),
                    session_commits=[
                        {
                            "sha": "aaa1111",
                            "message": "Shared commit",
                            "timestamp": "2026-03-28T14:30:00+09:00",
                        },
                    ],
                ),
            ],
        )
        pipeline_clients["github_client"].fetch_activity.return_value = make_github(
            "my-repo"
        )

        report = _make_report(
            [
                {
                    "name": "my-repo",
                    "summary": ["work"],
                    "tags": [],
                }
            ]
        )
        pipeline_clients["summary_client"].generate_summary.return_value = report
        pipeline_clients["notion_client"].create_report_pages.return_value = []

        process_date(SINCE, UNTIL, session, **pipeline_clients)

        notion_args = pipeline_clients["notion_client"].create_report_pages.call_args[0]
        commits = notion_args[4].repos()["my-repo"]["commits"]
        # Same SHA appearing in two sessions should appear only once,
        # with the earliest occurrence (s1) winning
        assert len(commits) == 1
        assert commits[0].sha == "aaa1111"
        assert commits[0].date == "2026-03-28T10:30:00+09:00"

    def test_supplements_session_commits_for_missing_repo(self, pipeline_clients):
        session = make_session(
            "my-repo",
            tools=("Bash",),
            session_commits=[
                {"sha": "a1b2c3d", "message": "Fix login bug"},
            ],
        )
        # GitHub API returned no data for this repo at all
        pipeline_clients["github_client"].fetch_activity.return_value = GitHubActivity(
            {}
        )

        report = _make_report(
            [
                {
                    "name": "my-repo",
                    "summary": ["work"],
                    "tags": [],
                }
            ]
        )
        pipeline_clients["summary_client"].generate_summary.return_value = report
        pipeline_clients["notion_client"].create_report_pages.return_value = []

        process_date(SINCE, UNTIL, session, **pipeline_clients)

        call_args = pipeline_clients["summary_client"].generate_summary.call_args[0]
        github_md = call_args[1]
        assert "Fix login bug" in github_md

        # Verify GitHubActivity passed to Notion contains the injected repo
        # with the same URL construction as when the repo was already present
        notion_args = pipeline_clients["notion_client"].create_report_pages.call_args[0]
        activity = notion_args[4]
        assert "my-repo" in activity.repos()
        commits = activity.repos()["my-repo"]["commits"]
        assert len(commits) == 1
        assert commits[0].url == f"https://github.com/{OWNER}/my-repo/commit/a1b2c3d"

    def test_merges_and_deduplicates_session_commits(self, pipeline_clients):
        session = make_session(
            "my-repo",
            messages=("Work",),
            tools=("Bash",),
            session_commits=[
                {"sha": "abc1234", "message": "Existing commit"},
                {"sha": "def5678", "message": "Squash-lost commit"},
            ],
        )
        # GitHub API found one commit with full SHA that overlaps with session
        pipeline_clients["github_client"].fetch_activity.return_value = make_github(
            "my-repo",
            commits=[
                make_commit(
                    sha="abc1234abcdef1234abcdef1234abcdef12345678",
                    message="Existing commit",
                    url=f"https://github.com/{OWNER}/my-repo/commit/abc1234",
                )
            ],
        )

        report = _make_report(
            [
                {
                    "name": "my-repo",
                    "summary": ["work"],
                    "tags": [],
                }
            ]
        )
        pipeline_clients["summary_client"].generate_summary.return_value = report
        pipeline_clients["notion_client"].create_report_pages.return_value = []

        process_date(SINCE, UNTIL, session, **pipeline_clients)

        call_args = pipeline_clients["summary_client"].generate_summary.call_args[0]
        github_md = call_args[1]
        # Both commits present, no duplicates
        assert "Existing commit" in github_md
        assert "Squash-lost commit" in github_md
        assert github_md.count("Existing commit") == 1

        # Verify GitHubActivity passed to Notion has exactly 2 commits (no duplication)
        notion_args = pipeline_clients["notion_client"].create_report_pages.call_args[0]
        activity = notion_args[4]
        assert len(activity.repos()["my-repo"]["commits"]) == 2

    def test_detects_skipped_repos(self, pipeline_clients):
        session = make_session("repo")
        pipeline_clients["github_client"].fetch_activity.return_value = make_github(
            "repo", commits=[make_commit(sha="abc", repo="repo")]
        )

        report = _make_report(
            [
                {
                    "name": "unknown-repo",
                    "summary": [],
                    "tags": [],
                }
            ]
        )
        pipeline_clients["summary_client"].generate_summary.return_value = report
        pipeline_clients["notion_client"].create_report_pages.return_value = []

        process_date(SINCE, UNTIL, session, **pipeline_clients)

        call_args = pipeline_clients["slack_client"].notify.call_args
        skipped = (
            call_args[0][3]
            if len(call_args[0]) > 3
            else call_args[1].get("skipped_repos", [])
        )
        assert "unknown-repo" in skipped

    def test_passes_session_refs_to_github_client_when_backfill(self, pipeline_clients):
        session = make_session(
            "repo-a",
            entries=[
                make_session_entry(
                    session_id="s1",
                    project="repo-a",
                    messages=("msg",),
                    tools=(),
                    session_pulls=[10, 20],
                    session_issues=[30],
                ),
                make_session_entry(
                    session_id="s2",
                    project="repo-a",
                    start="2026-03-28T12:00:00+09:00",
                    end="2026-03-28T13:00:00+09:00",
                    messages=("msg",),
                    tools=(),
                    session_pulls=[20, 21],
                    session_issues=[],
                ),
            ],
        )
        pipeline_clients["github_client"].fetch_activity.return_value = GitHubActivity(
            {}
        )

        process_date(SINCE, UNTIL, session, **pipeline_clients, is_backfill=True)

        kwargs = pipeline_clients["github_client"].fetch_activity.call_args.kwargs
        assert kwargs["is_backfill"] is True
        assert kwargs["session_pulls"] == {"repo-a": [10, 20, 21]}
        assert kwargs["session_issues"] == {"repo-a": [30]}

    def test_omits_session_refs_when_not_backfill(self, pipeline_clients):
        session = make_session(
            "repo-a",
            messages=("msg",),
            tools=(),
            session_pulls=[10],
            session_issues=[20],
        )
        pipeline_clients["github_client"].fetch_activity.return_value = GitHubActivity(
            {}
        )

        process_date(SINCE, UNTIL, session, **pipeline_clients)

        kwargs = pipeline_clients["github_client"].fetch_activity.call_args.kwargs
        assert kwargs["session_pulls"] == {}
        assert kwargs["session_issues"] == {}


class TestSessionCommitPullNumbersPopulation:
    @pytest.fixture(autouse=True)
    def _setup(self, pipeline_clients):
        self.clients = pipeline_clients
        self.clients["summary_client"].generate_summary.return_value = _make_report(
            [{"name": "my-repo", "summary": [], "tags": []}],
        )
        self.clients["notion_client"].create_report_pages.return_value = []

    def _session(self, session_commits):
        return make_session(
            "my-repo",
            messages=("Work",),
            tools=("Bash",),
            session_commits=session_commits,
        )

    def test_populates_for_new_session_commits_only(self):
        session = self._session(
            [
                {"sha": "abc1234", "message": "Existing"},
                {"sha": "def5678", "message": "Squash-lost"},
            ]
        )
        self.clients["github_client"].fetch_activity.return_value = make_github(
            "my-repo",
            commits=[
                make_commit(
                    sha="abc1234abcdef1234abcdef1234abcdef12345678",
                    message="Existing",
                    url=f"https://github.com/{OWNER}/my-repo/commit/abc1234",
                )
            ],
        )

        process_date(SINCE, UNTIL, session, **self.clients)

        populate = self.clients["github_client"].populate_commit_pull_numbers
        populate.assert_called_once()
        args = populate.call_args.args
        assert args[0] == "my-repo"
        assert [c.sha for c in args[1]] == ["def5678"]

    def test_populates_all_session_commits_when_repo_missing(self):
        session = self._session(
            [
                {"sha": "aaa1111", "message": "C1"},
                {"sha": "bbb2222", "message": "C2"},
            ]
        )
        self.clients["github_client"].fetch_activity.return_value = GitHubActivity({})

        process_date(SINCE, UNTIL, session, **self.clients)

        populate = self.clients["github_client"].populate_commit_pull_numbers
        populate.assert_called_once()
        args = populate.call_args.args
        assert args[0] == "my-repo"
        assert [c.sha for c in args[1]] == ["aaa1111", "bbb2222"]

    def test_skips_populate_when_all_session_commits_overlap_with_search(self):
        session = self._session([{"sha": "abc1234", "message": "Existing"}])
        self.clients["github_client"].fetch_activity.return_value = make_github(
            "my-repo",
            commits=[
                make_commit(
                    sha="abc1234abcdef1234abcdef1234abcdef12345678",
                    message="Existing",
                    url=f"https://github.com/{OWNER}/my-repo/commit/abc1234",
                )
            ],
        )

        process_date(SINCE, UNTIL, session, **self.clients)

        self.clients["github_client"].populate_commit_pull_numbers.assert_not_called()


class TestRun:
    @pytest.fixture(autouse=True)
    def run_patches(self):
        """Patched collaborators of run() with overridable defaults each test can adjust."""
        targets = {
            "SummaryClient": "report.pipeline.SummaryClient",
            "NotionClient": "report.pipeline.NotionClient",
            "GitHubClient": "report.pipeline.GitHubClient",
            "SessionClient": "report.pipeline.SessionClient",
            "SessionStore": "report.pipeline.SessionStore",
            "SlackClient": "report.pipeline.SlackClient",
            "require_env": "report.pipeline.require_env",
            "get_target_date_range": "report.pipeline.get_target_date_range",
        }
        with ExitStack() as stack:
            mocks = {
                name: stack.enter_context(patch(target))
                for name, target in targets.items()
            }
            mocks["require_env"].side_effect = lambda k: f"fake-{k}"
            mocks["get_target_date_range"].return_value = (SINCE, UNTIL)
            store = mocks["SessionStore"].return_value
            store.ingest.return_value = []
            store.scan_backfill_dates.return_value = []
            store.fetch_sessions.return_value = SessionActivity({})
            mocks["SessionClient"].return_value.delete_sessions.return_value = 0
            mocks[
                "GitHubClient"
            ].return_value.fetch_activity.return_value = GitHubActivity({})
            yield mocks

    def test_processes_primary_date(self, run_patches):
        run(source=None)

        store = run_patches["SessionStore"].return_value
        store.scan_backfill_dates.assert_called_once()
        store.fetch_sessions.assert_called_once_with("2026-03-28")
        slack_client = run_patches["SlackClient"].return_value
        slack_client.notify_metrics.assert_called_once()
        args = slack_client.notify_metrics.call_args
        elapsed, peak_mb, _version = args.args
        assert elapsed >= 0
        assert peak_mb >= 0
        assert args.kwargs.get("memory_limit_mb") is None
        slack_client.flush.assert_called_once()

    def test_passes_memory_limit_to_metrics(self, run_patches):
        slack_client = run_patches["SlackClient"].return_value

        run(source=None, memory_limit_mb=512)

        slack_client.notify_metrics.assert_called_once()
        assert slack_client.notify_metrics.call_args.kwargs["memory_limit_mb"] == 512
        slack_client.flush.assert_called_once()

    @patch("report.pipeline.get_version")
    def test_passes_version_and_timeout_to_metrics(self, mock_get_version, run_patches):
        mock_get_version.return_value = "0.2.1"
        slack_client = run_patches["SlackClient"].return_value

        run(source=None, memory_limit_mb=512, timeout_seconds=300)

        slack_client.notify_metrics.assert_called_once()
        call = slack_client.notify_metrics.call_args
        # version is the third positional argument
        assert call.args[2] == "0.2.1"
        assert call.kwargs["timeout_seconds"] == 300
        slack_client.flush.assert_called_once()

    def test_backfills_past_dates(self, run_patches):
        store = run_patches["SessionStore"].return_value
        store.scan_backfill_dates.return_value = [
            date(2026, 3, 26),
            date(2026, 3, 27),
        ]

        run(source=None)

        # 2 backfill dates + 1 primary = 3 calls
        assert store.fetch_sessions.call_count == 3

    def test_backfill_limited_to_max(self, run_patches):
        store = run_patches["SessionStore"].return_value
        # 5 past dates, but only MAX_BACKFILL most recent should be processed
        store.scan_backfill_dates.return_value = [
            date(2026, 3, 23),
            date(2026, 3, 24),
            date(2026, 3, 25),
            date(2026, 3, 26),
            date(2026, 3, 27),
        ]

        run(source=None)

        # MAX_BACKFILL + 1 primary
        assert store.fetch_sessions.call_count == MAX_BACKFILL + 1

    def test_notify_on_primary_failure(self, run_patches):
        store = run_patches["SessionStore"].return_value
        store.fetch_sessions.side_effect = RuntimeError("DynamoDB error")
        slack_client = run_patches["SlackClient"].return_value

        with pytest.raises(RuntimeError, match="DynamoDB error"):
            run(source=None)

        slack_client.notify_error.assert_called_once()
        # Metrics should still be sent on failure (finally block)
        slack_client.notify_metrics.assert_called_once()
        slack_client.flush.assert_called_once()

    def test_backfill_failure_does_not_stop_primary(self, run_patches):
        store = run_patches["SessionStore"].return_value
        store.scan_backfill_dates.return_value = [date(2026, 3, 27)]

        call_count = 0

        def fetch_sessions_side_effect(date_str):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("backfill error")
            return SessionActivity({})

        store.fetch_sessions.side_effect = fetch_sessions_side_effect
        slack_client = run_patches["SlackClient"].return_value

        run(source=None)

        # Backfill failure notified but primary still processed
        assert store.fetch_sessions.call_count == 2
        slack_client.notify_error.assert_called_once()
        backfill_since = slack_client.notify_error.call_args[0][0]
        assert backfill_since.date() == date(2026, 3, 27)

    def test_continues_when_ingestion_fails(self, run_patches):
        store = run_patches["SessionStore"].return_value
        store.ingest.side_effect = RuntimeError("DynamoDB error")
        slack_client = run_patches["SlackClient"].return_value

        run(source=None)

        # Ingestion error notified but pipeline continues
        slack_client.notify_error.assert_called_once()
        store.scan_backfill_dates.assert_called_once()

    def test_marks_reported_after_success(self, run_patches):
        store = run_patches["SessionStore"].return_value

        run(source=None)

        store.mark_reported.assert_called_once_with("2026-03-28")

    def test_passes_target_date_to_date_range(self, run_patches):
        target_since = datetime(2026, 3, 25, 0, 0, tzinfo=JST)
        target_until = datetime(2026, 3, 26, 0, 0, tzinfo=JST)
        run_patches["get_target_date_range"].return_value = (target_since, target_until)

        run(source="manual", target_date="2026-03-25")

        run_patches["get_target_date_range"].assert_called_once_with(
            "manual", target_date="2026-03-25"
        )

    def test_deletes_s3_after_ingest(self, run_patches):
        store = run_patches["SessionStore"].return_value
        store.ingest.return_value = ["claude-sessions/proj/s1.jsonl"]
        session_client = run_patches["SessionClient"].return_value
        session_client.delete_sessions.return_value = 1

        run(source=None)

        session_client.delete_sessions.assert_called_once_with(
            ["claude-sessions/proj/s1.jsonl"],
        )

    @patch("report.pipeline.process_date")
    def test_manual_run_uses_original_until(self, mock_process_date, run_patches):
        """Manual run without --date passes get_target_date_range's partial_until (not full-day)."""
        partial_until = datetime(2026, 3, 28, 15, 30, tzinfo=JST)
        run_patches["get_target_date_range"].return_value = (SINCE, partial_until)

        run(source="manual")

        mock_process_date.assert_called_once()
        call_args = mock_process_date.call_args
        assert call_args[0][0] == SINCE
        assert call_args[0][1] == partial_until

    def test_target_date_skips_backfill(self, run_patches):
        target_since = datetime(2026, 3, 25, 0, 0, tzinfo=JST)
        target_until = datetime(2026, 3, 26, 0, 0, tzinfo=JST)
        run_patches["get_target_date_range"].return_value = (target_since, target_until)
        store = run_patches["SessionStore"].return_value

        run(source="manual", target_date="2026-03-25")

        store.scan_backfill_dates.assert_not_called()
        store.fetch_sessions.assert_called_once_with("2026-03-25")

    def test_date_range_processes_all_dates(self, run_patches):
        target_since = datetime(2026, 3, 25, 0, 0, tzinfo=JST)
        target_until = datetime(2026, 3, 26, 0, 0, tzinfo=JST)
        run_patches["get_target_date_range"].return_value = (target_since, target_until)
        store = run_patches["SessionStore"].return_value

        run(source="manual", target_date="2026-03-25..2026-03-28")

        store.scan_backfill_dates.assert_not_called()
        assert store.fetch_sessions.call_count == 4
        store.fetch_sessions.assert_any_call("2026-03-25")
        store.fetch_sessions.assert_any_call("2026-03-26")
        store.fetch_sessions.assert_any_call("2026-03-27")
        store.fetch_sessions.assert_any_call("2026-03-28")

    def test_target_date_uses_backfill_fetch(self, run_patches):
        """target_date 指定時は Hybrid 経路（is_backfill=True）になる"""
        target_since = datetime(2026, 3, 25, 0, 0, tzinfo=JST)
        target_until = datetime(2026, 3, 26, 0, 0, tzinfo=JST)
        run_patches["get_target_date_range"].return_value = (target_since, target_until)
        github_client = run_patches["GitHubClient"].return_value

        run(source="manual", target_date="2026-03-25")

        for call in github_client.fetch_activity.call_args_list:
            assert call.kwargs.get("is_backfill") is True

    def test_scan_backfill_dates_use_hybrid_but_primary_does_not(self, run_patches):
        """scan_backfill_dates 由来の日付のみ is_backfill=True、primary は False"""
        store = run_patches["SessionStore"].return_value
        store.scan_backfill_dates.return_value = [
            date(2026, 3, 26),
            date(2026, 3, 27),
        ]
        github_client = run_patches["GitHubClient"].return_value

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
