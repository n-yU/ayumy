"""Ayumy daily report generator."""

import os
from collections.abc import Container, KeysView
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import NotRequired, TypedDict

from config import CONFIG

from .shared.dates import JST


class SessionCommit(TypedDict):
    """Commit recovered from session logs; `timestamp` may be absent on legacy DynamoDB entries."""

    sha: str
    message: str
    timestamp: NotRequired[str]


class SessionInfo(TypedDict):
    session_id: str
    project: str
    start_time: str
    end_time: str
    user_messages: list[str]
    tools_used: list[str]
    session_commits: list[SessionCommit]
    session_pulls: list[int]
    session_issues: list[int]


class SessionActivity:
    """Claude Code session activity data keyed by repository name."""

    def __init__(self, data: dict[str, list[SessionInfo]]) -> None:
        self._data = data

    def repos(self) -> dict[str, list[SessionInfo]]:
        return self._data

    def keys(self) -> KeysView[str]:
        return self._data.keys()

    def __bool__(self) -> bool:
        return bool(self._data)

    def __contains__(self, key: str) -> bool:
        return key in self._data

    def get(
        self, key: str, default: list[SessionInfo] | None = None
    ) -> list[SessionInfo] | None:
        return self._data.get(key, default)

    def without(self, repos: Container[str]) -> "SessionActivity":
        """Return a new SessionActivity with the given repos removed; used to strip session-only entries from the Claude prompt input."""
        return SessionActivity(
            {name: entries for name, entries in self._data.items() if name not in repos}
        )

    def format(self) -> str:
        """Format session logs as the Markdown block consumed by the Claude API prompt."""
        if not self._data:
            return "# Claude Code セッション\nセッションなし"

        lines = ["# Claude Code セッション"]
        for project_name, sessions in sorted(self._data.items()):
            lines.append(f"## プロジェクト: {project_name}")

            for i, session in enumerate(sessions, 1):
                start = self._format_time(session["start_time"])
                end = self._format_time(session["end_time"])
                lines.append(f"### セッション {i} ({start} - {end})")

                for msg in session["user_messages"]:
                    lines.append(f"- ユーザー: {msg}")

                if session["tools_used"]:
                    tools = ", ".join(session["tools_used"])
                    lines.append(f"- ツール使用: {tools}")

        return "\n".join(lines)

    @staticmethod
    def _format_time(iso_timestamp: str) -> str:
        # Parser fills `start_time` / `end_time` for every session item, so an empty value indicates a parser regression
        if not iso_timestamp:
            raise ValueError("Session timestamp is missing")
        dt = datetime.fromisoformat(iso_timestamp).astimezone(JST)
        return dt.strftime("%H:%M")


class RepoSummary(TypedDict):
    name: str
    summary: list[str]
    tags: list[str]


class ReportSummary(TypedDict):
    repositories: list[RepoSummary]


@dataclass(frozen=True)
class SummaryUsage:
    """Token counts and USD spend for a single Claude API call.

    `spend_usd` is computed locally from the active model's configured rates,
    not returned by the Anthropic API.
    """

    input_tokens: int
    output_tokens: int
    spend_usd: float

    @classmethod
    def from_call(cls, input_tokens: int, output_tokens: int) -> "SummaryUsage":
        rates = CONFIG.claude.pricing[CONFIG.claude.model]
        spend_usd = (
            input_tokens * rates["input_usd_per_1m_tokens"]
            + output_tokens * rates["output_usd_per_1m_tokens"]
        ) / 1_000_000
        return cls(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            spend_usd=spend_usd,
        )


def require_env(name: str) -> str:
    """Return the environment variable value.

    Raises:
        ValueError: If the variable is unset or empty.
    """
    value = os.environ.get(name)
    if not value:
        raise ValueError(f"{name} is not set")
    return value


@lru_cache
def get_version() -> str:
    """Return the ayumy version recorded in the VERSION file."""
    version_path = Path(__file__).resolve().parent.parent / "VERSION"
    return version_path.read_text().strip()
