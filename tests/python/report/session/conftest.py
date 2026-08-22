"""Shared fixtures for tests/report/session/."""

from unittest.mock import MagicMock, patch

import pytest

from report.session.parser import SessionLogParser
from report.session.store import SessionStore

from ._builders import SESSION_KEY, jsonl


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
def run_parser(session_client):
    """Return a callable that parses the given session log entries as a single session file."""

    def _run(*entries, repo="repo"):
        session_client.list_session_objects.return_value = [{"Key": SESSION_KEY}]
        session_client.read_repo_name.return_value = repo
        body = MagicMock()
        body.read.return_value = jsonl(*entries).encode("utf-8")
        session_client.s3.get_object.return_value = {"Body": body}
        return SessionLogParser().build_items(session_client)

    return _run
