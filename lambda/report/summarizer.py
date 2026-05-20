"""Claude API summary generator."""

import logging
from datetime import datetime

import anthropic

from . import JST, ReportSummary
from .tags import ALLOWED_TAG_NAMES, TAG_DEFINITIONS

logger = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-20250514"
MAX_TOKENS = 2048
TOOL_NAME = "submit_daily_report"

_TAG_GUIDANCE = "\n".join(f"- {t.name}: {t.description}" for t in TAG_DEFINITIONS)

_SYSTEM_PROMPT = f"""\
あなたは開発者の日次アクティビティを要約するアシスタントです。
与えられた GitHub アクティビティと Claude Code セッションログをもとに、
日本語で構造化された要約を生成し、{TOOL_NAME} ツールに渡してください。

文体は常体（である調・体言止め可）で統一し、ですます調は使わないでください。
各リポジトリの summary でこの規則を守ってください。

summary は各項目を1文程度の短い箇条書きとし、2〜5項目で記述してください。
最初の項目はそのリポジトリの最重要の要点として単独でも通じるものにしてください。
Claude Code セッションでの作業内容（相談・実装方針の検討など）も summary に含めて構いません。
PR/Issue の状態別一覧や時系列のイベントは別途プログラムで生成するため、summary では作業の意図や論点を中心に記述してください。

Claude Code セッションのプロジェクト名は GitHub リポジトリ名と対応させてください。
プロジェクト名からリポジトリを特定できない場合は、name を "unknown ({{プロジェクト名}})" としてください。
無理に推測して既存のリポジトリに紐づけないでください。

tags にはその日の作業内容を表す値を以下から選んでください:
{_TAG_GUIDANCE}
GitHub の Issue/PR ラベル（enhancement など）に引きずられず、必ず上記のいずれかを使用してください。当てはまるものがない場合は other を使用してください。
"""


class ValidationResult:
    """Result of validating a report against allowlists."""

    def __init__(self) -> None:
        self.invalid_tags: dict[str, list[str]] = {}

    def __bool__(self) -> bool:
        return bool(self.invalid_tags)


class SummaryClient:
    """Client for generating daily report summaries via Claude API."""

    def __init__(self, api_key: str) -> None:
        """Initialize the client with an Anthropic API key.

        Args:
            api_key: Anthropic API key
        """
        self.client = anthropic.Anthropic(api_key=api_key)

    def _build_tool_schema(self) -> dict:
        """Build the tool definition with tag allowlist enforced via enum.

        The enum constraint on tags[] makes the model unable to emit values
        outside the code-defined allowlist.
        """
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
        """Build the user prompt from formatted activity data.

        Args:
            target_date: The target date for the report
            formatted_github: Formatted GitHub activity text
            formatted_sessions: Formatted Claude Code session text

        Returns:
            A prompt string following Spec.md §5.3 input format
        """
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
    ) -> ReportSummary:
        """Generate a structured summary using Claude API tool use.

        Returns:
            A ReportSummary dict with summary and per-repository details

        Raises:
            ValueError: If the response contains no tool_use block
        """
        prompt = self.build_prompt(target_date, formatted_github, formatted_sessions)
        tool = self._build_tool_schema()

        message = self.client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=_SYSTEM_PROMPT,
            tools=[tool],
            tool_choice={"type": "tool", "name": TOOL_NAME},
            messages=[{"role": "user", "content": prompt}],
        )

        for block in message.content:
            if getattr(block, "type", None) == "tool_use" and block.name == TOOL_NAME:
                return block.input  # type: ignore[return-value]

        raise ValueError(f"Claude API response missing tool_use block for {TOOL_NAME}.")

    def validate_report(self, report: ReportSummary) -> ValidationResult:
        """Validate and fix tags in a report against the allowlist.

        Removes invalid tags from each repository. Mutates the report
        in place.

        Args:
            report: Report to validate (mutated in place)

        Returns:
            A ValidationResult with any invalid values found
        """
        result = ValidationResult()
        tag_set = set(ALLOWED_TAG_NAMES)

        for repo in report["repositories"]:
            name = repo["name"]

            invalid = [t for t in repo["tags"] if t not in tag_set]
            if invalid:
                result.invalid_tags[name] = invalid
                repo["tags"] = [t for t in repo["tags"] if t in tag_set]
                logger.warning("Removed invalid tags for %s: %s", name, invalid)

        return result
