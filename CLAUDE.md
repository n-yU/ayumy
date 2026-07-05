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
lambda/config/                       # チューニング定数の YAML と loader（モデル ID・throttle 値等）
lambda/report/                       # メインパッケージ: GitHub API + Claude API + Notion API
lambda/requirements.in               # Lambda デプロイ依存の source（直接依存のみ、バージョン範囲指定）
lambda/requirements-dev.in           # ローカル開発依存の source（boto3 等を追加、`-r requirements.in` で本体を参照）
lambda/requirements.txt              # `requirements.in` から `uv pip compile --generate-hashes` で生成した hash 付き lock
lambda/requirements-dev.txt          # `requirements-dev.in` から同様に生成した hash 付き lock
template.yaml                        # AWS SAM テンプレート（Lambda, EventBridge, IAM ロール, S3 バケット, DynamoDB テーブル）
```

## 技術詳細
- **実行環境**: AWS Lambda（SAM でデプロイ）
- **クライアント対応 OS**: クライアント側のスクリプト（`scripts/`, `hooks/`, `bin/ayumy`）は macOS のみサポート
- **ローカル開発**: uv で `.venv` を管理。shell テスト実行には bats が必要（`brew install bats-core`）
- **テスト・lint・format コマンド**: `make test`（Python + shell 一括）／ `make test-python` ／ `make test-shell` ／ `make test-cov`（Python カバレッジ計測。Shell カバレッジは CI でのみ取得）／ `make format` ／ `make format-check` ／ `make lint` ／ `make lint-fix` を使う。target 一覧と用途は `make help` で確認できる
- **Lambda デプロイ・build コマンド**
  - デプロイ: `make lambda-deploy`（AWS 認証確認 + `sam build` + `sam deploy --no-confirm-changeset`）
  - ローカル invoke: `make lambda-invoke`（`sam build` + `sam local invoke`）
  - raw `sam build` / `sam deploy` は Makefile が扱わないケースに限定する（初回 `sam deploy --guided`、S3 バケット変更時の `sam deploy --no-resolve-s3`）
- **依存管理**: 直接依存は `lambda/requirements.in` / `lambda/requirements-dev.in` に記述し、`make lock` で `uv pip compile --generate-hashes` を呼んで hash 付きの `lambda/requirements.txt` / `lambda/requirements-dev.txt` を再生成する。Lambda デプロイ・CI・`make lambda-install` はいずれも生成された `.txt` を読むため、`.in` を変更したら必ず `make lock` を実行し `.txt` を commit する。`uv` のバージョンが異なると `.txt` の出力が変わり CI drift check が誤検知するため、ローカルでも CI 側（`.github/workflows/cicd.yml` の `astral-sh/setup-uv`）と同じバージョンを使う
- **避けるコマンド**: `uv run pytest` を使わない（CWD の `pyproject.toml` を project marker として検出し `uv.lock` を暗黙生成してしまうため。本リポジトリは `pip-compile` ベースの `requirements*.txt` を lock として運用し、`uv.lock` は管理対象外としている）
- **言語**: Python 3.12、デプロイ依存: `requests`, `anthropic`, `PyGithub`、開発依存: 左記 + `boto3`
- **Claude モデル**: 要約生成モデルは [lambda/config/config.yml](lambda/config/config.yml) で定義（既定 `claude-sonnet-4-6`）
- **GitHub API**: REST、Fine-grained PAT、セッションログから特定したリポジトリのみ対象
- **Notion API**: Internal Integration Token、データベースプロパティは [Spec: Database Properties](docs/Spec.md#database-properties) に定義
- **Hook 設計**: フォアグラウンド同期実行で、転送失敗時は非ゼロ終了で push を中止する（silent fail 防止）。セッション ID 単位の上書きで冪等性を担保

## 環境変数
Lambda（環境変数 + Secrets Manager）:
- `AYUMY_S3_BUCKET` — セッションログの保管先 S3 バケット名（環境変数）
- `AYUMY_DYNAMO_TABLE` — セッションメタデータの DynamoDB テーブル名（環境変数）
- `AYUMY_LAMBDA_TIMEOUT` — Lambda 関数の timeout 秒数（環境変数、template.yaml の `LambdaTimeoutSeconds` パラメータと連動）
- `NOTION_DATABASE_ID` — 書き込み先の Notion データベース ID（環境変数）
- `SLACK_CHANNEL` — 通知先 Slack channel ID（環境変数）
- `GITHUB_PAT` — GitHub Fine-grained PAT（Secrets Manager）
- `ANTHROPIC_API_KEY` — Anthropic API キー（Secrets Manager）
- `NOTION_SECRET` — Notion Internal Integration トークン（Secrets Manager）
- `SLACK_BOT_TOKEN` — Slack Bot User OAuth Token（Secrets Manager）

クライアントマシン:
- `AYUMY_S3_BUCKET` — セッションログの保管先 S3 バケット名
- `AYUMY_LAMBDA_FUNCTION` — Lambda 関数名（`--report` オプション用）

## 開発メモ
- 仕様書は [Spec.md](docs/Spec.md)（日本語）— すべての要件の原典
- 初期開発手順は [Initial-Development.md](docs/archive/Initial-Development.md) — フェーズ別の実装計画と v1 からの変遷を記録
- [Manual.md](docs/Manual.md) は運用者目線で書く。実装寄りの用語（「振る舞いを調整する値」等）や構造の説明（「〜に集約されている」等）は使わず、「何ができるか」「どこで変更するか」を具体的に示す。実装・仕様レベルの細部は Spec.md 側に委ねる
- JSONL の生データは S3 バケットに保管し、リモートリポジトリには push しない
- アクティビティの取得対象期間: 前日 JST 00:00:00 〜 当日 JST 00:00:00
- アクティビティが 0 件の日はスキップまたは「活動なし」と記録
- 各 commit における整合性チェック（共通 CLAUDE.md の Git 操作セクション参照）の対象に含めるドキュメントは [Spec.md](docs/Spec.md), [Initial-Development.md](docs/archive/Initial-Development.md), [README.md](README.md), [Setup.md](docs/Setup.md), [Manual.md](docs/Manual.md)
