"""Shared fixtures for tests/report/notion/."""

from unittest.mock import MagicMock

import pytest

from report import notion
from report.shared.notice import Notice

from .. import _builders


@pytest.fixture
def notion_client():
    return _builders.stub(
        notion.Client,
        client=MagicMock(),
        database_id="db-id",
        owner=_builders.OWNER,
        _data_source_id=None,
        _notice=Notice(),
    )


@pytest.fixture
def build_status(notion_client):
    """Return a callable that builds the status sections for the given activity."""

    def _build(**activity):
        return notion_client._build_status_sections(
            _builders.repo_activity(**activity), _builders.SINCE, _builders.UNTIL
        )

    return _build


@pytest.fixture
def build_timeline(notion_client):
    """Return a callable that builds the timeline section for the given activity."""

    def _build(**activity):
        return notion_client._build_timeline_section(
            _builders.repo_activity(**activity), _builders.SINCE, _builders.UNTIL
        )

    return _build
