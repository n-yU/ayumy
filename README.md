# ayumy
Traces of daily craft, woven by AI

## Overview
GitHub 上の日次開発アクティビティ（Commit, PR, Issue）と Claude Code のセッションログを自動収集し、Claude API で自然言語の要約を生成して Notion データベースに記録するシステムです。

## Architecture
常時稼働マシン（Raspberry Pi 等）を使用した2フェーズ構成です。セッションログの生データは LAN 内にのみ保持し、リモートリポジトリには push しません。

### Phase 1: Session Log Transfer
`git commit` 時に post-commit hook が発火し、アクティブな Claude Code セッションの JSONL を常時稼働マシンに rsync で転送します。commit せずに作業を中断する場合は `ayumy sync` で手動転送できます。

```
開発マシン
  git commit → post-commit hook ─┐
  ayumy sync（手動）─────────────┤
                                  ▼
                         rsync over SSH
                                  ▼
常時稼働マシン
  ~/ayumy-data/claude-sessions/ に JSONL 蓄積
```

### Phase 2: Daily Summary (cron)
常時稼働マシン上の cron が毎日 UTC 00:00 に起動し、蓄積されたデータから日次レポートを生成します。

```
cron (daily)
  ├─→ JSONL + GitHub API からデータ収集
  ├─→ Claude API で日次要約を生成
  ├─→ Notion API でデータベースに記録
  └─→ 処理済み JSONL を削除
```

## Tech Stack
| 技術 | 用途 |
|---|---|
| Python 3.12 | メインスクリプト |
| GitHub API (REST) | 開発アクティビティの取得 |
| Anthropic API | 自然言語による要約生成 |
| Notion API | 作業記録の書き込み |

## Setup
詳細なセットアップ手順は [Spec.md](./docs/Spec.md) の §8 を参照してください。

### Environment Variables (Server)
常時稼働マシンの `~/.ayumy.env` に設定:

| 変数 | 説明 |
|---|---|
| `GITHUB_PAT` | GitHub Fine-grained PAT |
| `ANTHROPIC_API_KEY` | Anthropic API キー |
| `NOTION_TOKEN` | Notion Internal Integration トークン |
| `NOTION_DATABASE_ID` | Notion データベース ID |

### Environment Variables (Dev Machine)
| 変数 | 説明 |
|---|---|
| `AYUMY_HOST` | 常時稼働マシンの SSH ホスト名（例: `pi@raspberrypi.local`） |
| `AYUMY_DATA_DIR` | データディレクトリのパス（デフォルト: `~/ayumy-data`） |
