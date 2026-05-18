"""Tag definitions for daily reports.

Tags classify the kind of work performed on a given day. Definitions
live in code rather than Notion because Notion's multi-select option
lacks a description field, and the descriptions feed both the system
prompt and the Claude API tool schema for tag selection guidance.
"""

from typing import NamedTuple


class TagDefinition(NamedTuple):
    name: str
    description: str


TAG_DEFINITIONS: tuple[TagDefinition, ...] = (
    TagDefinition("feature", "新機能の追加や既存機能の拡張"),
    TagDefinition("bugfix", "バグ修正・障害対応"),
    TagDefinition("docs", "ドキュメント・README・Spec の整備"),
    TagDefinition(
        "refactor",
        "内部構造の改善、依存更新、設定整理を含む保守的変更",
    ),
    TagDefinition("ci", "CI/CD・ビルド・デプロイ・テスト基盤の整備"),
    TagDefinition(
        "review",
        "PR レビュー対応、Copilot review のフィードバック反映",
    ),
    TagDefinition(
        "other",
        "既存タグのいずれにも当てはまらない作業（割り当てが多い日が増えた場合、"
        "新規 tag 追加を検討するシグナル）",
    ),
)

ALLOWED_TAG_NAMES: tuple[str, ...] = tuple(t.name for t in TAG_DEFINITIONS)
