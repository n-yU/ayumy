# ayumy
Traces of daily craft, woven by AI

GitHub 上の日次開発アクティビティ（Commit, PR, Issue）と Claude Code のセッションログを自動収集し、Claude API で自然言語の要約を生成して Notion データベースに記録するシステム。

## Architecture
常時稼働マシン（Raspberry Pi 等）を使用した2フェーズ構成。セッションログの生データは LAN 内にのみ保持する。

```
[開発マシン]
  git commit → post-commit hook ─┐
  ayumy sync（手動）─────────────┤
                                  ▼
                         rsync over SSH
                                  │
[常時稼働マシン]                  ▼
  ~/ayumy-data/claude-sessions/ に JSONL 蓄積
  cron (毎日 UTC 00:00) → daily_report.py
    ├─→ JSONL + GitHub API → Claude API で要約生成
    ├─→ Notion API で記録
    ├─→ Slack Webhook で通知
    └─→ 処理済み JSONL を削除
```

## Tech Stack
| 技術 | 用途 |
|---|---|
| Python 3.12 | メインスクリプト（`requests`, `anthropic`） |
| GitHub API (REST) | 開発アクティビティの取得 |
| Anthropic API (`claude-sonnet-4-20250514`) | 自然言語による要約生成 |
| Notion API | 作業記録の書き込み |
| Slack Incoming Webhook | 完了通知 |

## Directory Structure
```
ayumy/
├── scripts/
│   ├── daily_report.py        # メインスクリプト: GitHub API + Claude API + Notion API
│   └── sync_session.sh        # セッション転送スクリプト（hook・手動共用）
├── hooks/
│   └── post-commit            # 各リポジトリにシンボリックリンクで配置
├── Spec.md
├── CLAUDE.md
└── README.md
```

**常時稼働マシン上のデータディレクトリ**

```
~/ayumy-data/
├── claude-sessions/           # 開発マシンから転送された JSONL
│   ├── {project-name}/
│   │   └── {session-id}.jsonl
│   └── ...
└── logs/                      # 実行ログ
```

## Setup
詳細なセットアップ手順は [Spec.md](./Spec.md) の §8 を参照。

### Environment Variables (Server)
常時稼働マシンの `~/.ayumy.env` に設定:

| 変数 | 説明 |
|---|---|
| `GITHUB_PAT` | GitHub Fine-grained PAT |
| `ANTHROPIC_API_KEY` | Anthropic API キー |
| `NOTION_TOKEN` | Notion Internal Integration トークン |
| `NOTION_DATABASE_ID` | Notion データベース ID |
| `SLACK_WEBHOOK_URL` | Slack Incoming Webhook URL |
| `AYUMY_DATA_DIR` | データディレクトリのパス（デフォルト: `~/ayumy-data`） |

### Environment Variables (Dev Machine)
| 変数 | 説明 |
|---|---|
| `AYUMY_HOST` | 常時稼働マシンの SSH ホスト名（例: `pi@raspberrypi.local`） |
| `AYUMY_DATA_DIR` | データディレクトリのパス（デフォルト: `~/ayumy-data`） |

## Development Phases
| Phase | 内容 | Issue |
|---|---|---|
| 1 | セッション転送スクリプト（`sync_session.sh`） | [#1](https://github.com/n-yU/ayumy/issues/1) |
| 2 | Git hook（`post-commit`） | [#2](https://github.com/n-yU/ayumy/issues/2) |
| 3 | 常時稼働マシンのコンテナ化 | — |
| 4 | メインスクリプト（`daily_report.py`） | — |
| 5 | 結合テスト・運用準備 | — |

## Running Cost
課金が発生するのは Anthropic API のみ（GitHub API・Notion API は無料枠内）。

| 期間 | コスト |
|---|---|
| 1日 | ~$0.05 |
| 1ヶ月 | ~$1.5 |
| 1年 | ~$18 |

※ 平均的な開発日の見積もり。セッションログが大量にある日はトークン数が増加する。
