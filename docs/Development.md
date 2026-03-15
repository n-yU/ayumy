# Development
以下の順序で実装を進める。依存関係の少ないコンポーネントから着手し、先に作ったものが後のテストデータ・検証基盤となる構成。

## v2 Phase 1: セッション転送の S3 対応（`scripts/sync_session.sh`） [#9](https://github.com/n-yU/ayumy/issues/9)
既存の NAS 転送（`cp`）を S3 転送（`aws s3 cp`）に置き換える。
- 転送先を `AYUMY_DATA_DIR`（ローカルパス）から `AYUMY_S3_BUCKET`（S3 バケット）に変更
- `--report` オプションの追加（転送後に `aws lambda invoke` で Lambda を呼び出す）
- `AYUMY_LAMBDA_FUNCTION` 環境変数の追加
- 既存の `--project`, `--all`, `--background` オプション、マーカーファイル方式はそのまま維持

## v2 Phase 2: AWS Lambda 環境の構築（`template.yaml`, `lambda/`） [#10](https://github.com/n-yU/ayumy/issues/10)
メインスクリプト（Python）の実行環境を Lambda として構築する。
- SAM テンプレートの作成（Lambda 関数、EventBridge Scheduler、IAM ロール、S3 バケット）
- Lambda ハンドラの作成（`lambda/handler.py`）
- Secrets Manager へのシークレット登録
- `sam build && sam deploy` によるデプロイ確認

## v2 Phase 3: メインスクリプト（`lambda/report.py`）
以下のサブ機能を順に実装する。各機能は独立して動作確認可能。
1. GitHub アクティビティ取得 — REST API で Commits / PRs / Issues を取得・整形
2. JSONL セッションログの読み取り — S3 バケットの `claude-sessions/` のパース
3. Claude API で要約生成 — Spec.md §5.3 のフォーマットに従い統合要約を生成
4. Notion API で書き込み — データベースプロパティとページ本文の作成（Spec.md §6 準拠）
5. Slack 通知 — Incoming Webhook でサマリーと Notion リンクを送信（Spec.md §5.4）
6. 処理済み JSONL のアーカイブ — 正常完了後に S3 上で `processed/` へ移動（Spec.md §5.5）

## v2 Phase 4: 結合テスト・運用準備
- 全コンポーネントの結合テスト（クライアントマシン → S3 → Lambda → Notion の一連の流れ）
- EventBridge Scheduler の設定確認と初回実行
- エラーハンドリング・CloudWatch Logs の検証

## アーカイブ: v1 開発手順（NAS + Docker 構成）
以下は設計変更前（NAS + ホストマシン構成）の開発手順。v1 Phase 1〜2 は完了済み、v1 Phase 3 は途中まで実施。

<details>
<summary>v1 Phase 1〜5</summary>

### v1 Phase 1: セッション転送スクリプト（`scripts/sync_session.sh`）と CLI [#1](https://github.com/n-yU/ayumy/issues/1)
外部 API 不要。ローカル環境のみで動作確認できる。
- `--project`, `--all`, `--background` オプションの実装
- マーカーファイル（`.ayumy_last_sync`）による差分検出
- NAS への cp による転送（冪等性の担保）
- `bin/ayumy` CLI エントリポイントの実装（`ayumy sync` でスクリプトを呼び出し）
- ローカル検証: `AYUMY_DATA_DIR` にローカルの別ディレクトリ（例: `/tmp/ayumy-data/`）を指定して使用

### v1 Phase 2: Git hook（`hooks/post-commit`） [#2](https://github.com/n-yU/ayumy/issues/2)
v1 Phase 1 の `sync_session.sh` を前提としたラッパー。
- リポジトリパスからプロジェクト名を解決
- `sync_session.sh --project {name} --background` の呼び出し
- `exit 0` の保証（commit をブロックしない設計）
- hook の配布: `ayumy setup-hooks` コマンド（単体設置 / `--all` で一括設置）

### v1 Phase 3: ホストマシンのコンテナ化（`Dockerfile`, `compose.yaml`） [#6](https://github.com/n-yU/ayumy/issues/6)
v1 Phase 4 で作成するメインスクリプト（Python）の実行環境をコンテナとして構築する。日次の定期実行に加え、作業の区切りなど任意のタイミングでの手動実行も想定する。
- `Dockerfile` の作成（Python 3.12 + 依存パッケージ `requests`, `anthropic`）
- `compose.yaml` の作成（`env_file`, volume mount, 環境変数の設定）
- ホストマシンセットアップスクリプトの作成（env ファイル生成、コンテナビルド＆テスト、crontab 設定）

### v1 Phase 4: メインスクリプト（`scripts/report.py`）
以下のサブ機能を順に実装する。各機能は独立して動作確認可能。
1. GitHub アクティビティ取得 — REST API で Commits / PRs / Issues を取得・整形
2. JSONL セッションログの読み取り — `$AYUMY_DATA_DIR/claude-sessions/` のパース
3. Claude API で要約生成 — §5.3 のフォーマットに従い統合要約を生成
4. Notion API で書き込み — データベースプロパティとページ本文の作成（§6 準拠）
5. Slack 通知 — Incoming Webhook でサマリーと Notion リンクを送信（§5.4）
6. 処理済み JSONL のアーカイブ — 正常完了後に `processed/` へ移動（§5.5）

### v1 Phase 5: 結合テスト・運用準備
- 全コンポーネントの結合テスト（クライアントマシン → Pi → Notion の一連の流れ）
- cron 設定の投入と初回実行の確認
- エラーハンドリング・ログ出力の検証

</details>
