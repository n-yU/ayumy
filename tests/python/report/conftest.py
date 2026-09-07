"""Shared fixtures for tests/report/."""

from dataclasses import replace
from unittest.mock import MagicMock, patch

import pytest

from config import CONFIG
from report import pipeline, summarizer
from report.domain import summary

from . import _builders


@pytest.fixture
def notify_flags():
    """Override the notification switches the pipeline reads.

    The config singleton is frozen, so a switch can only be changed by rebinding a rebuilt copy on the module holding the reference.
    """

    def _override(**flags):
        slack = replace(CONFIG.slack, notify=replace(CONFIG.slack.notify, **flags))
        return patch.object(pipeline, "CONFIG", replace(CONFIG, slack=slack))

    return _override


@pytest.fixture
def pipeline_clients():
    github_client = MagicMock()
    github_client.owner = _builders.OWNER
    summary_client = MagicMock()
    summary_client.validate_report.return_value = summarizer.ValidationResult()
    summary_client.generate_summary.return_value = (
        {"repositories": []},
        summary.Usage(input_tokens=0, output_tokens=0, spend_usd=0.0),
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
