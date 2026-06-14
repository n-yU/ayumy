"""Shared fixtures for tests/report/."""

from unittest.mock import MagicMock

import pytest

from report.summarizer import ValidationResult

from ._builders import OWNER


@pytest.fixture
def pipeline_clients():
    github_client = MagicMock()
    github_client.owner = OWNER
    summary_client = MagicMock()
    summary_client.validate_report.return_value = ValidationResult()
    return {
        "github_client": github_client,
        "notion_client": MagicMock(),
        "summary_client": summary_client,
        "slack_client": MagicMock(),
    }
