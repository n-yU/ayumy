"""Tests for the config loader."""

from dataclasses import FrozenInstanceError

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
            CONFIG.claude = ClaudeConfig(model="x", max_tokens=1)  # type: ignore[misc]

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


class TestLoader:
    def test_load_returns_value_equal_to_singleton(self):
        result = _load()
        assert isinstance(result, Config)
        assert result == CONFIG
