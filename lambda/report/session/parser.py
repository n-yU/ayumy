"""Session log parser: Bash command analysis + JSONL → DynamoDB item conversion."""

import json
import logging
import re
import shlex
from collections import defaultdict
from datetime import UTC, datetime
from os.path import normpath
from pathlib import Path, PurePosixPath

from .. import JST
from ..notice import Notice, NoticeSource

logger = logging.getLogger(__name__)

_GH_CLI_RE = re.compile(r"\bgh\s+(pr|issue)\s+(\w[\w-]*)")
# Subs that take no positional integer, so digits in their args aren't PR/Issue refs
_GH_CLI_NO_NUMBER_SUBS = {"create", "list", "status"}
_GH_API_RE = re.compile(r"\bgh\s+api\b")
_API_PATH_PR_RE = re.compile(r"\b(?:pulls|pull)/(\d+)\b")
_API_PATH_ISSUE_RE = re.compile(r"\bissues/(\d+)\b")
# `git ... #N` is ambiguous between PR and Issue; the fetcher reconciles via 404 / pull_request attr
_HASH_REF_RE = re.compile(r"(?<![A-Za-z0-9])#(\d+)\b")
_GIT_CLI_RE = re.compile(r"\bgit\s+\w[\w-]*")
_SHELL_STOPS = ("\n", "&&", "||", ";", "|")


def _command_segment(command: str, start: int) -> str:
    end = len(command)
    for stop in _SHELL_STOPS:
        i = command.find(stop, start)
        if i != -1 and i < end:
            end = i
    return command[start:end]


def _first_positional_int(segment: str) -> int | None:
    """Tokenizes via shlex so quoted flag values count as one token, preventing matches against integers like `--body "fix 999"`."""
    try:
        tokens = shlex.split(segment, posix=True)
    except ValueError:
        return None
    for tok in tokens:
        if tok.startswith("-"):
            continue
        if tok.isdigit():
            return int(tok)
    return None


def _expand_home(path: str, project_cwd: str | None) -> str:
    """Resolve a leading `~` against the session author's home inferred from `project_cwd`.

    Lambda's runtime user differs from session author, so `Path.expanduser` would resolve to the wrong home.
    """
    if path != "~" and not path.startswith("~/"):
        return path
    if project_cwd:
        parts = PurePosixPath(project_cwd).parts
        if len(parts) >= 3 and parts[0] == "/" and parts[1] in ("Users", "home"):
            home = PurePosixPath("/", parts[1], parts[2])
            return str(home) + path[1:]
    return str(Path(path).expanduser())


def _effective_cwd(command: str, project_cwd: str | None) -> str | None:
    try:
        # shlex doesn't treat `&&` as an operator, so normalize spacing for forms like `cd /path&&git ...`
        tokens = shlex.split(command.replace("&&", " && "), posix=True)
    except ValueError:
        return None
    if len(tokens) < 3 or tokens[0] != "cd" or tokens[2] != "&&":
        return None
    if tokens[1] == "-":
        return None
    raw = tokens[1]
    target = _expand_home(raw, project_cwd)
    p = PurePosixPath(target)
    if not p.is_absolute():
        if raw.startswith("~") or "$" in raw or "`" in raw:
            # Unresolvable paths (`~user/`, shell expansions) get classified as cross-repo, not joined under project_cwd
            return normpath("/" + raw)
        if project_cwd:
            p = PurePosixPath(project_cwd) / p
    # normpath collapses `..` / `.` segments without touching the filesystem
    return normpath(str(p))


def _is_cross_repo(effective_cwd: str | None, project_cwd: str | None) -> bool:
    if not project_cwd or effective_cwd is None:
        return False
    cwd = PurePosixPath(effective_cwd)
    root = PurePosixPath(project_cwd)
    return cwd != root and not cwd.is_relative_to(root)


def _extract_pr_issue_refs(command: str) -> tuple[set[int], set[int]]:
    """Extract the PR and Issue numbers referenced by a Bash command.

    `#N` inside `git` args is ambiguous between PR and Issue; placed in both sets, fetcher reconciles via 404 or `pull_request` attribute.
    """
    pulls: set[int] = set()
    issues: set[int] = set()

    for m in _GH_CLI_RE.finditer(command):
        kind = m.group(1)
        sub = m.group(2)
        if sub in _GH_CLI_NO_NUMBER_SUBS:
            continue
        segment = _command_segment(command, m.end())
        n = _first_positional_int(segment)
        if n is not None:
            (pulls if kind == "pr" else issues).add(n)

    for m in _GH_API_RE.finditer(command):
        segment = _command_segment(command, m.end())
        for sm in _API_PATH_PR_RE.finditer(segment):
            pulls.add(int(sm.group(1)))
        for sm in _API_PATH_ISSUE_RE.finditer(segment):
            issues.add(int(sm.group(1)))

    for m in _GIT_CLI_RE.finditer(command):
        segment = _command_segment(command, m.end())
        for ref in _HASH_REF_RE.findall(segment):
            n = int(ref)
            pulls.add(n)
            issues.add(n)

    return pulls, issues


