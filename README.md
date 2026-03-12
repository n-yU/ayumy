# Ayumy
Traces of daily craft, woven by AI

GitHub 上の日次開発アクティビティ（Commit, PR, Issue）と Claude Code のセッションログを自動収集し、Claude API で自然言語の要約を生成して Notion データベースに記録するシステム

## Architecture
ホストマシン（Raspberry Pi 等）を使用した2フェーズ構成。セッションログの生データは LAN 内（NAS）にのみ保持する

```
[クライアントマシン]
  git commit → post-commit hook ─┐
  ayumy sync（手動）─────────────┤
                                  ▼
                         NAS (共有ストレージ)
                                  │
[ホストマシン]                    ▼
  cron (毎日 JST 00:00) → docker compose run --rm ayumy (report.py)
  手動実行 ─────────────→ docker compose run --rm ayumy (report.py)
    ├─→ JSONL + GitHub API → Claude API で要約生成
    ├─→ Notion API で記録
    ├─→ Slack Webhook で通知
    └─→ 処理済み JSONL を NAS 上にアーカイブ

[NAS]
  JSONL 蓄積（ホストマシンに NFS 等でマウント）
```

## Tech Stack
| 技術 | 用途 |
|---|---|
| Docker | レポート生成コンテナの実行環境 |
| Python 3.12 | メインスクリプト（`requests`, `anthropic`） |
| GitHub API (REST) | 開発アクティビティの取得 |
| Anthropic API (`claude-sonnet-4-20250514`) | 自然言語による要約生成 |
| Notion API | 作業記録の書き込み |
| Slack Incoming Webhook | 完了通知 |

## Directory Structure
```
ayumy/
├── bin/
│   └── ayumy                 # CLI エントリポイント（サブコマンドのディスパッチ）
├── scripts/
│   ├── report.py             # メインスクリプト: GitHub API + Claude API + Notion API
│   ├── sync_session.sh       # セッション転送スクリプト（hook・手動共用）
│   └── setup_hooks.sh        # hook の設置スクリプト
├── hooks/
│   └── post-commit           # 各リポジトリにシンボリックリンクで配置
├── Dockerfile                # レポート生成コンテナ
├── compose.yaml              # Docker Compose 設定
├── Spec.md
├── CLAUDE.md
└── README.md
```

## Setup
### クライアントマシン
```bash
# 1. リポジトリをクローン
git clone https://github.com/{user}/ayumy.git ~/ayumy

# 2. PATH を通す（~/.zshrc 等に追加）
export PATH="$HOME/ayumy/bin:$PATH"

# 3. NAS をマウントし、環境変数を設定（~/.zshrc 等に追加）
export AYUMY_DATA_DIR="/path/to/nas/ayumy-data"

# 4. 動作確認
ayumy sync
```

### ホストマシン
`~/.ayumy.env` に環境変数（`GITHUB_PAT`, `ANTHROPIC_API_KEY`, `NOTION_TOKEN`, `NOTION_DATABASE_ID`, `SLACK_WEBHOOK_URL`, `AYUMY_DATA_DIR`）を設定し、Docker Compose でレポート生成コンテナを実行する。詳細は [Spec.md](./Spec.md) の §8 を参照

## Running Cost
課金が発生するのは Anthropic API のみ（GitHub API・Notion API は無料枠内）

| 期間 | コスト |
|---|---|
| 1日 | ~$0.05 |
| 1ヶ月 | ~$1.5 |
| 1年 | ~$18 |

※ 平均的な開発日の見積もり。セッションログが大量にある日はトークン数が増加する
