"""Tests for the config loader."""

import re
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from config import CONFIG, config

TEMPLATE_PATH = Path(config.__file__).parent / "config.template.yml"


class TestConfigShape:
    def test_config_is_frozen_config_dataclass(self):
        assert isinstance(CONFIG, config.Config)

    def test_all_sections_are_populated(self):
        assert isinstance(CONFIG.claude, config.ClaudeConfig)
        assert isinstance(CONFIG.slack, config.SlackConfig)
        assert isinstance(CONFIG.slack.notify, config.SlackNotify)
        assert isinstance(CONFIG.github, config.GitHubConfig)
        assert isinstance(CONFIG.pipeline, config.PipelineConfig)
        assert isinstance(CONFIG.notion, config.NotionConfig)

    def test_top_level_config_is_immutable(self):
        section = config.ClaudeConfig(model="x", max_tokens=1, pricing={})
        with pytest.raises(FrozenInstanceError):
            CONFIG.claude = section  # type: ignore[misc]

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
        assert CONFIG.pipeline.timeout_margin_sec > 0

    def test_notification_switches_are_booleans(self):
        assert isinstance(CONFIG.slack.notify.no_activity, bool)
        assert isinstance(CONFIG.slack.notify.session_only, bool)

    def test_active_model_has_pricing_entry(self):
        assert CONFIG.claude.model in CONFIG.claude.pricing

    def test_repository_icons_stays_at_the_end_of_the_file(self):
        # `ayumy setup-hooks` appends entries at EOF instead of locating the map, so nothing may follow it
        last = TEMPLATE_PATH.read_text(encoding="utf-8").rstrip().splitlines()[-1]
        assert re.fullmatch(
            r"    (# )?\S+: \{ name: .+, color: \w+ \}", last
        ) or re.fullmatch(r"  repository_icons:", last), last

    def test_unconfigured_repository_falls_back_to_the_default_icon(self):
        assert CONFIG.notion.icon_for("no-such-repo") is CONFIG.notion.default_icon

    def test_pricing_entries_have_positive_input_and_output_rates(self):
        for model, rates in CONFIG.claude.pricing.items():
            assert rates["input_usd_per_1m_tokens"] > 0, model
            assert rates["output_usd_per_1m_tokens"] > 0, model


class TestLoader:
    def test_load_returns_value_equal_to_singleton(self):
        result = config._load()
        assert isinstance(result, config.Config)
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
            "slack": {
                "headline_max": 1,
                "notify": {"no_activity": False, "session_only": True},
            },
            "github": {"search_batch": 1, "search_window_sec": 1},
            "pipeline": {"max_backfill": 1, "max_range_days": 1},
        }
        with patch("config.config.yaml.safe_load", return_value=stub):
            with pytest.raises(ValueError, match="unregistered-model"):
                config._load()

    def test_load_raises_with_the_generation_target_when_config_is_missing(self):
        with patch.object(Path, "exists", return_value=False):
            with pytest.raises(FileNotFoundError, match="make config-init"):
                config._load()

    def test_load_accepts_a_repository_icons_map_with_no_entries(self):
        # The state of a checkout where `ayumy setup-hooks` has not run yet, which YAML reads as None
        stub = yaml.safe_load(TEMPLATE_PATH.read_text(encoding="utf-8"))
        stub["notion"]["repository_icons"] = None
        with patch("config.config.yaml.safe_load", return_value=stub):
            assert config._load().notion.repository_icons == {}

    def test_load_raises_when_notification_switches_are_missing(self):
        stub = yaml.safe_load(TEMPLATE_PATH.read_text(encoding="utf-8"))
        del stub["slack"]["notify"]
        with patch("config.config.yaml.safe_load", return_value=stub):
            with pytest.raises(KeyError, match="notify"):
                config._load()
