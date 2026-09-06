"""Claude API summary generator."""

import logging
from datetime import datetime
from pathlib import Path
from string import Template
from typing import cast

import anthropic
import jsonschema

from config import CONFIG

from ..domain.summary import ReportSummary, SummaryUsage
from ..shared.dates import JST
from ..shared.notice import Notice, NoticeSource
from .tags import ALLOWED_TAG_NAMES, TAG_DEFINITIONS

logger = logging.getLogger(__name__)

TOOL_NAME = "submit_daily_report"
_TAG_GUIDANCE = "\n".join(f"- {t.name}: {t.description}" for t in TAG_DEFINITIONS)
# Template's `$` placeholders rather than `str.format`, so the braces in the prompt's own examples need no escaping
_SYSTEM_PROMPT = Template(
    (Path(__file__).parent.parent / "prompts" / "summary_system.txt").read_text(
        encoding="utf-8"
    )
).substitute(tool_name=TOOL_NAME, tag_guidance=_TAG_GUIDANCE)
_RESPONSE_SHAPE_SCHEMA = {
    "type": "object",
    "required": ["repositories"],
    "properties": {
        "repositories": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["name", "summary", "tags"],
                "properties": {
                    "name": {"type": "string"},
                    "summary": {"type": "array", "items": {"type": "string"}},
                    "tags": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
    },
}
_VALUE_REPR_LIMIT = 200


def _validate_response_shape(payload: object) -> ReportSummary:
    try:
        jsonschema.validate(payload, _RESPONSE_SHAPE_SCHEMA)
    except jsonschema.ValidationError as e:
        path = "/".join(str(p) for p in e.absolute_path) or "<root>"
        value_repr = repr(e.instance)
        if len(value_repr) > _VALUE_REPR_LIMIT:
            value_repr = value_repr[:_VALUE_REPR_LIMIT] + "..."
        summary_line = (
            f"Claude API response shape invalid: "
            f"path={path} validator={e.validator} expected={e.validator_value!r} "
            f"got={type(e.instance).__name__} value={value_repr}"
        )
        raise ValueError(f"{summary_line}\n{e}") from e
    return cast(ReportSummary, payload)


class ValidationResult:
    """Result of validating a report against allowlists."""

    def __init__(self) -> None:
        self.invalid_tags: dict[str, list[str]] = {}

    def __bool__(self) -> bool:
        return bool(self.invalid_tags)


class SummaryClient:
    """Client for generating daily report summaries via Claude API."""

    def __init__(self, api_key: str, notice: Notice | None = None) -> None:
        self.client = anthropic.Anthropic(api_key=api_key)
        self._notice = notice or Notice()

    def _build_tool_schema(self) -> dict:
        """Build the tool definition the model fills in; the `tags` enum bars it from emitting values outside the code-defined allowlist."""
        return {
            "name": TOOL_NAME,
            "description": "日次の開発アクティビティ要約を構造化された形で提出する",
            "input_schema": {
                "type": "object",
                "properties": {
                    "repositories": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "summary": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "minItems": 2,
                                    "maxItems": 5,
                                },
                                "tags": {
                                    "type": "array",
                                    "description": (
                                        "その日の作業内容を表す tag のリスト。"
                                        "enum で指定された値のみ使用可:\n"
                                        f"{_TAG_GUIDANCE}"
                                    ),
                                    "items": {
                                        "type": "string",
                                        "enum": list(ALLOWED_TAG_NAMES),
                                    },
                                },
                            },
                            "required": ["name", "summary", "tags"],
                        },
                    },
                },
                "required": ["repositories"],
            },
        }

    def build_prompt(
        self,
        target_date: datetime,
        formatted_github: str,
        formatted_sessions: str,
    ) -> str:
        date_str = target_date.astimezone(JST).strftime("%Y-%m-%d")
        return (
            f"以下は {date_str} の GitHub アクティビティおよび"
            f" Claude Code での作業記録です。\n"
            f"日本語で簡潔に要約してください。\n\n"
            f"---\n{formatted_github}\n\n"
            f"---\n{formatted_sessions}"
        )

    def generate_summary(
        self,
        target_date: datetime,
        formatted_github: str,
        formatted_sessions: str,
    ) -> tuple[ReportSummary, SummaryUsage]:
        """Generate the structured summary via Claude API tool use.

        Returns the parsed report and the token / spend record for the API call.

        Raises:
            ValueError: If the response contains no tool_use block for the expected tool,
                or if the tool_use input violates the expected shape.
        """
        prompt = self.build_prompt(target_date, formatted_github, formatted_sessions)
        tool = self._build_tool_schema()

        message = self.client.messages.create(
            model=CONFIG.claude.model,
            max_tokens=CONFIG.claude.max_tokens,
            system=_SYSTEM_PROMPT,
            tools=[tool],
            tool_choice={"type": "tool", "name": TOOL_NAME},
            messages=[{"role": "user", "content": prompt}],
        )

        usage = SummaryUsage.from_call(
            message.usage.input_tokens, message.usage.output_tokens
        )

        for block in message.content:
            if getattr(block, "type", None) == "tool_use" and block.name == TOOL_NAME:
                return _validate_response_shape(block.input), usage

        raise ValueError(f"Claude API response missing tool_use block for {TOOL_NAME}.")

    def validate_report(self, report: ReportSummary) -> ValidationResult:
        """Strips disallowed tags from `report` in place."""
        result = ValidationResult()
        tag_set = set(ALLOWED_TAG_NAMES)

        for repo in report["repositories"]:
            name = repo["name"]

            invalid = [t for t in repo["tags"] if t not in tag_set]
            if invalid:
                result.invalid_tags[name] = invalid
                repo["tags"] = [t for t in repo["tags"] if t in tag_set]
                self._notice.add(
                    NoticeSource.SUMMARY,
                    "Invalid tags removed",
                    logger=logger,
                    repo=name,
                    tags=",".join(invalid),
                )

        return result
