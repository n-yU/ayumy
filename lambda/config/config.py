"""Load tuning constants from config.yml into a frozen dataclass singleton."""

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class ClaudeConfig:
    model: str
    max_tokens: int
    pricing: dict[str, dict[str, float]]


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
class NotionIcon:
    name: str
    color: str


@dataclass(frozen=True)
class NotionConfig:
    default_icon: NotionIcon
    repository_icons: dict[str, NotionIcon]

    def icon_for(self, repo_name: str) -> NotionIcon:
        return self.repository_icons.get(repo_name, self.default_icon)


@dataclass(frozen=True)
class Config:
    claude: ClaudeConfig
    slack: SlackConfig
    github: GitHubConfig
    pipeline: PipelineConfig
    notion: NotionConfig


def _load() -> Config:
    config_path = Path(__file__).parent / "config.yml"
    with config_path.open() as f:
        data = yaml.safe_load(f)
    claude = ClaudeConfig(**data["claude"])
    if claude.model not in claude.pricing:
        raise ValueError(
            f"claude.model '{claude.model}' has no entry in claude.pricing"
        )
    notion = data["notion"]
    return Config(
        claude=claude,
        slack=SlackConfig(**data["slack"]),
        github=GitHubConfig(**data["github"]),
        pipeline=PipelineConfig(**data["pipeline"]),
        notion=NotionConfig(
            default_icon=NotionIcon(**notion["default_icon"]),
            # Empty when no repository has been configured yet, which YAML reads as None
            repository_icons={
                repo: NotionIcon(**icon)
                for repo, icon in (notion["repository_icons"] or {}).items()
            },
        ),
    )


CONFIG = _load()
