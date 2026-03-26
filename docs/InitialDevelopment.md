# Initial Development
以下の順序で初期開発を進める。依存関係の少ないコンポーネントから着手し、先に作ったものが後のテストデータ・検証基盤となる構成。継続的な実装はリリースノートにまとめる

## archived: v1 (NAS + Docker)
以下は設計変更前（NAS + ホストマシン構成）の開発手順。v1 Phase 1〜2 は完了済み、v1 Phase 3 は途中まで実施

<details>
<summary>v1 Phase 1〜5</summary>

### v1 Phase 1: セッション転送スクリプト（`scripts/sync_session.sh`）と CLI [#1](https://github.com/n-yU/ayumy/issues/1)
外部 API 不要。ローカル環境のみで動作確認できる
- `--project`, `--all`, `--background` オプションの実装
- マーカーファイル（`.ayumy_last_sync`）による差分検出
- NAS への cp による転送（冪等性の担保）
- `bin/ayumy` CLI エントリポイントの実装（`ayumy sync` でスクリプトを呼び出し）
- ローカル検証: `AYUMY_DATA_DIR` にローカルの別ディレクトリ（例: `/tmp/ayumy-data/`）を指定して使用

### v1 Phase 2: Git hook（`hooks/post-commit`） [#2](https://github.com/n-yU/ayumy/issues/2)
v1 Phase 1 の `sync_session.sh` を前提としたラッパー
- リポジトリパスからプロジェクト名を解決
- `sync_session.sh --project {name} --background` の呼び出し
- `exit 0` の保証（commit をブロックしない設計）
- hook の配布: `ayumy setup-hooks` コマンド（単体設置 / `--all` で一括設置）

### v1 Phase 3: ホストマシンのコンテナ化（`Dockerfile`, `compose.yaml`） [#6](https://github.com/n-yU/ayumy/issues/6)
v1 Phase 4 で作成するメインスクリプト（Python）の実行環境をコンテナとして構築する。日次の定期実行に加え、作業の区切りなど任意のタイミングでの手動実行も想定する
- `Dockerfile` の作成（Python 3.12 + 依存パッケージ `requests`, `anthropic`）
- `compose.yaml` の作成（`env_file`, volume mount, 環境変数の設定）
- ホストマシンセットアップスクリプトの作成（env ファイル生成、コンテナビルド＆テスト、crontab 設定）

### v1 Phase 4: メインスクリプト（`scripts/report.py`）
以下のサブ機能を順に実装する。各機能は独立して動作確認可能
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

## v2 Phase 1: セッション転送の S3 対応（`scripts/sync_session.sh`） [#9](https://github.com/n-yU/ayumy/issues/9)
既存の NAS 転送（`cp`）を S3 転送（`aws s3 cp`）に置き換える
- 転送先を `AYUMY_DATA_DIR`（ローカルパス）から `AYUMY_S3_BUCKET`（S3 バケット）に変更
- `--report` オプションの追加（転送後に `aws lambda invoke` で Lambda を呼び出す）
- `AYUMY_LAMBDA_FUNCTION` 環境変数の追加
- 既存の `--project`, `--all`, `--background` オプション、マーカーファイル方式はそのまま維持

## v2 Phase 2: AWS Lambda 環境の構築（`template.yaml`, `lambda/`） [#10](https://github.com/n-yU/ayumy/issues/10)
メインスクリプト（Python）の実行環境を Lambda として構築する
- SAM テンプレートの作成（Lambda 関数、EventBridge Scheduler、IAM ロール、S3 バケット）
- Lambda ハンドラの作成（`lambda/handler.py`）
- Secrets Manager へのシークレット登録
- `sam build && sam deploy` によるデプロイ確認

### v2 Phase 2.1: 実行方式の識別（`sync_session.sh`, `handler.py`） [#16](https://github.com/n-yU/ayumy/issues/16)
手動実行と定期実行で対象期間を切り替えるために、Lambda event に `source` フィールドを渡す仕組みを追加する
- `sync_session.sh` の Lambda 呼び出しペイロードに `{"source": "manual"}` を追加
- `handler.py` で `event.source` を `AYUMY_SOURCE` 環境変数として `report` パッケージに渡す
- 定期実行（EventBridge）は前日分、手動実行は当日分を対象とする

