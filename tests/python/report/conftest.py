"""Shared fixtures for tests/report/."""

from unittest.mock import MagicMock

import pytest

from report.domain import summary
from report.summarizer import ValidationResult

from . import _builders


@pytest.fixture
def pipeline_clients():
    github_client = MagicMock()
    github_client.owner = _builders.OWNER
    summary_client = MagicMock()
    summary_client.validate_report.return_value = ValidationResult()
    summary_client.generate_summary.return_value = (
        {"repositories": []},
        summary.SummaryUsage(input_tokens=0, output_tokens=0, spend_usd=0.0),
    )
    cost_store = MagicMock()
    cost_store.start_record.return_value = "2026-03-28#stub"
    return {
        "github_client": github_client,
        "notion_client": MagicMock(),
        "summary_client": summary_client,
        "cost_store": cost_store,
        "slack_client": MagicMock(),
    }
