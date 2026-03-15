# CLAUDE.md
このファイルは Claude Code (claude.ai/code) がこのリポジトリで作業する際のガイドラインを提供する。

## プロジェクト概要
**ayumy** — GitHub 上の日次開発アクティビティ（Commit, PR, Issue）と Claude Code セッションログを自動収集し、Claude API で自然言語の要約を生成して Notion データベースに記録するシステム。

## アーキテクチャ
2フェーズ構成（セッションログは S3 に保管、レポート生成は AWS Lambda で実行）:

1. **フェーズ 1（post-commit hook / 手動同期）**: 各リポジトリでの commit を契機に、`~/.claude/projects/` から未同期の Claude Code セッションの JSONL を S3 バケットにアップロードする。`ayumy sync --report` で S3 転送後に Lambda を呼び出してレポート生成まで実行できる。
2. **フェーズ 2（AWS Lambda）**: S3 上のセッションログの読み取りと GitHub API によるアクティビティ取得を行い、Claude API で要約を生成して Notion に書き込み、Slack に通知する。処理済み JSONL は S3 上で `processed/` にアーカイブする。EventBridge Scheduler による日次の定期実行に加え、`ayumy sync --report` による手動実行にも対応する。

## リポジトリ構成
```
scripts/sync_session.sh              # セッション転送スクリプト（hook・手動共用）
scripts/setup_hooks.sh               # hook の設置スクリプト
hooks/post-commit                    # Git hook（各リポジトリにシンボリックリンクで配置）
lambda/handler.py                    # Lambda ハンドラ（report.py を呼び出すエントリポイント）
lambda/report.py                     # メインスクリプト: GitHub API + Claude API + Notion API
lambda/requirements.txt              # Lambda 用の依存パッケージ
template.yaml                        # AWS SAM テンプレート（Lambda, EventBridge, IAM ロール, S3 バケット）
```

## 技術詳細
- **実行環境**: AWS Lambda（SAM でデプロイ）
- **言語**: Python 3.12、依存: `requests`, `anthropic`, `boto3`
- **Claude モデル**: 要約生成に `claude-sonnet-4-20250514` を使用
- **GitHub API**: REST、Fine-grained PAT、`affiliation=owner` で自分の所有リポジトリのみ対象
- **Notion API**: Internal Integration Token、データベースプロパティは Spec.md §6 に定義
- **Hook 設計**: 必ず `exit 0` を返す（commit をブロックしない）、バックグラウンド実行、セッション ID 単位の上書きで冪等性を担保

## 環境変数
Lambda（環境変数 + Secrets Manager）:
- `AYUMY_S3_BUCKET` — セッションログの保管先 S3 バケット名（環境変数）
- `NOTION_DATABASE_ID` — 書き込み先の Notion データベース ID（環境変数）
- `GITHUB_PAT` — GitHub Fine-grained PAT（Secrets Manager）
- `ANTHROPIC_API_KEY` — Anthropic API キー（Secrets Manager）
- `NOTION_SECRET` — Notion Internal Integration トークン（Secrets Manager）
- `SLACK_WEBHOOK_URL` — Slack Incoming Webhook URL（Secrets Manager）

クライアントマシン:
- `AYUMY_S3_BUCKET` — セッションログの保管先 S3 バケット名
- `AYUMY_LAMBDA_FUNCTION` — Lambda 関数名（`--report` オプション用）

## 書式規約
- **Markdown の見出し**: 見出しの直後に空行を入れない（見出しの前には空行を入れる）

## 言語規約
- **コミットメッセージ**: 英語で書く
- **コード内コメント**: 英語で書く
- **GitHub Issue・PR**: タイトルは英語、本文は日本語で書く
- **ドキュメント**（md ファイル）: タイトルは英語で書く

## 開発メモ
- 仕様書は `Spec.md`（日本語）— すべての要件の原典
- JSONL の生データは S3 バケットに保管し、リモートリポジトリには push しない
- アクティビティの取得対象期間: 前日 JST 00:00:00 〜 当日 JST 00:00:00
- アクティビティが 0 件の日はスキップまたは「活動なし」と記録
