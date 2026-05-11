"""DynamoDB session store for session metadata."""

import json
import logging
import re
import shlex
from collections import defaultdict
from datetime import date, datetime, timezone
from os.path import normpath
from pathlib import Path, PurePosixPath

import boto3
from boto3.dynamodb.conditions import Key

from . import JST, SessionActivity, SessionInfo

logger = logging.getLogger(__name__)

# `gh pr|issue` invocation. `gh pr create`, `gh pr list`, `gh pr status`
# (and the issue equivalents) do not take a number positional argument
_GH_CLI_RE = re.compile(r"\bgh\s+(pr|issue)\s+(\w[\w-]*)")
_GH_CLI_NO_NUMBER_SUBS = {"create", "list", "status"}
# `gh api` invocation anchor; the path is scanned in the segment that follows
_GH_API_RE = re.compile(r"\bgh\s+api\b")
# PR/Issue number embedded in a `gh api` REST path
_API_PATH_PR_RE = re.compile(r"\b(?:pulls|pull)/(\d+)\b")
_API_PATH_ISSUE_RE = re.compile(r"\bissues/(\d+)\b")
# `#N` reference inside `git` command arguments. Treated as ambiguous
# between PR and Issue
_HASH_REF_RE = re.compile(r"(?<![A-Za-z0-9])#(\d+)\b")
# `git` invocation anchor
_GIT_CLI_RE = re.compile(r"\bgit\s+\w[\w-]*")
# Stop characters that delimit a single shell command within a Bash line
_SHELL_STOPS = ("\n", "&&", "||", ";", "|")


def _command_segment(command: str, start: int) -> str:
    """Return the portion of `command` from `start` up to the next shell stop."""
    end = len(command)
    for stop in _SHELL_STOPS:
        i = command.find(stop, start)
        if i != -1 and i < end:
            end = i
    return command[start:end]


def _first_positional_int(segment: str) -> int | None:
    """Return the first positional integer token in `segment`.

    Tokenizes via shlex so that quoted flag values count as a single
    token; this prevents matching integers that live inside strings
    like `--body "fix 999"`. Tokens beginning with `-` are treated as
    flags and skipped
    """
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
    """Expand a leading `~` using project_cwd's home as the anchor.

    Lambda's runtime user differs from the session author, so
    Path.expanduser would resolve to the wrong home.
    """
    if not path.startswith("~"):
        return path
    if project_cwd:
        parts = PurePosixPath(project_cwd).parts
        if len(parts) >= 3 and parts[0] == "/" and parts[1] in ("Users", "home"):
            home = PurePosixPath("/", parts[1], parts[2])
            return str(home) + path[1:]
    return str(Path(path).expanduser())


def _effective_cwd(command: str, project_cwd: str | None) -> str | None:
    """Return the effective cwd of a Bash command, or None for project cwd.

    Only the leading `cd <path> && ...` form is recognized; subshells,
    pushd, `cd -`, and other variants are treated as project cwd.
    """
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return None
    if len(tokens) < 3 or tokens[0] != "cd" or tokens[2] != "&&":
        return None
    if tokens[1] == "-":
        return None
    target = _expand_home(tokens[1], project_cwd)
    p = PurePosixPath(target)
    if not p.is_absolute() and project_cwd:
        p = PurePosixPath(project_cwd) / p
    # normpath collapses `..` / `.` segments without touching the filesystem
    return normpath(str(p))


def _is_cross_repo(effective_cwd: str | None, project_cwd: str | None) -> bool:
    """Return True when the command runs outside the project working directory."""
    if not project_cwd or effective_cwd is None:
        return False
    cwd = PurePosixPath(effective_cwd)
    root = PurePosixPath(project_cwd)
    return cwd != root and not cwd.is_relative_to(root)


