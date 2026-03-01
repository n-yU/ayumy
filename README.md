# ayumy
Traces of daily craft, woven by AI

## Overview
GitHub 上の日次開発アクティビティ（Commit, PR, Issue）と Claude Code のセッションログを自動収集し、Claude API で自然言語の要約を生成して Notion データベースに記録するシステムです。

## Architecture
本システムは2つのフェーズで構成されます。

### Phase 1: Session Log Sync (post-commit hook)
各リポジトリでの `git commit` を契機に、その時点でアクティブな Claude Code セッションの JSONL を `logs` リポジトリへバックグラウンドで自動 push します。

```
git commit
  └─→ post-commit hook
        ├─→ ~/.claude/projects/ からアクティブセッションの JSONL を特定
        └─→ logs リポジトリの claude-sessions/ に commit & push
```

### Phase 2: Daily Summary (GitHub Actions)
毎日 UTC 00:00 に GitHub Actions が起動し、蓄積されたデータから日次レポートを生成します。

```
GitHub Actions (cron: daily)
  ├─→ claude-sessions/ から前日分の JSONL を読み取り
  ├─→ GitHub API: 全 owner リポジトリの Commit / PR / Issue を取得
  ├─→ Claude API: 全データを統合して日次要約を生成
  └─→ Notion API: 要約をデータベースページとして作成
```

## Tech Stack
| 技術 | 用途 |
|---|---|
| Python 3.12 | メインスクリプト |
| GitHub API (REST) | 開発アクティビティの取得 |
| Anthropic API | 自然言語による要約生成 |
| Notion API | 作業記録の書き込み |
| GitHub Actions | 日次ワークフローの実行 |

## Setup
詳細なセットアップ手順は [Spec.md](./Spec.md) の §8 を参照してください。

### Required Secrets (GitHub Actions)
| Secret | 説明 |
|---|---|
| `MY_GITHUB_PAT` | GitHub Fine-grained PAT |
| `ANTHROPIC_API_KEY` | Anthropic API キー |
| `NOTION_TOKEN` | Notion Internal Integration トークン |
| `NOTION_DATABASE_ID` | Notion データベース ID |

### Local Environment
post-commit hook の利用には環境変数 `LOGS_REPO_PATH`（`logs` リポジトリのローカルパス）の設定が必要です。
