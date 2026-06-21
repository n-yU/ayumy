"""Tests for SessionClient."""

import logging
from unittest.mock import MagicMock, patch

from report.session import SessionClient


def _make_client():
    with patch("report.session.client.boto3"):
        client = SessionClient("bucket")
    client.s3 = MagicMock()
    return client


class TestDeleteSessions:
    def setup_method(self):
        self.client = _make_client()

    def test_deletes_specified_keys(self):
        self.client.s3.delete_objects.return_value = {
            "Deleted": [
                {"Key": "claude-sessions/proj/s1.jsonl"},
                {"Key": "claude-sessions/proj/s2.jsonl"},
            ],
        }
        keys = [
            "claude-sessions/proj/s1.jsonl",
            "claude-sessions/proj/s2.jsonl",
        ]

        count = self.client.delete_sessions(keys)

        assert count == 2
        self.client.s3.delete_objects.assert_called_once()
        call_args = self.client.s3.delete_objects.call_args
        objects = call_args.kwargs["Delete"]["Objects"]
        assert len(objects) == 2

    def test_returns_zero_for_empty_list(self):
        count = self.client.delete_sessions([])

        assert count == 0
        self.client.s3.delete_objects.assert_not_called()

    def test_partial_failure_logs_warning_and_counts_deleted(self, caplog):
        self.client.s3.delete_objects.return_value = {
            "Deleted": [{"Key": "claude-sessions/proj/s1.jsonl"}],
            "Errors": [
                {"Key": "claude-sessions/proj/s2.jsonl", "Code": "AccessDenied"},
            ],
        }

        with caplog.at_level(logging.WARNING, logger="report.session.client"):
            count = self.client.delete_sessions(
                ["claude-sessions/proj/s1.jsonl", "claude-sessions/proj/s2.jsonl"]
            )

        assert count == 1
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warnings, "partial failure must surface as a warning"
        assert "AccessDenied" in warnings[0].getMessage()


class TestReadRepoName:
    def setup_method(self):
        self.client = _make_client()

        class _NoSuchKey(Exception):
            pass

        self.client.s3.exceptions.NoSuchKey = _NoSuchKey
        self.no_such_key = _NoSuchKey

    def test_missing_object_is_suppressed_silently(self, caplog):
        self.client.s3.get_object.side_effect = self.no_such_key

        with caplog.at_level(logging.WARNING, logger="report.session.client"):
            assert self.client.read_repo_name("proj") is None
        assert not [r for r in caplog.records if r.levelno == logging.WARNING]

    def test_invalid_content_logs_warning(self, caplog):
        self.client.s3.get_object.return_value = {
            "Body": MagicMock(read=MagicMock(return_value=b"owner/repo")),
        }

        with caplog.at_level(logging.WARNING, logger="report.session.client"):
            assert self.client.read_repo_name("proj") is None
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warnings
        assert "proj" in warnings[0].getMessage()