def _extract_pr_issue_refs(command: str) -> tuple[set[int], set[int]]:
    """Extract PR and Issue numbers from a Bash command string.

    Recognizes `gh pr|issue {sub} {N}`, `gh api .../pulls|issues/{N}`,
    and `#N` inside `git` arguments. `#N` is ambiguous, so it is
    placed in both sets and the fetcher reconciles via 404 / the
    `pull_request` attribute

    Returns:
        A tuple of (pull numbers, issue numbers)
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


class SessionStore:
    """Client for reading and writing session metadata in DynamoDB."""

    def __init__(self, table_name: str) -> None:
        """Initialize the store with a DynamoDB table name.

        Args:
            table_name: DynamoDB table name for session metadata
        """
        self.table = boto3.resource("dynamodb").Table(table_name)

    def ingest(self, session_client) -> list[str]:
        """Parse all JSONL files and write session items to DynamoDB.

        Downloads every unarchived JSONL from S3, groups entries by
        (JST date, repo, session_id), and writes the resulting items
        to DynamoDB. Specified fields are updated while preserving
        existing attributes such as reported_at.

        Args:
            session_client: SessionClient instance for S3 access

        Returns:
            S3 keys that were processed
        """
        items, keys = self._build_items(session_client)
        self._write_items(items)
        return keys

    def _build_items(self, session_client) -> tuple[list[dict], list[str]]:
        """Parse JSONL files and build DynamoDB items.

        Groups all entries by (JST date, repo, session_id) without
        date filtering. Skips entries without timestamps and projects
        without a .ayumy_repo metadata file.

        Args:
            session_client: SessionClient instance for S3 access

        Returns:
            A tuple of (DynamoDB items, S3 keys processed)
        """
        # Pattern: [... short-sha] commit message
        # Handles normal, root-commit, and detached HEAD forms
        # MULTILINE allows matching after hook output preceding the summary line
        commit_pattern = re.compile(r"^\[.+\s+([0-9a-f]+)\]\s+(.+)", re.MULTILINE)

        # (date, repo, session_id) -> accumulated entry data
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
        # Track which S3 keys contributed to each group
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
                Bucket=session_client.bucket, Key=key,
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
                    logger.warning("Skipping malformed line in %s", key)
                    continue

                if project_cwd is None:
                    entry_cwd = entry.get("cwd")
                    if isinstance(entry_cwd, str) and entry_cwd:
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
                            if block.get("type") != "tool_result" or block.get("is_error"):
                                continue
                            tool_use_id = block.get("tool_use_id")
                            cwd = tool_use_cwds.get(tool_use_id) if tool_use_id else None
                            if _is_cross_repo(cwd, project_cwd):
                                continue
                            text = block.get("content", "")
                            if isinstance(text, str):
                                for sha, msg in commit_pattern.findall(text):
                                    group["commits"].append({
                                        "sha": sha,
                                        "message": msg,
                                        "timestamp": timestamp,
                                    })
                elif entry_type == "assistant":
                    for block in entry.get("message", {}).get("content", []):
                        if block.get("type") != "tool_use":
                            continue
                        group["tools_used"].add(block["name"])
                        if block.get("name") != "Bash":
                            continue
                        command = block.get("input", {}).get("command", "")
                        if not isinstance(command, str) or not command:
                            continue
                        cwd = _effective_cwd(command, project_cwd)
                        tool_use_id = block.get("id")
                        if tool_use_id:
                            tool_use_cwds[tool_use_id] = cwd
                        if _is_cross_repo(cwd, project_cwd):
                            continue
                        pulls, issues = _extract_pr_issue_refs(command)
                        group["pulls"].update(pulls)
                        group["issues"].update(issues)

        now = datetime.now(timezone.utc).isoformat()
        items = []
        for (date_str, repo, session_id), group in groups.items():
            if not group["user_messages"] and not group["commits"]:
                continue

            timestamps = sorted(group["timestamps"])
            items.append({
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
            })

        # Only return keys that produced at least one DynamoDB item
        written_groups = {(i["date"], i["repo"], i["repo#session_id"].split("#", 1)[1]) for i in items}
        processed_keys = list(dict.fromkeys(
            k for k, gs in key_groups.items() if gs & written_groups
        ))

        return items, processed_keys

    def _write_items(self, items: list[dict]) -> None:
        """Write items to DynamoDB using update_item.

        Uses SET with attribute assignments so that existing
        reported_at values are preserved across re-ingestion.

        Args:
            items: List of DynamoDB item dicts
        """
        for item in items:
            key = {
                "date": item["date"],
                "repo#session_id": item["repo#session_id"],
            }
            fields = {k: v for k, v in item.items() if k not in key}
            update_expr = "SET " + ", ".join(
                f"#f_{k} = :v_{k}" for k in fields
            )
            self.table.update_item(
                Key=key,
                UpdateExpression=update_expr,
                ExpressionAttributeNames={f"#f_{k}": k for k in fields},
                ExpressionAttributeValues={f":v_{k}": v for k, v in fields.items()},
            )

    def fetch_sessions(self, date_str: str) -> SessionActivity:
        """Query sessions for a specific date from DynamoDB.

        Args:
            date_str: JST date string (YYYY-MM-DD)

        Returns:
            A SessionActivity instance keyed by repository name
        """
        items = []
        response = self.table.query(
            KeyConditionExpression=Key("date").eq(date_str),
        )
        items.extend(response["Items"])

        while "LastEvaluatedKey" in response:
            response = self.table.query(
                KeyConditionExpression=Key("date").eq(date_str),
                ExclusiveStartKey=response["LastEvaluatedKey"],
            )
            items.extend(response["Items"])

        data: dict[str, list[SessionInfo]] = {}
        for item in items:
            repo = item["repo"]
            session_id = item["repo#session_id"].split("#", 1)[1]
            session_info: SessionInfo = {
                "session_id": session_id,
                "project": item["project"],
                "start_time": item["start_time"],
                "end_time": item["end_time"],
                "user_messages": item["user_messages"],
                "tools_used": item["tools_used"],
                "session_commits": item.get("session_commits", []),
                "session_pulls": [int(n) for n in item.get("session_pulls", [])],
                "session_issues": [int(n) for n in item.get("session_issues", [])],
            }
            data.setdefault(repo, []).append(session_info)

        for sessions in data.values():
            sessions.sort(key=lambda s: s["start_time"])

        return SessionActivity(data)

    def scan_backfill_dates(self, primary_date: date) -> list[date]:
        """Scan DynamoDB for past dates needing report generation.

        Finds dates where reported_at is not set (new sessions) or
        updated_at > reported_at (updated sessions after reporting).

        Args:
            primary_date: The current primary date to exclude

        Returns:
            Sorted list of past dates needing (re-)generation
        """
        # Attr-to-attr comparison requires raw expression string
        scan_kwargs = {
            "FilterExpression": "attribute_not_exists(reported_at) OR updated_at > reported_at",
            "ProjectionExpression": "#d",
            "ExpressionAttributeNames": {"#d": "date"},
        }

        dates: set[date] = set()
        response = self.table.scan(**scan_kwargs)
        for item in response["Items"]:
            dates.add(date.fromisoformat(item["date"]))

        while "LastEvaluatedKey" in response:
            response = self.table.scan(
                **scan_kwargs,
                ExclusiveStartKey=response["LastEvaluatedKey"],
            )
            for item in response["Items"]:
                dates.add(date.fromisoformat(item["date"]))

        return sorted(d for d in dates if d < primary_date)

    def mark_reported(self, date_str: str) -> None:
        """Set reported_at on all items for the given date.

        Args:
            date_str: JST date string (YYYY-MM-DD)
        """
        now = datetime.now(timezone.utc).isoformat()

        response = self.table.query(
            KeyConditionExpression=Key("date").eq(date_str),
            ProjectionExpression="#d, #sk",
            ExpressionAttributeNames={
                "#d": "date",
                "#sk": "repo#session_id",
            },
        )
        items = response["Items"]

        while "LastEvaluatedKey" in response:
            response = self.table.query(
                KeyConditionExpression=Key("date").eq(date_str),
                ProjectionExpression="#d, #sk",
                ExpressionAttributeNames={
                    "#d": "date",
                    "#sk": "repo#session_id",
                },
                ExclusiveStartKey=response["LastEvaluatedKey"],
            )
            items.extend(response["Items"])

        for item in items:
            self.table.update_item(
                Key={
                    "date": item["date"],
                    "repo#session_id": item["repo#session_id"],
                },
                UpdateExpression="SET reported_at = :ts",
                ExpressionAttributeValues={":ts": now},
            )
