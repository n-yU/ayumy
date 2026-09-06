"""Shared fixtures for tests/report/session/."""

from unittest.mock import MagicMock, patch

import pytest

from report.session import SessionStore, parser

from . import _builders


@pytest.fixture
def session_client():
    return MagicMock(bucket="bucket")


@pytest.fixture
def store():
    with patch("report.session.store.boto3"):
        store = SessionStore("table")
    store.table = MagicMock()
    return store


@pytest.fixture
def stub_session_log(session_client):
    """Return a callable that stages the given entries on `session_client` as one session file."""

    def _stub(*entries, repo="repo"):
        session_client.list_session_objects.return_value = [
            {"Key": _builders.SESSION_KEY}
        ]
        session_client.read_repo_name.return_value = repo
        body = MagicMock()
        body.read.return_value = _builders.jsonl(*entries).encode("utf-8")
        session_client.s3.get_object.return_value = {"Body": body}
        return session_client

    return _stub


@pytest.fixture
def run_parser(stub_session_log):
    """Return a callable that parses the given session log entries as a single session file."""

    def _run(*entries, repo="repo"):
        return parser.SessionLogParser().build_items(
            stub_session_log(*entries, repo=repo)
        )

    return _run
