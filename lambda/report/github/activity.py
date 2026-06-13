"""GitHubActivity container exposing repo-keyed PR / Issue / Commit data."""

from collections.abc import Callable, KeysView
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
        """Inject session-derived commits for `repo_name` into this activity.

        Deduplicates the commits by SHA across `sessions`, drops ones already
        covered by GitHub search (matched via SHA prefix on the existing repo's
        commits), and resolves PR association via `populate_pull_numbers` on the
        commits actually being injected so they nest under their parent PR in
        downstream rendering.
        """
        seen_shas: set[str] = set()
        session_commits: list[CommitInfo] = []
        for s in sessions:
            for c in s.get("session_commits", []):
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
                if not any(s.startswith(sc.sha) for s in existing_shas)
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
