"""GitHubActivity container exposing repo-keyed PR / Issue / Commit data."""

from collections.abc import KeysView
from typing import TypedDict

from ..domain import CommitInfo, IssueInfo, PullInfo


class RepoActivity(TypedDict):
    commits: list[CommitInfo]
    pulls: list[PullInfo]
    issues: list[IssueInfo]


class GitHubActivity:
    """GitHub activity data keyed by repository name."""

    def __init__(self, data: dict[str, RepoActivity]) -> None:
        self._data = data

    def repos(self) -> dict[str, RepoActivity]:
        return self._data

    def keys(self) -> KeysView[str]:
        return self._data.keys()

    def __bool__(self) -> bool:
        return bool(self._data)

    def __contains__(self, key: str) -> bool:
        return key in self._data

    def format(self) -> str:
        """Format GitHub activity as the Markdown block consumed by the Claude API prompt (Spec.md §5.4)."""
        if not self._data:
            return "# GitHub アクティビティ\nアクティビティなし"

        lines = ["# GitHub アクティビティ"]
        for repo_name, data in sorted(self._data.items()):
            lines.append(f"## {repo_name}")

            if data["commits"]:
                lines.append("### Commits")
                for c in data["commits"]:
                    lines.append(f"- {c.message}")

            if data["pulls"]:
                lines.append("### Pull Requests")
                for pr in data["pulls"]:
                    labels = f" ({', '.join(pr.labels)})" if pr.labels else ""
                    lines.append(f"- [{pr.state}] #{pr.number} {pr.title}{labels}")

            if data["issues"]:
                lines.append("### Issues")
                for issue in data["issues"]:
                    labels = f" ({', '.join(issue.labels)})" if issue.labels else ""
                    lines.append(
                        f"- [{issue.state}] #{issue.number} {issue.title}{labels}"
                    )

        return "\n".join(lines)
