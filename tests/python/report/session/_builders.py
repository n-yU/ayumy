"""Builders for session log entries used by tests/report/session/."""

import json

SESSION_KEY = "claude-sessions/proj/s1.jsonl"


def user(timestamp, content, *, cwd=None):
    """Build a user entry whose content is either a plain message or a list of blocks."""
    return _entry("user", timestamp, content, cwd)


def assistant(timestamp, *blocks, cwd=None):
    """Build an assistant entry carrying the given content blocks."""
    return _entry("assistant", timestamp, list(blocks), cwd)


def tool_use_block(name, *, tool_use_id=None, **tool_input):
    """Build a tool_use block, omitting the id and input keys the parser treats as optional."""
    block = {"type": "tool_use", "name": name}
    if tool_use_id is not None:
        block["id"] = tool_use_id
    if tool_input:
        block["input"] = tool_input
    return block


def tool_result(timestamp, content, *, tool_use_id="tu", is_error=False, cwd=None):
    """Build a user entry wrapping one tool_result block, the shape command output arrives in."""
    return user(
        timestamp,
        [
            {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": content,
                "is_error": is_error,
            }
        ],
        cwd=cwd,
    )


def bash(timestamp, command, *, tool_use_id=None, cwd=None):
    """Build an assistant entry wrapping one Bash tool_use block."""
    return assistant(
        timestamp,
        tool_use_block("Bash", tool_use_id=tool_use_id, command=command),
        cwd=cwd,
    )


def jsonl(*entries):
    """Serialize entries into the newline-delimited JSON body stored on S3."""
    return "\n".join(json.dumps(e) for e in entries)


def _entry(entry_type, timestamp, content, cwd):
    entry = {
        "type": entry_type,
        "timestamp": timestamp,
        "message": {"content": content},
    }
    if cwd is not None:
        entry["cwd"] = cwd
    return entry
