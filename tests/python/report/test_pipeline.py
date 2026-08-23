"""Tests for report generation pipeline."""

import logging
from contextlib import ExitStack
from datetime import date, datetime, timedelta
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
    SINCE,
    UNTIL,
    assert_published,
    assert_skipped,
    make_commit,
    make_github,
    make_session,
    make_session_entry,
)

_STUB_USAGE = SummaryUsage(input_tokens=0, output_tokens=0, spend_usd=0.0)


def _repo(name, summary=("work",), tags=()):
    return {"name": name, "summary": list(summary), "tags": list(tags)}


def _stub_fetch_activity(clients, activity):
    clients["github_client"].fetch_activity.return_value = activity


def _stub_summary(clients, repos, *, usage=_STUB_USAGE, pages=()):
    """Stub the Claude summary and the Notion pages it leads to."""
    clients["summary_client"].generate_summary.return_value = (
        {"repositories": repos},
        usage,
    )
    clients["notion_client"].create_report_pages.return_value = list(pages)


def _stub_date_range(run_patches, day):
    """Point the window at the given March day, as an explicit --date would."""
    since = datetime(2026, 3, day, tzinfo=JST)
    run_patches["get_target_date_range"].return_value = (
        since,
        since + timedelta(days=1),
    )


def _fail_on_nth_fetch(store, n, message):
    """Raise from the `n`-th fetch_sessions call so the surrounding dates still succeed."""
    calls = 0

    def _fetch(_date_str):
        nonlocal calls
        calls += 1
        if calls == n:
            raise RuntimeError(message)
        return SessionActivity({})

    store.fetch_sessions.side_effect = _fetch