## v2 Phase 3: メインパッケージ（`lambda/report/`） [#14](https://github.com/n-yU/ayumy/issues/14)
以下のサブ機能を順に実装する。各機能は独立して動作確認可能
1. GitHub アクティビティ取得 — REST API で Commits / PRs / Issues を取得・整形
2. JSONL セッションログの読み取り — S3 バケットの `claude-sessions/` のパース
3. Claude API で要約生成 — Spec.md §5.3 のフォーマットに従い統合要約を生成
4. Notion API で書き込み — データベースプロパティとページ本文の作成（Spec.md §6 準拠）
5. Slack 通知 — Incoming Webhook でサマリーと Notion リンクを送信（Spec.md §5.4）
6. 処理済み JSONL のアーカイブ — 正常完了後に S3 上で `processed/` へ移動（Spec.md §5.5）

### v2 Phase 3.1: Activity 型リファクタリング [#26](https://github.com/n-yU/ayumy/issues/26)
`Activity` / `SessionActivity` の型エイリアスをクラスに昇格し、各 Client に分散している `format_activity` メソッドを Activity クラス自身に持たせる
- `Activity` → `GitHubActivity` にリネーム
- `GitHubActivity` / `SessionActivity` クラスに `format` メソッドを移動
- 呼び出し元（`__main__.py`）と型定義（`__init__.py`）の更新

### v2 Phase 3.2: タグ・ステータスのバリデーション [#22](https://github.com/n-yU/ayumy/issues/22)
Notion DB の select/multi-select オプションを allowlist として使用し、要約生成時にバリデーションする。タグ・ステータスの管理は Notion DB の UI から直接行う
- Notion DB スキーマから allowlist を取得
- システムプロンプトへの動的な候補リスト注入
- 要約生成時のバリデーション（allowlist 外の値を除外またはフォールバック）

### v2 Phase 3.3: セッションログの日付フィルタリング [#23](https://github.com/n-yU/ayumy/issues/23)
日をまたぐセッションで前日分のメッセージが翌日のレポートに混入する問題を解決する。`parse_session` で JSONL エントリの `timestamp` を `since` / `until` でフィルタし、対象期間内のメッセージのみを抽出する
- `parse_session` に `since` / `until` パラメータを追加
- `fetch_sessions` から `since` / `until` を伝播
- Spec.md §5.2 に日付フィルタリングの記述を追加

## v2 Phase 4: 追加実装
Phase 5 の結合テストにあたって追加で対応が必要となった改善点をまとめる

### v2 Phase 4.1: セッション起点の GitHub アクティビティ取得 [#32](https://github.com/n-yU/ayumy/issues/32)
S3 上のセッションログから対象リポジトリを先に特定し、そのリポジトリのみ GitHub アクティビティを取得するよう処理順を変更する。リポジトリ名は `post-commit` hook で `.ayumy_repo` に書き出し、S3 経由で Lambda に伝播する
- `post-commit` hook でリポジトリ名を `.ayumy_repo` に書き出す
- `sync_session.sh` で `.ayumy_repo` を S3 にアップロード
- `session.py` で S3 上の `.ayumy_repo` を読んでリポジトリ名を解決
- `fetch_activity` を指定リポジトリのみに限定
- `__main__.py` の処理順を変更（セッション取得 → GitHub アクティビティ取得）

### v2 Phase 4.2: Lambda の非同期呼び出し [#31](https://github.com/n-yU/ayumy/issues/31)
`ayumy sync --report` の Lambda 呼び出しを同期（`RequestResponse`）から非同期（`Event`）に変更する。同期呼び出しでは Lambda の実行時間が AWS CLI の read timeout を超えるとエラーになるため、非同期で即座に終了し、結果は Slack 通知で確認する
- `aws lambda invoke` に `--invocation-type Event` を追加
- 同期呼び出し用のレスポンス処理を削除

### v2 Phase 4.3: Notion data sources API への移行 [#35](https://github.com/n-yU/ayumy/issues/35)
Notion API バージョン 2025-09-03 で `databases.retrieve` のレスポンスから `properties` が削除され、`databases.query` も廃止されたため、data sources API に移行する
- `databases.retrieve` で `data_sources` から ID を取得し、`data_sources.retrieve` で `properties` を取得（`fetch_allowlists`）
- `databases.query` を `data_sources.query` に置き換え（`_archive_existing_pages`）
- `data_source_id` を `fetch_allowlists` でキャッシュし、未初期化時にガードするプロパティを追加

## v2 Phase 5: 結合テスト・運用準備 [#15](https://github.com/n-yU/ayumy/issues/15)
- Notion DB プロパティの作成（Setup.md §2 に従う）
- EventBridge Scheduler の有効化と初回実行確認
- 全コンポーネントの結合テスト（`ayumy sync --report` による手動実行）
- エラーハンドリング・CloudWatch Logs の検証
