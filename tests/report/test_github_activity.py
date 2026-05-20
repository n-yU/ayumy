"""Tests for GitHubActivity formatting."""

from report import GitHubActivity


class TestGitHubActivityFormat:
    def test_empty_activity(self):
        activity = GitHubActivity({})
        assert activity.format() == "# GitHub アクティビティ\nアクティビティなし"

    def test_with_commits_prs_issues(self):
        data = {
            "my-repo": {
                "commits": [{"message": "Fix bug"}],
                "pulls": [
                    {
                        "number": 1,
                        "title": "Add feature",
                        "state": "merged",
                        "labels": ["enhancement"],
                    }
                ],
                "issues": [
                    {
                        "number": 2,
                        "title": "Bug report",
                        "state": "closed",
                        "labels": [],
                    }
                ],
            },
        }
        result = GitHubActivity(data).format()
        assert "## my-repo" in result
        assert "- Fix bug" in result
        assert "- [merged] #1 Add feature (enhancement)" in result
        assert "- [closed] #2 Bug report" in result

    def test_repos_sorted_alphabetically(self):
        data = {
            "z-repo": {"commits": [{"message": "z"}], "pulls": [], "issues": []},
            "a-repo": {"commits": [{"message": "a"}], "pulls": [], "issues": []},
        }
        result = GitHubActivity(data).format()
        assert result.index("a-repo") < result.index("z-repo")

    def test_bool_and_contains(self):
        activity = GitHubActivity({"repo": {"commits": [], "pulls": [], "issues": []}})
        assert bool(activity)
        assert "repo" in activity
        assert "other" not in activity
        assert not bool(GitHubActivity({}))
