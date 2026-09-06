"""Shared fixtures for tests/report/github/."""

from unittest.mock import MagicMock

import pytest

from report.github import GitHubClient
from report.shared.notice import Notice

from .._builders import REPO, REPO_FULL_NAME, make_stub


@pytest.fixture
def github_client():
    return make_stub(
        GitHubClient,
        g=MagicMock(),
        _search_count=0,
        _window_start=0.0,
        _notice=Notice(),
    )


@pytest.fixture
def repo():
    repo = MagicMock()
    repo.name = REPO
    repo.full_name = REPO_FULL_NAME
    return repo


@pytest.fixture
def activity_repo(github_client, repo):
    """Wire the repo mock behind `get_user().get_repo()`, the lookup `fetch_activity` performs."""
    user = MagicMock()
    github_client.g.get_user.return_value = user
    user.get_repo.return_value = repo
    return repo
