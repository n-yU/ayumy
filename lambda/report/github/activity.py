"""GitHubActivity container exposing repo-keyed PR / Issue / Commit data."""

from collections.abc import Callable, KeysView
from datetime import datetime
from typing import TYPE_CHECKING, TypedDict

from ..domain import CommitInfo, IssueInfo, PullInfo

if TYPE_CHECKING:
    from .. import SessionInfo


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

    def merge_session_commits(
        self,
        repo_name: str,
        sessions: "list[SessionInfo]",
        *,
        owner: str,
        populate_pull_numbers: Callable[[str, list[CommitInfo]], None],
    ) -> None:
        """Dedup by SHA across `sessions`, drop those already covered by GitHub search (SHA-prefix match), and resolve PR association via `populate_pull_numbers` so injected commits nest under their parent PR."""
        seen_shas: set[str] = set()
        session_commits: list[CommitInfo] = []
        for s in sessions:
            for c in s["session_commits"]:
                if c["sha"] in seen_shas:
                    continue
                seen_shas.add(c["sha"])
                session_commits.append(
                    CommitInfo(
                        sha=c["sha"],
                        message=c["message"],
                        author="",
                        date=c.get("timestamp") or s["start_time"],
                        url=f"https://github.com/{owner}/{repo_name}/commit/{c['sha']}",
                    )
                )
        if not session_commits:
            return
        repo_data = self._data.get(repo_name)
        if repo_data is not None:
            existing_shas = {c.sha for c in repo_data["commits"]}
            new_commits = [
                sc
                for sc in session_commits
                if not any(
                    existing_sha.startswith(sc.sha) for existing_sha in existing_shas
                )
            ]
            if new_commits:
                populate_pull_numbers(repo_name, new_commits)
                repo_data["commits"].extend(new_commits)
        else:
            populate_pull_numbers(repo_name, session_commits)
            self._data[repo_name] = {
                "commits": session_commits,
                "pulls": [],
                "issues": [],
            }

    def format(self, since: datetime, until: datetime) -> str:
        """Format GitHub activity as the Markdown block consumed by the Claude API prompt (Spec: Summary Generation).

        Filtered on the same window basis as the report page so the summary cannot describe items the page does not list.
        """
        lines = ["# GitHub アクティビティ"]
        for repo_name, data in sorted(self._data.items()):
            repo_lines: list[str] = []

            commits = [c for c in data["commits"] if c.is_in_range(since, until)]
            if commits:
                repo_lines.append("### Commits")
                for c in commits:
                    repo_lines.append(f"- {c.message}")

            pulls = [
                (pr, state)
                for pr in data["pulls"]
                if (state := pr.state_in_range(since, until)) is not None
            ]
            if pulls:
                repo_lines.append("### Pull Requests")
                for pr, state in pulls:
                    labels = f" ({', '.join(pr.labels)})" if pr.labels else ""
                    repo_lines.append(f"- [{state}] #{pr.number} {pr.title}{labels}")

            issues = [
                (issue, state)
                for issue in data["issues"]
                if (state := issue.state_in_range(since, until)) is not None
            ]
            if issues:
                repo_lines.append("### Issues")
                for issue, state in issues:
                    labels = f" ({', '.join(issue.labels)})" if issue.labels else ""
                    repo_lines.append(
                        f"- [{state}] #{issue.number} {issue.title}{labels}"
                    )

            if repo_lines:
                lines.append(f"## {repo_name}")
                lines.extend(repo_lines)

        if len(lines) == 1:
            return "# GitHub アクティビティ\nアクティビティなし"

        return "\n".join(lines)
