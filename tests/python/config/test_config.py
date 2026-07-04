"""Tests for the config loader."""

from dataclasses import FrozenInstanceError
from unittest.mock import patch

import pytest

from config import CONFIG
from config.config import (
    ClaudeConfig,
    Config,
    GitHubConfig,
    PipelineConfig,
    SlackConfig,
    _load,
)


class TestConfigShape:
    def test_config_is_frozen_config_dataclass(self):
        assert isinstance(CONFIG, Config)

    def test_all_sections_are_populated(self):
        assert isinstance(CONFIG.claude, ClaudeConfig)
        assert isinstance(CONFIG.slack, SlackConfig)
        assert isinstance(CONFIG.github, GitHubConfig)
        assert isinstance(CONFIG.pipeline, PipelineConfig)

    def test_top_level_config_is_immutable(self):
        with pytest.raises(FrozenInstanceError):
            CONFIG.claude = ClaudeConfig(model="x", max_tokens=1, pricing={})  # type: ignore[misc]

    def test_section_dataclass_is_immutable(self):
        with pytest.raises(FrozenInstanceError):
            CONFIG.claude.model = "x"  # type: ignore[misc]


class TestConfigValues:
    def test_claude_model_is_non_empty_string(self):
        assert isinstance(CONFIG.claude.model, str)
        assert CONFIG.claude.model

    def test_numeric_values_are_positive(self):
        assert CONFIG.claude.max_tokens > 0
        assert CONFIG.slack.headline_max > 0
        assert CONFIG.github.search_batch > 0
        assert CONFIG.github.search_window_sec > 0
        assert CONFIG.pipeline.max_backfill > 0
        assert CONFIG.pipeline.max_range_days > 0

    def test_active_model_has_pricing_entry(self):
        assert CONFIG.claude.model in CONFIG.claude.pricing

    def test_pricing_entries_have_positive_input_and_output_rates(self):
        for model, rates in CONFIG.claude.pricing.items():
            assert rates["input_usd_per_1m_tokens"] > 0, model
            assert rates["output_usd_per_1m_tokens"] > 0, model


class TestLoader:
    def test_load_returns_value_equal_to_singleton(self):
        result = _load()
        assert isinstance(result, Config)
        assert result == CONFIG

    def test_load_raises_when_active_model_missing_from_pricing(self):
        stub = {
            "claude": {
                "model": "unregistered-model",
                "max_tokens": 1,
                "pricing": {
                    "other-model": {
                        "input_usd_per_1m_tokens": 1.0,
                        "output_usd_per_1m_tokens": 2.0,
                    },
                },
            },
            "slack": {"headline_max": 1},
            "github": {"search_batch": 1, "search_window_sec": 1},
            "pipeline": {"max_backfill": 1, "max_range_days": 1},
        }
        with patch("config.config.yaml.safe_load", return_value=stub):
            with pytest.raises(ValueError, match="unregistered-model"):
                _load()
