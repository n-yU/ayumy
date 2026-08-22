"""Shared fixtures for tests/report/github/."""

from unittest.mock import MagicMock

import pytest

from report.github import GitHubClient
from report.notice import Notice

from .._builders import OWNER, make_stub


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
    repo.name = "my-repo"
    repo.full_name = f"{OWNER}/my-repo"
    return repo


@pytest.fixture
def activity_repo(github_client, repo):
    """Wire the repo mock behind `get_user().get_repo()`, the lookup `fetch_activity` performs."""
    user = MagicMock()
    github_client.g.get_user.return_value = user
    user.get_repo.return_value = repo
    return repo
