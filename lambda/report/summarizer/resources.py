"""Assets handed to the Claude API: the system prompt and the tool definition.

The tool definition is data with no room for comments, so what its constraints are for sits here:
the tags enum bars the model from emitting values outside the code-defined allowlist,
and the summary item bounds match the item count the system prompt asks for.
"""

import json
import re
from pathlib import Path
from string import Template
from typing import cast

from . import tags

TOOL_NAME = "submit_daily_report"

_ASSET_ROOT = Path(__file__).parent.parent
# A string holding nothing but one placeholder takes the value itself, so a list stays a list instead of being stringified
_WHOLE_PLACEHOLDER = re.compile(r"\$\{?(?P<name>[A-Za-z_][A-Za-z0-9_]*)\}?\Z")
_SHAPE_KEYWORDS = frozenset({"type", "properties", "items", "required"})
_TAG_GUIDANCE = "\n".join(f"- {t.name}: {t.description}" for t in tags.DEFINITIONS)
_VALUES: dict[str, object] = {
    "tool_name": TOOL_NAME,
    "tag_guidance": _TAG_GUIDANCE,
    "allowed_tags": list(tags.ALLOWED_NAMES),
}


def _fill(node: object) -> object:
    """Replace every placeholder in `node`, walking the parsed structure rather than its text.

    Raises:
        KeyError: If a placeholder has no matching value.
    """
    if isinstance(node, dict):
        return {key: _fill(value) for key, value in node.items()}
    if isinstance(node, list):
        return [_fill(item) for item in node]
    if isinstance(node, str):
        whole = _WHOLE_PLACEHOLDER.fullmatch(node)
        if whole:
            return _VALUES[whole.group("name")]
        return Template(node).substitute(_VALUES)
    return node


def _shape_only(schema: dict) -> dict:
    """Strip `schema` down to the structure it describes, dropping what it constrains.

    The response is checked against the result rather than the tool definition itself,
    so a tag outside the allowlist is stripped with a notice instead of failing the whole run.
    """
    shape = {}
    for keyword, value in schema.items():
        if keyword not in _SHAPE_KEYWORDS:
            continue
        if keyword == "properties":
            shape[keyword] = {name: _shape_only(sub) for name, sub in value.items()}
        elif keyword == "items":
            shape[keyword] = _shape_only(value)
        else:
            shape[keyword] = value
    return shape


def _read(*parts: str) -> str:
    return (_ASSET_ROOT.joinpath(*parts)).read_text(encoding="utf-8")


# Template's `$` placeholders rather than `str.format`, so the braces in the prompt's own examples need no escaping
SYSTEM_PROMPT = Template(_read("prompts", "summary_system.txt")).substitute(_VALUES)
TOOL_DEFINITION = cast(dict, _fill(json.loads(_read("schemas", "summary_tool.json"))))
RESPONSE_SHAPE = _shape_only(TOOL_DEFINITION["input_schema"])
