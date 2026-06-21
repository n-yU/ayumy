# CLAUDE.md
このファイルは Claude Code (claude.ai/code) がこのリポジトリで作業する際のガイドラインを提供する。

## プロジェクト概要
**ayumy** — GitHub 上の日次開発アクティビティ（Commit, PR, Issue）と Claude Code セッションログを自動収集し、Claude API で自然言語の要約を生成して Notion データベースに記録するシステム。

## アーキテクチャ
2フェーズ構成（セッションログは S3 に保管、セッションメタデータは DynamoDB に集約、レポート生成は AWS Lambda で実行）:

1. **フェーズ 1（pre-push hook / 手動同期）**: 各リポジトリでの push を契機に、`~/.claude/projects/` から未同期の Claude Code セッションの JSONL を S3 バケットにアップロードする。アップロード失敗時は push を中止する。`ayumy sync --report` で S3 転送後に Lambda を呼び出してレポート生成まで実行できる。
2. **フェーズ 2（AWS Lambda）**: S3 上のセッションログをパースして DynamoDB に書き込み、S3 から JSONL を削除する。DynamoDB からセッションメタデータを読み取り、GitHub API によるアクティビティ取得を行い、Claude API で要約を生成して Notion に書き込み、Slack に通知する。EventBridge Scheduler による日次の定期実行に加え、`ayumy sync --report` による手動実行にも対応する。

## リポジトリ構成
```
scripts/sync_session.sh              # セッション転送スクリプト（hook・手動共用）
scripts/setup_hooks.sh               # hook の設置スクリプト
hooks/pre-push                       # Git hook（各リポジトリにシンボリックリンクで配置）
lambda/handler.py                    # Lambda ハンドラ（report パッケージを呼び出すエントリポイント）
lambda/report/                       # メインパッケージ: GitHub API + Claude API + Notion API
lambda/requirements.txt              # Lambda デプロイ用の依存パッケージ
lambda/requirements-dev.txt          # ローカル開発用の依存パッケージ（boto3 を含む）
template.yaml                        # AWS SAM テンプレート（Lambda, EventBridge, IAM ロール, S3 バケット, DynamoDB テーブル）
```

## 技術詳細
- **実行環境**: AWS Lambda（SAM でデプロイ）
- **クライアント対応 OS**: クライアント側のスクリプト（`scripts/`, `hooks/`, `bin/ayumy`）は macOS のみサポート
- **ローカル開発**: uv で `.venv` を管理。shell テスト実行には bats が必要（`brew install bats-core`）
- **テスト・lint・format コマンド**: `make test`（Python + shell 一括）／ `make test-python` ／ `make test-shell` ／ `make test-cov`（Python カバレッジ計測。Shell カバレッジは CI でのみ取得）／ `make format` ／ `make format-check` ／ `make lint` ／ `make lint-fix` を使う
- **避けるコマンド**: `uv run pytest` を使わない（CWD の `pyproject.toml` を project marker として検出し `uv.lock` を暗黙生成してしまうため。本リポジトリは `lambda/requirements.txt` 主導で `uv.lock` を管理対象外としている）
- **言語**: Python 3.12、デプロイ依存: `requests`, `anthropic`, `PyGithub`、開発依存: 左記 + `boto3`
- **Claude モデル**: 要約生成に `claude-sonnet-4-6` を使用
- **GitHub API**: REST、Fine-grained PAT、セッションログから特定したリポジトリのみ対象
- **Notion API**: Internal Integration Token、データベースプロパティは [Spec: Database Properties](docs/Spec.md#database-properties) に定義
- **Hook 設計**: フォアグラウンド同期実行で、転送失敗時は非ゼロ終了で push を中止する（silent fail 防止）。セッション ID 単位の上書きで冪等性を担保

## 環境変数
Lambda（環境変数 + Secrets Manager）:
- `AYUMY_S3_BUCKET` — セッションログの保管先 S3 バケット名（環境変数）
- `AYUMY_DYNAMO_TABLE` — セッションメタデータの DynamoDB テーブル名（環境変数）
- `AYUMY_LAMBDA_TIMEOUT` — Lambda 関数の timeout 秒数（環境変数、template.yaml の `LambdaTimeoutSeconds` パラメータと連動）
- `NOTION_DATABASE_ID` — 書き込み先の Notion データベース ID（環境変数）
- `GITHUB_PAT` — GitHub Fine-grained PAT（Secrets Manager）
- `ANTHROPIC_API_KEY` — Anthropic API キー（Secrets Manager）
- `NOTION_SECRET` — Notion Internal Integration トークン（Secrets Manager）
- `SLACK_WEBHOOK_URL` — Slack Incoming Webhook URL（Secrets Manager）

クライアントマシン:
- `AYUMY_S3_BUCKET` — セッションログの保管先 S3 バケット名
- `AYUMY_LAMBDA_FUNCTION` — Lambda 関数名（`--report` オプション用）

## 開発メモ
- 仕様書は [Spec.md](docs/Spec.md)（日本語）— すべての要件の原典
- 初期開発手順は [Initial-Development.md](docs/archive/Initial-Development.md) — フェーズ別の実装計画と v1 からの変遷を記録
- JSONL の生データは S3 バケットに保管し、リモートリポジトリには push しない
- アクティビティの取得対象期間: 前日 JST 00:00:00 〜 当日 JST 00:00:00
- アクティビティが 0 件の日はスキップまたは「活動なし」と記録
- 各 commit における整合性チェック（共通 CLAUDE.md の Git 操作セクション参照）の対象に含めるドキュメントは [Spec.md](docs/Spec.md), [Initial-Development.md](docs/archive/Initial-Development.md), [README.md](README.md), [Setup.md](docs/Setup.md), [Manual.md](docs/Manual.md)
