"""Claude API summary generator."""

import json
import logging
from datetime import datetime

import anthropic

from . import JST, ReportSummary

logger = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-20250514"
MAX_TOKENS = 2048

_SYSTEM_PROMPT_TEMPLATE = """\
あなたは開発者の日次アクティビティを要約するアシスタントです。
与えられた GitHub アクティビティと Claude Code セッションログをもとに、
日本語で構造化された要約を生成してください。

文体は常体（である調・体言止め可）で統一し、ですます調は使わないでください。
各リポジトリの summary でこの規則を守ってください。

以下の JSON 形式で出力してください。JSON 以外のテキストは含めないでください。

{{
  "repositories": [
    {{
      "name": "リポジトリ名",
      "summary": [
        "このリポジトリでの作業要点を1項目ずつ簡潔に記述",
        "最重要の要点を最初の項目に置く",
        "2〜5項目の範囲で記述する"
      ],
      "tags": ["該当する分類タグ"]
    }}
  ]
}}

各リポジトリの summary は各項目を1文程度の短い箇条書きとし、2〜5項目で記述してください。
summary の最初の項目はそのリポジトリの最重要の要点として単独でも通じるものにしてください。
Claude Code セッションでの作業内容（相談・実装方針の検討など）も summary に含めて構いません。
PR/Issue の状態別一覧や時系列のイベントは別途プログラムで生成するため、summary では作業の意図や論点を中心に記述してください。

Claude Code セッションのプロジェクト名は GitHub リポジトリ名と対応させてください。
プロジェクト名からリポジトリを特定できない場合は、name を "unknown ({{プロジェクト名}})" としてください。
無理に推測して既存のリポジトリに紐づけないでください。

tags は以下から該当するものをリポジトリごとに選択:
{tags}
"""


class ValidationResult:
    """Result of validating a report against allowlists."""

    def __init__(self) -> None:
        self.invalid_tags: dict[str, list[str]] = {}

    def __bool__(self) -> bool:
        return bool(self.invalid_tags)


class SummaryClient:
    """Client for generating daily report summaries via Claude API."""

    def __init__(self, api_key: str, allowed_tags: list[str]) -> None:
        """Initialize the client with an Anthropic API key and tag allowlist.

        Args:
            api_key: Anthropic API key
            allowed_tags: Allowed tag names from Notion DB. Held for the
                lifetime of the client; not refreshed mid-run
        """
        self.client = anthropic.Anthropic(api_key=api_key)
        self.allowed_tags = allowed_tags

    def _build_system_prompt(self) -> str:
        """Build the system prompt with dynamic tag list."""
        tags = "\n".join(f"- {tag}" for tag in self.allowed_tags)
        return _SYSTEM_PROMPT_TEMPLATE.format(tags=tags)

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
        """Generate a structured summary using Claude API.

        Returns:
            A ReportSummary dict with summary and per-repository details

        Raises:
            ValueError: If Claude API response cannot be parsed as JSON
        """
        prompt = self.build_prompt(target_date, formatted_github, formatted_sessions)
        system_prompt = self._build_system_prompt()

        message = self.client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=system_prompt,
            messages=[{"role": "user", "content": prompt}],
        )

        if not message.content:
            raise ValueError("Claude API response has no content blocks.")

        response_text = message.content[0].text.strip()
        # Strip markdown code fences if present
        if response_text.startswith("```"):
            response_text = response_text.split("\n", 1)[1]
            response_text = response_text.rsplit("```", 1)[0].strip()
        try:
            return json.loads(response_text)
        except json.JSONDecodeError as e:
            raise ValueError(
                f"Failed to parse Claude API response as JSON: {e}\n"
                f"Response (truncated): {response_text[:500]}"
            ) from e

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
        tag_set = set(self.allowed_tags)

        for repo in report["repositories"]:
            name = repo["name"]

            invalid = [t for t in repo["tags"] if t not in tag_set]
            if invalid:
                result.invalid_tags[name] = invalid
                repo["tags"] = [t for t in repo["tags"] if t in tag_set]
                logger.warning("Removed invalid tags for %s: %s", name, invalid)

        return result
