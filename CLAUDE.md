# CLAUDE.md
このファイルは Claude Code (claude.ai/code) がこのリポジトリで作業する際のガイドラインを提供する。

## プロジェクト概要
**ayumy** — GitHub 上の日次開発アクティビティ（Commit, PR, Issue）と Claude Code セッションログを自動収集し、Claude API で自然言語の要約を生成して Notion データベースに記録するシステム。

## アーキテクチャ
2フェーズ構成:

1. **フェーズ 1（post-commit hook）**: 各リポジトリでの commit を契機に、`~/.claude/projects/` からアクティブな Claude Code セッションの JSONL を `logs` リポジトリの `claude-sessions/` にバックグラウンドで push する。
2. **フェーズ 2（GitHub Actions 日次 cron）**: 蓄積されたセッションログの読み取りと GitHub API によるアクティビティ取得を行い、Claude API で要約を生成して Notion に書き込む。

## リポジトリ構成
```
.github/workflows/daily_report.yml   # GitHub Actions ワークフロー（毎日 UTC 00:00）
scripts/daily_report.py              # メインスクリプト: GitHub API + Claude API + Notion API
scripts/push_active_session.sh       # post-commit hook から呼ばれるセッション同期スクリプト
hooks/post-commit                    # Git hook（各リポジトリにシンボリックリンクで配置）
claude-sessions/{project}/{id}.jsonl # 蓄積された Claude Code セッションデータ
```

## 技術詳細
- **言語**: Python 3.12、依存: `requests`, `anthropic`
- **Claude モデル**: 要約生成に `claude-sonnet-4-20250514` を使用
- **GitHub API**: REST、Fine-grained PAT、`affiliation=owner` で自分の所有リポジトリのみ対象
- **Notion API**: Internal Integration Token、データベースプロパティは Spec.md §6 に定義
- **Hook 設計**: 必ず `exit 0` を返す（commit をブロックしない）、push はバックグラウンド実行、セッション ID 単位の上書きで冪等性を担保

## 環境変数 / Secrets
GitHub Actions（`logs` リポジトリの Secrets）:
- `MY_GITHUB_PAT` — GitHub Fine-grained PAT（全 owner リポジトリへの read 権限）
- `ANTHROPIC_API_KEY` — Anthropic API キー
- `NOTION_TOKEN` — Notion Internal Integration トークン
- `NOTION_DATABASE_ID` — 書き込み先の Notion データベース ID

ローカル（post-commit hook 用）:
- `LOGS_REPO_PATH` — `logs` リポジトリのローカルパス

## 書式規約
- **Markdown の見出し**: 見出しの直後に空行を入れない（見出しの前には空行を入れる）

## 言語規約
- **コミットメッセージ**: 英語で書く
- **コード内コメント**: 英語で書く
- **GitHub Issue・PR**: タイトルは英語、本文は日本語で書く
- **ドキュメント**（`docs/` 以下の md ファイル）: タイトルは英語で書く

## 開発メモ
- 仕様書は `docs/Spec.md`（日本語）— すべての要件の原典
- `logs` リポジトリは JSONL に会話の生データが含まれるため必ず **private** にすること
- アクティビティの取得対象期間: 前日 UTC 00:00:00 〜 当日 UTC 00:00:00
- アクティビティが 0 件の日はスキップまたは「活動なし」と記録
