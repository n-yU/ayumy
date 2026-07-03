"""Load tuning constants from config.yml into a frozen dataclass singleton."""

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class ClaudeConfig:
    model: str
    max_tokens: int


@dataclass(frozen=True)
class SlackConfig:
    headline_max: int


@dataclass(frozen=True)
class GitHubConfig:
    search_batch: int
    search_window_sec: int


@dataclass(frozen=True)
class PipelineConfig:
    max_backfill: int
    max_range_days: int


@dataclass(frozen=True)
class Config:
    claude: ClaudeConfig
    slack: SlackConfig
    github: GitHubConfig
    pipeline: PipelineConfig


def _load() -> Config:
    config_path = Path(__file__).parent / "config.yml"
    with config_path.open() as f:
        data = yaml.safe_load(f)
    return Config(
        claude=ClaudeConfig(**data["claude"]),
        slack=SlackConfig(**data["slack"]),
        github=GitHubConfig(**data["github"]),
        pipeline=PipelineConfig(**data["pipeline"]),
    )


CONFIG = _load()
