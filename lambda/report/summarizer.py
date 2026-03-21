"""Claude API summary generator."""

import json
from datetime import datetime

import anthropic

from . import JST, ReportSummary

MODEL = "claude-sonnet-4-20250514"
MAX_TOKENS = 2048

SYSTEM_PROMPT = """\
あなたは開発者の日次アクティビティを要約するアシスタントです。
与えられた GitHub アクティビティと Claude Code セッションログをもとに、
日本語で構造化された要約を生成してください。

以下の JSON 形式で出力してください。JSON 以外のテキストは含めないでください。

{
  "summary": "その日の作業全体を2〜3文で要約",
  "repositories": [
    {
      "name": "リポジトリ名",
      "summary": "このリポジトリでの作業概要",
      "achievements": ["マージされた PR、クローズされた Issue 等の成果"],
      "ongoing": ["オープンな PR や Issue 等の継続中の作業"],
      "claude_code": "Claude Code での作業概要（セッションなしの場合は空文字列）",
      "tags": ["該当する分類タグ"],
      "status": "このリポジトリでの進捗状態"
    }
  ]
}

Claude Code セッションのプロジェクト名は GitHub リポジトリ名と対応させてください。
プロジェクト名からリポジトリを特定できない場合は、name を "unknown ({プロジェクト名})" としてください。
無理に推測して既存のリポジトリに紐づけないでください。

tags は以下から該当するものをリポジトリごとに選択:
- feature: 新機能追加に関する Commit / PR
- bugfix: バグ修正に関する Commit / PR / Issue
- docs: ドキュメント更新
- refactor: リファクタリング
- ci: CI/CD やビルド設定の変更
- review: PR レビューが主な活動だった場合
- ai-assisted: Claude Code を活用した作業が含まれる場合

status は以下からリポジトリごとに1つ選択:
- productive: 複数の PR マージや Issue クローズがある
- maintenance: 依存関係更新、CI 修正など保守作業が中心
- blocked: PR レビュー待ちや Issue の議論が中心
- light: アクティビティが少ない日
"""


class SummaryClient:
    """Client for generating daily report summaries via Claude API."""

    def __init__(self, api_key: str) -> None:
        """Initialize the client with an Anthropic API key.

        Args:
            api_key: Anthropic API key
        """
        self.client = anthropic.Anthropic(api_key=api_key)

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

        Args:
            target_date: The target date for the report
            formatted_github: Formatted GitHub activity text
            formatted_sessions: Formatted Claude Code session text

        Returns:
            A ReportSummary dict with summary and per-repository details

        Raises:
            ValueError: If Claude API response cannot be parsed as JSON
        """
        prompt = self.build_prompt(target_date, formatted_github, formatted_sessions)

        message = self.client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )

        response_text = message.content[0].text
        try:
            return json.loads(response_text)
        except json.JSONDecodeError as e:
            raise ValueError(
                f"Failed to parse Claude API response as JSON: {e}\n"
                f"Response: {response_text}"
            ) from e
