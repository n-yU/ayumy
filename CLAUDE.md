# CLAUDE.md
このファイルは Claude Code (claude.ai/code) がこのリポジトリで作業する際のガイドラインを提供する。

## プロジェクト概要
**ayumy** — GitHub 上の日次開発アクティビティ（Commit, PR, Issue）と Claude Code セッションログを自動収集し、Claude API で自然言語の要約を生成して Notion データベースに記録するシステム。

## アーキテクチャ
2フェーズ構成（ホストマシン＝Raspberry Pi 等を使用、データは NAS に保持）:

1. **フェーズ 1（post-commit hook）**: 各リポジトリでの commit を契機に、`~/.claude/projects/` から未同期の Claude Code セッションの JSONL を NAS 上のデータディレクトリにコピーする。
2. **フェーズ 2（ホストマシン上の Docker コンテナ）**: NAS 上のセッションログの読み取りと GitHub API によるアクティビティ取得を行い、Claude API で要約を生成して Notion に書き込み、Slack に通知する。処理済み JSONL は `processed/` にアーカイブする。cron による日次の定期実行に加え、任意のタイミングでの手動実行にも対応する。

## リポジトリ構成
```
scripts/report.py                    # メインスクリプト: GitHub API + Claude API + Notion API
scripts/sync_session.sh              # セッション転送スクリプト（hook・手動共用）
scripts/setup_hooks.sh               # hook の設置スクリプト
scripts/setup_host.sh                # ホストマシンのセットアップスクリプト
hooks/post-commit                    # Git hook（各リポジトリにシンボリックリンクで配置）
Dockerfile                           # レポート生成コンテナ
compose.yaml                         # Docker Compose 設定
```

## 技術詳細
- **実行環境**: Docker（`docker compose run --rm` で起動）
- **言語**: Python 3.12、依存: `requests`, `anthropic`
- **Claude モデル**: 要約生成に `claude-sonnet-4-20250514` を使用
- **GitHub API**: REST、Fine-grained PAT、`affiliation=owner` で自分の所有リポジトリのみ対象
- **Notion API**: Internal Integration Token、データベースプロパティは Spec.md §6 に定義
- **Hook 設計**: 必ず `exit 0` を返す（commit をブロックしない）、バックグラウンド実行、セッション ID 単位の上書きで冪等性を担保

## 環境変数
ホストマシン（`~/.ayumy.env`）:
- `GITHUB_PAT` — GitHub Fine-grained PAT（全 owner リポジトリへの read 権限）
- `ANTHROPIC_API_KEY` — Anthropic API キー
- `NOTION_TOKEN` — Notion Internal Integration トークン
- `NOTION_DATABASE_ID` — 書き込み先の Notion データベース ID
- `AYUMY_DATA_DIR` — NAS 上のデータディレクトリのマウントパス

クライアントマシン（post-commit hook 用）:
- `AYUMY_DATA_DIR` — NAS 上のデータディレクトリのマウントパス

## 書式規約
- **Markdown の見出し**: 見出しの直後に空行を入れない（見出しの前には空行を入れる）

## 言語規約
- **コミットメッセージ**: 英語で書く
- **コード内コメント**: 英語で書く
- **GitHub Issue・PR**: タイトルは英語、本文は日本語で書く
- **ドキュメント**（md ファイル）: タイトルは英語で書く

## 開発メモ
- 仕様書は `Spec.md`（日本語）— すべての要件の原典
- JSONL の生データは LAN 内（NAS）にのみ保持し、リモートリポジトリには push しない
- アクティビティの取得対象期間: 前日 JST 00:00:00 〜 当日 JST 00:00:00
- アクティビティが 0 件の日はスキップまたは「活動なし」と記録
