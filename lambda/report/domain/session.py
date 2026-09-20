"""Claude Code session types and the repo-keyed container over them."""

from collections.abc import Container, KeysView
from datetime import datetime
from typing import NotRequired, TypedDict, overload

from ..shared import dates


class SessionCommit(TypedDict):
    """Commit recovered from session logs; `timestamp` may be absent on legacy DynamoDB entries."""

    sha: str
    message: str
    timestamp: NotRequired[datetime]


class SessionInfo(TypedDict):
    session_id: str
    project: str
    start_time: datetime
    end_time: datetime
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

    @overload
    def get(self, key: str) -> list[SessionInfo] | None: ...

    @overload
    def get(self, key: str, default: list[SessionInfo]) -> list[SessionInfo]: ...

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
    def _format_time(timestamp: datetime) -> str:
        return timestamp.astimezone(dates.JST).strftime("%H:%M")