class TestProcessDate:
    def test_skips_when_no_activity(self, pipeline_clients):
        _stub_fetch_activity(pipeline_clients, GitHubActivity({}))
        process_date(SINCE, UNTIL, SessionActivity({}), **pipeline_clients)

        assert_skipped(pipeline_clients, SINCE)

    def test_generates_report_and_publishes(self, pipeline_clients):
        _stub_fetch_activity(
            pipeline_clients, make_github("my-repo", commits=[make_commit(sha="abc")])
        )
        _stub_summary(
            pipeline_clients,
            [_repo("my-repo")],
            pages=[("my-repo", "https://notion.so/page1")],
        )
        process_date(SINCE, UNTIL, make_session("my-repo"), **pipeline_clients)

        assert_published(pipeline_clients)
        # Verify the (target_date, SINCE, UNTIL) trio is passed in order
        notion_args = pipeline_clients["notion_client"].create_report_pages.call_args[0]
        assert notion_args[:3] == (SINCE, SINCE, UNTIL)

    def test_records_cost_after_summary_generation(self, pipeline_clients):
        _stub_fetch_activity(
            pipeline_clients, make_github("my-repo", commits=[make_commit(sha="abc")])
        )
        _stub_summary(
            pipeline_clients,
            [_repo("my-repo")],
            usage=SummaryUsage(input_tokens=1000, output_tokens=200, spend_usd=0.012),
        )
        process_date(SINCE, UNTIL, make_session("my-repo"), **pipeline_clients)

        start = pipeline_clients["cost_store"].start_record
        start.assert_called_once()
        target_date, usage = start.call_args.args
        assert target_date == date(2026, 3, 28)
        assert usage.spend_usd == 0.012

    def test_notifies_validation_errors(self, pipeline_clients):
        _stub_fetch_activity(
            pipeline_clients,
            make_github("repo", commits=[make_commit(sha="abc", repo="repo")]),
        )
        _stub_summary(pipeline_clients, [_repo("repo", summary=(), tags=("BadTag",))])
        invalid = ValidationResult()
        invalid.invalid_tags = {"repo": ["BadTag"]}
        pipeline_clients["summary_client"].validate_report.return_value = invalid
        process_date(SINCE, UNTIL, make_session("repo"), **pipeline_clients)

        pipeline_clients["slack_client"].notify_validation_errors.assert_called_once()

    def test_invokes_session_commit_merge_per_repo(self, pipeline_clients):
        # Stub fetch_activity so we can observe merge_session_commits on the real container
        _stub_fetch_activity(pipeline_clients, make_github("my-repo"))
        _stub_summary(pipeline_clients, [_repo("my-repo")])
        session = make_session(
            "my-repo",
            tools=("Bash",),
            session_commits=[{"sha": "a1b2c3d", "message": "Fix login bug"}],
        )
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
        _stub_fetch_activity(pipeline_clients, GitHubActivity({}))
        process_date(SINCE, UNTIL, make_session("repo-a"), **pipeline_clients)

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
                    make_session_entry(
                        session_id="sa",
                        project="repo-a",
                        messages=("work on repo-a",),
                    )
                ],
                "repo-b": [
                    make_session_entry(
                        session_id="sb",
                        project="repo-b",
                        start="2026-03-28T12:00:00+09:00",
                        end="2026-03-28T13:00:00+09:00",
                        messages=("design work on repo-b",),
                        tools=("Read",),
                    )
                ],
            }
        )
        _stub_fetch_activity(
            pipeline_clients,
            make_github("repo-a", commits=[make_commit(sha="abc", repo="repo-a")]),
        )
        _stub_summary(
            pipeline_clients,
            [_repo("repo-a")],
            pages=[("repo-a", "https://notion.so/a")],
        )
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
        _stub_fetch_activity(pipeline_clients, GitHubActivity({}))
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
        _stub_fetch_activity(pipeline_clients, GitHubActivity({}))
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

    @pytest.fixture
    def slack_client(self, run_patches):
        return run_patches["SlackClient"].return_value

    @pytest.fixture
    def session_store(self, run_patches):
        return run_patches["SessionStore"].return_value

    def test_processes_primary_date(self, session_store, slack_client):
        run(source=None)

        session_store.scan_backfill_dates.assert_called_once()
        session_store.fetch_sessions.assert_called_once_with("2026-03-28")
        slack_client.notify_metrics.assert_called_once()
        args = slack_client.notify_metrics.call_args
        elapsed, peak_mb, _version = args.args
        assert elapsed >= 0
        assert peak_mb >= 0
        assert args.kwargs.get("memory_limit_mb") is None
        slack_client.flush.assert_called_once()

    def test_passes_memory_limit_to_metrics(self, slack_client):
        run(source=None, memory_limit_mb=512)

        slack_client.notify_metrics.assert_called_once()
        assert slack_client.notify_metrics.call_args.kwargs["memory_limit_mb"] == 512
        slack_client.flush.assert_called_once()

    @patch("report.pipeline.get_version")
    def test_passes_version_and_timeout_to_metrics(
        self, mock_get_version, slack_client
    ):
        mock_get_version.return_value = "0.2.1"
        run(source=None, memory_limit_mb=512, timeout_seconds=300)

        slack_client.notify_metrics.assert_called_once()
        call = slack_client.notify_metrics.call_args
        # version is the third positional argument
        assert call.args[2] == "0.2.1"
        assert call.kwargs["timeout_seconds"] == 300
        slack_client.flush.assert_called_once()

    def test_omits_cost_when_compute_display_fails(self, run_patches, slack_client):
        cost_store = run_patches["CostStore"].return_value
        cost_store.compute_display.side_effect = RuntimeError("dynamodb down")
        run(source=None)

        slack_client.notify_metrics.assert_called_once()
        assert slack_client.notify_metrics.call_args.kwargs["cost"] is None
        slack_client.flush.assert_called_once()
        slack_client.send_notice_thread.assert_called_once()

    def test_survives_cost_store_init_failure(self, run_patches, slack_client):
        run_patches["CostStore"].side_effect = RuntimeError("no env")
        with pytest.raises(RuntimeError):
            run(source=None)

        # Main-flow error still reaches Slack, and the metrics chain runs with cost=None
        slack_client.notify_error.assert_called_once()
        slack_client.notify_metrics.assert_called_once()
        assert slack_client.notify_metrics.call_args.kwargs["cost"] is None
        slack_client.flush.assert_called_once()

    def test_backfills_past_dates(self, session_store):
        session_store.scan_backfill_dates.return_value = [
            date(2026, 3, 26),
            date(2026, 3, 27),
        ]
        run(source=None)

        # 2 backfill dates + 1 primary = 3 calls
        assert session_store.fetch_sessions.call_count == 3

    def test_backfill_limited_to_max(self, session_store):
        # 5 past dates, but only CONFIG.pipeline.max_backfill most recent should be processed
        session_store.scan_backfill_dates.return_value = [
            date(2026, 3, 23),
            date(2026, 3, 24),
            date(2026, 3, 25),
            date(2026, 3, 26),
            date(2026, 3, 27),
        ]
        run(source=None)

        assert (
            session_store.fetch_sessions.call_count == CONFIG.pipeline.max_backfill + 1
        )

    def test_notify_on_primary_failure(self, session_store, slack_client, caplog):
        session_store.fetch_sessions.side_effect = RuntimeError("DynamoDB error")
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

    def test_backfill_failure_does_not_stop_primary(self, session_store, slack_client):
        session_store.scan_backfill_dates.return_value = [date(2026, 3, 27)]
        _fail_on_nth_fetch(session_store, 1, "backfill error")
        run(source=None)

        # Backfill failure logged as warning (no notify_error), primary still processed
        assert session_store.fetch_sessions.call_count == 2
        slack_client.notify_error.assert_not_called()

    def test_ingestion_failure_aborts_run(self, session_store, slack_client):
        session_store.ingest.side_effect = RuntimeError("DynamoDB error")
        with pytest.raises(RuntimeError, match="DynamoDB error"):
            run(source=None)

        # Ingestion failure surfaces to the final fallback and Slack notification
        slack_client.notify_error.assert_called_once()
        session_store.scan_backfill_dates.assert_not_called()

    def test_target_date_per_day_failure_logged_as_warning(
        self, run_patches, session_store, slack_client, caplog
    ):
        _stub_date_range(run_patches, 25)
        _fail_on_nth_fetch(session_store, 2, "middle day failure")
        with caplog.at_level(logging.WARNING, logger="report.pipeline"):
            run(source="manual", target_date="2026-03-25..2026-03-27")

        # All 3 dates attempted, failed day logged as warning, no Slack error notify
        assert session_store.fetch_sessions.call_count == 3
        slack_client.notify_error.assert_not_called()
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("middle day failure" in r.getMessage() for r in warnings)

    def test_s3_delete_failure_continues_as_warning(
        self, run_patches, session_store, slack_client, caplog
    ):
        session_store.ingest.return_value = ["claude-sessions/proj/s1.jsonl"]
        session_client = run_patches["SessionClient"].return_value
        session_client.delete_sessions.side_effect = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "denied"}}, "DeleteObjects"
        )
        with caplog.at_level(logging.WARNING, logger="report.pipeline"):
            run(source=None)

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("S3 deletion failed" in r.getMessage() for r in warnings)
        slack_client.notify_error.assert_not_called()
        session_store.scan_backfill_dates.assert_called_once()

    def test_marks_reported_after_success(self, session_store):
        run(source=None)

        session_store.mark_reported.assert_called_once_with("2026-03-28")

    def test_passes_target_date_to_date_range(self, run_patches):
        _stub_date_range(run_patches, 25)
        run(source="manual", target_date="2026-03-25")

        run_patches["get_target_date_range"].assert_called_once_with(
            "manual", target_date="2026-03-25"
        )

    def test_deletes_s3_after_ingest(self, run_patches, session_store):
        session_store.ingest.return_value = ["claude-sessions/proj/s1.jsonl"]
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

    def test_target_date_skips_backfill(self, run_patches, session_store):
        _stub_date_range(run_patches, 25)
        run(source="manual", target_date="2026-03-25")

        session_store.scan_backfill_dates.assert_not_called()
        session_store.fetch_sessions.assert_called_once_with("2026-03-25")

    def test_date_range_processes_all_dates(self, run_patches, session_store):
        _stub_date_range(run_patches, 25)
        run(source="manual", target_date="2026-03-25..2026-03-28")

        session_store.scan_backfill_dates.assert_not_called()
        assert [
            call.args[0] for call in session_store.fetch_sessions.call_args_list
        ] == ["2026-03-25", "2026-03-26", "2026-03-27", "2026-03-28"]

    def test_target_date_uses_backfill_fetch(self, run_patches):
        """An explicit target_date takes the Hybrid path (is_backfill=True)."""
        _stub_date_range(run_patches, 25)
        github_client = run_patches["GitHubClient"].return_value
        run(source="manual", target_date="2026-03-25")

        for call in github_client.fetch_activity.call_args_list:
            assert call.kwargs.get("is_backfill") is True

    def test_scan_backfill_dates_use_hybrid_but_primary_does_not(
        self, run_patches, session_store
    ):
        """Only dates from scan_backfill_dates get is_backfill=True; the primary date does not."""
        session_store.scan_backfill_dates.return_value = [
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
