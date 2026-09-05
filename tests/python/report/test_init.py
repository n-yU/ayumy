"""Tests for report package core utilities."""

import re

import pytest

from config import CONFIG
from report import SummaryUsage, get_version, require_env


class TestGetVersion:
    def test_returns_semver_string(self):
        version = get_version()
        assert re.fullmatch(r"\d+\.\d+\.\d+", version)


class TestRequireEnv:
    def test_returns_value_when_set(self, monkeypatch):
        monkeypatch.setenv("TEST_VAR", "hello")
        assert require_env("TEST_VAR") == "hello"

    def test_raises_when_missing(self, monkeypatch):
        monkeypatch.delenv("TEST_VAR", raising=False)
        with pytest.raises(ValueError, match="TEST_VAR is not set"):
            require_env("TEST_VAR")

    def test_raises_when_empty(self, monkeypatch):
        monkeypatch.setenv("TEST_VAR", "")
        with pytest.raises(ValueError, match="TEST_VAR is not set"):
            require_env("TEST_VAR")


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
