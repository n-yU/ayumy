"""Tests for report generation pipeline."""

import logging
from contextlib import ExitStack
from datetime import date, datetime
from unittest.mock import patch

import pytest
from botocore.exceptions import ClientError

from config import CONFIG
from report import JST, SessionActivity, SummaryUsage
from report.github import GitHubActivity
from report.pipeline import process_date, run
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

_STUB_USAGE = SummaryUsage(input_tokens=0, output_tokens=0, spend_usd=0.0)


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
        pipeline_clients["summary_client"].generate_summary.return_value = (
            report,
            _STUB_USAGE,
        )
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

    def test_records_cost_after_summary_generation(self, pipeline_clients):
        session = make_session("my-repo")
        pipeline_clients["github_client"].fetch_activity.return_value = make_github(
            "my-repo", commits=[make_commit(sha="abc")]
        )
        report = _make_report([{"name": "my-repo", "summary": ["work"], "tags": []}])
        pipeline_clients["summary_client"].generate_summary.return_value = (
            report,
            SummaryUsage(input_tokens=1000, output_tokens=200, spend_usd=0.012),
        )
        pipeline_clients["notion_client"].create_report_pages.return_value = []

        process_date(SINCE, UNTIL, session, **pipeline_clients)

        start = pipeline_clients["cost_store"].start_record
        start.assert_called_once()
        target_date, usage = start.call_args.args
        assert target_date == date(2026, 3, 28)
        assert usage.spend_usd == 0.012

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
        pipeline_clients["summary_client"].generate_summary.return_value = (
            report,
            _STUB_USAGE,
        )
        invalid = ValidationResult()
        invalid.invalid_tags = {"repo": ["BadTag"]}
        pipeline_clients["summary_client"].validate_report.return_value = invalid
        pipeline_clients["notion_client"].create_report_pages.return_value = []

        process_date(SINCE, UNTIL, session, **pipeline_clients)

        pipeline_clients["slack_client"].notify_validation_errors.assert_called_once()

    def test_invokes_session_commit_merge_per_repo(self, pipeline_clients):
        # Stub fetch_activity so we can observe merge_session_commits on the real container
        activity = make_github("my-repo")
        pipeline_clients["github_client"].fetch_activity.return_value = activity
        session = make_session(
            "my-repo",
            tools=("Bash",),
            session_commits=[{"sha": "a1b2c3d", "message": "Fix login bug"}],
        )
        report = _make_report(
            [{"name": "my-repo", "summary": ["work"], "tags": []}],
        )
        pipeline_clients["summary_client"].generate_summary.return_value = (
            report,
            _STUB_USAGE,
        )
        pipeline_clients["notion_client"].create_report_pages.return_value = []

        process_date(SINCE, UNTIL, session, **pipeline_clients)

        # End-to-end: the session commit reaches the activity that flows to Notion
        notion_args = pipeline_clients["notion_client"].create_report_pages.call_args[0]
        merged = notion_args[4].repos()["my-repo"]["commits"]
        assert [c.sha for c in merged] == ["a1b2c3d"]
        assert merged[0].url == f"https://github.com/{OWNER}/my-repo/commit/a1b2c3d"
        # The summary prompt also sees the session commit: merge must run before format
        summary_args = pipeline_clients["summary_client"].generate_summary.call_args[0]
        assert "Fix login bug" in summary_args[1]
        # Pipeline must wire github_client.populate_commit_pull_numbers as the resolver
        populate = pipeline_clients["github_client"].populate_commit_pull_numbers
        populate.assert_called_once()
        args = populate.call_args.args
        assert args[0] == "my-repo"
        assert [c.sha for c in args[1]] == ["a1b2c3d"]

    def test_all_session_only_skips_summary(self, pipeline_clients):
        session = make_session("repo-a")
        pipeline_clients["github_client"].fetch_activity.return_value = GitHubActivity(
            {}
        )

        process_date(SINCE, UNTIL, session, **pipeline_clients)

        pipeline_clients["summary_client"].generate_summary.assert_not_called()
        pipeline_clients["cost_store"].start_record.assert_not_called()
        pipeline_clients["notion_client"].create_report_pages.assert_not_called()
        pipeline_clients["slack_client"].notify.assert_not_called()
        pipeline_clients["slack_client"].notify_session_only.assert_called_once_with(
            SINCE, ["repo-a"]
        )

    def test_partial_session_only_excludes_from_prompt(self, pipeline_clients):
        session = SessionActivity(
            {
                "repo-a": [
                    {
                        "session_id": "sa",
                        "project": "repo-a",
                        "start_time": "2026-03-28T10:00:00+09:00",
                        "end_time": "2026-03-28T11:00:00+09:00",
                        "user_messages": ["work on repo-a"],
                        "tools_used": ["Edit"],
                        "session_commits": [],
                        "session_pulls": [],
                        "session_issues": [],
                    }
                ],
                "repo-b": [
                    {
                        "session_id": "sb",
                        "project": "repo-b",
                        "start_time": "2026-03-28T12:00:00+09:00",
                        "end_time": "2026-03-28T13:00:00+09:00",
                        "user_messages": ["design work on repo-b"],
                        "tools_used": ["Read"],
                        "session_commits": [],
                        "session_pulls": [],
                        "session_issues": [],
                    }
                ],
            }
        )
        pipeline_clients["github_client"].fetch_activity.return_value = make_github(
            "repo-a", commits=[make_commit(sha="abc", repo="repo-a")]
        )

        report = _make_report([{"name": "repo-a", "summary": ["work"], "tags": []}])
        pipeline_clients["summary_client"].generate_summary.return_value = (
            report,
            _STUB_USAGE,
        )
        pipeline_clients["notion_client"].create_report_pages.return_value = [
            ("repo-a", "https://notion.so/a"),
        ]

        process_date(SINCE, UNTIL, session, **pipeline_clients)

        summary_args = pipeline_clients["summary_client"].generate_summary.call_args[0]
        formatted_sessions = summary_args[2]
        assert "repo-a" in formatted_sessions
        assert "repo-b" not in formatted_sessions
        assert "design work on repo-b" not in formatted_sessions

        notify_kwargs = pipeline_clients["slack_client"].notify.call_args.kwargs
        assert notify_kwargs["session_only_repos"] == ["repo-b"]

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
            "CostStore": "report.pipeline.CostStore",
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

    def test_omits_cost_when_compute_display_fails(self, run_patches):
        cost_store = run_patches["CostStore"].return_value
        cost_store.compute_display.side_effect = RuntimeError("dynamodb down")
        slack_client = run_patches["SlackClient"].return_value

        run(source=None)

        slack_client.notify_metrics.assert_called_once()
        assert slack_client.notify_metrics.call_args.kwargs["cost"] is None
        slack_client.flush.assert_called_once()
        slack_client.send_notice_thread.assert_called_once()

    def test_survives_cost_store_init_failure(self, run_patches):
        run_patches["CostStore"].side_effect = RuntimeError("no env")
        slack_client = run_patches["SlackClient"].return_value

        with pytest.raises(RuntimeError):
            run(source=None)

        # Main-flow error still reaches Slack, and the metrics chain runs with cost=None
        slack_client.notify_error.assert_called_once()
        slack_client.notify_metrics.assert_called_once()
        assert slack_client.notify_metrics.call_args.kwargs["cost"] is None
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
        # 5 past dates, but only CONFIG.pipeline.max_backfill most recent should be processed
        store.scan_backfill_dates.return_value = [
            date(2026, 3, 23),
            date(2026, 3, 24),
            date(2026, 3, 25),
            date(2026, 3, 26),
            date(2026, 3, 27),
        ]

        run(source=None)
        assert store.fetch_sessions.call_count == CONFIG.pipeline.max_backfill + 1

    def test_notify_on_primary_failure(self, run_patches, caplog):
        store = run_patches["SessionStore"].return_value
        store.fetch_sessions.side_effect = RuntimeError("DynamoDB error")
        slack_client = run_patches["SlackClient"].return_value

        with (
            caplog.at_level(logging.ERROR, logger="report.pipeline"),
            pytest.raises(RuntimeError, match="DynamoDB error"),
        ):
            run(source=None)

        slack_client.notify_error.assert_called_once()
        # Metrics should still be sent on failure (finally block)
        slack_client.notify_metrics.assert_called_once()
        slack_client.flush.assert_called_once()
        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert any(
            "Report generation failed for 2026-03-28" in r.getMessage()
            and r.exc_info is not None
            for r in errors
        )

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

        # Backfill failure logged as warning (no notify_error), primary still processed
        assert store.fetch_sessions.call_count == 2
        slack_client.notify_error.assert_not_called()

    def test_ingestion_failure_aborts_run(self, run_patches):
        store = run_patches["SessionStore"].return_value
        store.ingest.side_effect = RuntimeError("DynamoDB error")
        slack_client = run_patches["SlackClient"].return_value

        with pytest.raises(RuntimeError, match="DynamoDB error"):
            run(source=None)

        # Ingestion failure surfaces to the final fallback and Slack notification
        slack_client.notify_error.assert_called_once()
        store.scan_backfill_dates.assert_not_called()

    def test_target_date_per_day_failure_logged_as_warning(self, run_patches, caplog):
        target_since = datetime(2026, 3, 25, 0, 0, tzinfo=JST)
        target_until = datetime(2026, 3, 26, 0, 0, tzinfo=JST)
        run_patches["get_target_date_range"].return_value = (target_since, target_until)
        store = run_patches["SessionStore"].return_value
        slack_client = run_patches["SlackClient"].return_value

        call_count = 0

        def fetch_sessions_side_effect(date_str):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("middle day failure")
            return SessionActivity({})

        store.fetch_sessions.side_effect = fetch_sessions_side_effect

        with caplog.at_level(logging.WARNING, logger="report.pipeline"):
            run(source="manual", target_date="2026-03-25..2026-03-27")

        # All 3 dates attempted, failed day logged as warning, no Slack error notify
        assert store.fetch_sessions.call_count == 3
        slack_client.notify_error.assert_not_called()
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("middle day failure" in r.getMessage() for r in warnings)

    def test_s3_delete_failure_continues_as_warning(self, run_patches, caplog):
        store = run_patches["SessionStore"].return_value
        store.ingest.return_value = ["claude-sessions/proj/s1.jsonl"]
        session_client = run_patches["SessionClient"].return_value
        session_client.delete_sessions.side_effect = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "denied"}}, "DeleteObjects"
        )
        slack_client = run_patches["SlackClient"].return_value

        with caplog.at_level(logging.WARNING, logger="report.pipeline"):
            run(source=None)

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("S3 deletion failed" in r.getMessage() for r in warnings)
        slack_client.notify_error.assert_not_called()
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
        """An explicit target_date takes the Hybrid path (is_backfill=True)."""
        target_since = datetime(2026, 3, 25, 0, 0, tzinfo=JST)
        target_until = datetime(2026, 3, 26, 0, 0, tzinfo=JST)
        run_patches["get_target_date_range"].return_value = (target_since, target_until)
        github_client = run_patches["GitHubClient"].return_value

        run(source="manual", target_date="2026-03-25")

        for call in github_client.fetch_activity.call_args_list:
            assert call.kwargs.get("is_backfill") is True

    def test_scan_backfill_dates_use_hybrid_but_primary_does_not(self, run_patches):
        """Only dates from scan_backfill_dates get is_backfill=True; the primary date does not."""
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