class SessionLogParser:
    """Convert JSONL session logs read via `SessionClient` into DynamoDB-shaped items."""

    def __init__(self, notice: Notice | None = None) -> None:
        self._notice = notice or Notice()

    def build_items(self, session_client) -> tuple[list[dict], list[str]]:
        """Groups by (JST date, repo, session_id). Entries without timestamps and projects without `.ayumy_repo` are skipped."""
        # MULTILINE lets the commit summary line match even when hook output precedes it
        commit_pattern = re.compile(r"^\[.+\s+([0-9a-f]+)\]\s+(.+)", re.MULTILINE)

        groups: dict[tuple[str, str, str], dict] = defaultdict(
            lambda: {
                "project": "",
                "timestamps": [],
                "user_messages": [],
                "tools_used": set(),
                "commits": [],
                "pulls": set(),
                "issues": set(),
            }
        )

        repo_cache: dict[str, str | None] = {}
        key_groups: dict[str, set[tuple[str, str, str]]] = defaultdict(set)

        for obj in session_client.list_session_objects():
            key = obj["Key"]
            parts = key.split("/")
            project = parts[1] if len(parts) >= 3 else "unknown"
            session_id = parts[-1].removesuffix(".jsonl")

            if project not in repo_cache:
                repo_cache[project] = session_client.read_repo_name(project)
            repo = repo_cache[project]
            if repo is None:
                continue

            resp = session_client.s3.get_object(
                Bucket=session_client.bucket,
                Key=key,
            )
            body = resp["Body"].read().decode("utf-8")

            # cwd-based cross-repo filter state, scoped per session file
            project_cwd: str | None = None
            tool_use_cwds: dict[str, str | None] = {}

            for line in body.splitlines():
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    self._notice.add(
                        NoticeSource.SESSION,
                        "Malformed JSONL line skipped",
                        logger=logger,
                        key=key,
                    )
                    continue

                entry_cwd_raw = entry.get("cwd")
                if entry_cwd_raw is None or entry_cwd_raw == "":
                    entry_cwd = None
                elif isinstance(entry_cwd_raw, str):
                    entry_cwd = entry_cwd_raw
                else:
                    self._notice.add(
                        NoticeSource.SESSION,
                        "Unexpected cwd type",
                        logger=logger,
                        key=key,
                        type=type(entry_cwd_raw).__name__,
                    )
                    entry_cwd = None
                if project_cwd is None and entry_cwd:
                    project_cwd = entry_cwd

                timestamp = entry.get("timestamp")
                if not timestamp:
                    continue

                entry_dt = datetime.fromisoformat(timestamp).astimezone(JST)
                date_str = entry_dt.date().isoformat()
                group_key = (date_str, repo, session_id)
                key_groups[key].add(group_key)
                group = groups[group_key]
                group["project"] = project
                group["timestamps"].append(timestamp)

                entry_type = entry.get("type")
                if entry_type == "user":
                    content = entry.get("message", {}).get("content", "")
                    if isinstance(content, str) and content.strip():
                        group["user_messages"].append(content.strip())
                    elif isinstance(content, list):
                        for block in content:
                            if block.get("type") != "tool_result" or block.get(
                                "is_error"
                            ):
                                continue
                            tool_use_id = block.get("tool_use_id")
                            cwd = (
                                tool_use_cwds.get(tool_use_id) if tool_use_id else None
                            )
                            if _is_cross_repo(cwd, project_cwd):
                                continue
                            text = block.get("content", "")
                            if isinstance(text, str):
                                for sha, msg in commit_pattern.findall(text):
                                    group["commits"].append(
                                        {
                                            "sha": sha,
                                            "message": msg,
                                            "timestamp": timestamp,
                                        }
                                    )
                elif entry_type == "assistant":
                    for block in entry.get("message", {}).get("content", []):
                        if block.get("type") != "tool_use":
                            continue
                        group["tools_used"].add(block["name"])
                        if block.get("name") != "Bash":
                            continue
                        command = block.get("input", {}).get("command", "")
                        if not isinstance(command, str):
                            self._notice.add(
                                NoticeSource.SESSION,
                                "Unexpected Bash command type",
                                logger=logger,
                                key=key,
                                type=type(command).__name__,
                            )
                            continue
                        if not command:
                            continue
                        cwd = _effective_cwd(command, entry_cwd or project_cwd)
                        if cwd is None:
                            # No leading `cd`: command runs in the entry's recorded cwd, which may itself be outside project
                            cwd = entry_cwd
                        tool_use_id = block.get("id")
                        if tool_use_id:
                            tool_use_cwds[tool_use_id] = cwd
                        if _is_cross_repo(cwd, project_cwd):
                            continue
                        pulls, issues = _extract_pr_issue_refs(command)
                        group["pulls"].update(pulls)
                        group["issues"].update(issues)

        now = datetime.now(UTC).isoformat()
        items = []
        for (date_str, repo, session_id), group in groups.items():
            if not group["user_messages"] and not group["commits"]:
                continue

            timestamps = sorted(group["timestamps"])
            items.append(
                {
                    "date": date_str,
                    "repo#session_id": f"{repo}#{session_id}",
                    "repo": repo,
                    "project": group["project"],
                    "start_time": timestamps[0],
                    "end_time": timestamps[-1],
                    "user_messages": group["user_messages"],
                    "tools_used": sorted(group["tools_used"]),
                    "session_commits": group["commits"],
                    "session_pulls": sorted(group["pulls"]),
                    "session_issues": sorted(group["issues"]),
                    "updated_at": now,
                }
            )

        written_groups = {
            (i["date"], i["repo"], i["repo#session_id"].split("#", 1)[1]) for i in items
        }
        processed_keys = list(
            dict.fromkeys(k for k, gs in key_groups.items() if gs & written_groups)
        )

        return items, processed_keys
