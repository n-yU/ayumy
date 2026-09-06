"""Shared fixtures for tests/report/notion/."""

from unittest.mock import MagicMock

import pytest

from report.notion import NotionClient
from report.shared.notice import Notice

from .._builders import OWNER, SINCE, UNTIL, make_repo_activity, make_stub


@pytest.fixture
def notion_client():
    return make_stub(
        NotionClient,
        client=MagicMock(),
        database_id="db-id",
        owner=OWNER,
        _data_source_id=None,
        _notice=Notice(),
    )


@pytest.fixture
def build_status(notion_client):
    """Return a callable that builds the status sections for the given activity."""

    def _build(**activity):
        return notion_client._build_status_sections(
            make_repo_activity(**activity), SINCE, UNTIL
        )

    return _build


@pytest.fixture
def build_timeline(notion_client):
    """Return a callable that builds the timeline section for the given activity."""

    def _build(**activity):
        return notion_client._build_timeline_section(
            make_repo_activity(**activity), SINCE, UNTIL
        )

    return _build
