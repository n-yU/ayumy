"""Tests for SummaryUsage token accounting."""

import pytest

from config import CONFIG
from report.domain.summary import SummaryUsage


class TestSummaryUsage:
    def test_zero_tokens_produce_zero_spend(self):
        usage = SummaryUsage.from_call(0, 0)
        assert usage.input_tokens == 0
        assert usage.output_tokens == 0
        assert usage.spend_usd == 0.0

    def test_spend_matches_configured_rates(self):
        rates = CONFIG.claude.pricing[CONFIG.claude.model]
        expected = (
            1_000_000 * rates["input_usd_per_1m_tokens"]
            + 500_000 * rates["output_usd_per_1m_tokens"]
        ) / 1_000_000

        usage = SummaryUsage.from_call(1_000_000, 500_000)

        assert usage.spend_usd == pytest.approx(expected)
