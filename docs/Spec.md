# Ayumy: Spec
## 1. 概要
GitHub 上の日次開発アクティビティ（Commit, Pull Request, Issue）と Claude Code での会話記録を自動収集し、Claude API で自然言語の要約を生成したうえで、Notion データベースに記録するシステム。対象リポジトリは S3 上のセッションログから特定する。

## 2. 目的
- 日々の開発作業を自動的に記録・蓄積する
- Claude Code での思考・意思決定の過程も含めて記録する
- 自然言語の要約により、振り返りや共有が容易な形で残す
- 手動の日報作成を不要にする

## 3. システム構成
### 3.1 アーキテクチャ
本システムは2フェーズで構成される。セッションログは S3 バケットに保管し、レポート生成は AWS Lambda で実行する。セッションメタデータは DynamoDB に集約する。

```
[クライアントマシン]
  git commit → post-commit hook ─┐
  ayumy sync（手動）─────────────┤
  ayumy sync --report ───────────┤── S3 転送後に Lambda も実行
                                  ▼
                         S3 バケット (ayumy-data)
                                  │
[AWS Lambda]                      ▼
  EventBridge (毎日 JST 00:00) → Lambda (report)
  ayumy sync --report ──────────→ Lambda (report)
    ├─→ JSONL パース → DynamoDB にセッション書き込み → S3 から JSONL 削除
    ├─→ DynamoDB + GitHub API → Claude API で要約生成
    ├─→ Notion API で記録
    └─→ Slack Webhook で通知

[S3]
  s3://{bucket}/
  └── claude-sessions/ に JSONL 蓄積

[DynamoDB]
  ayumy-sessions テーブル
  └── セッションメタデータ（日付 × リポジトリ × セッション ID）
```

### 3.2 使用する外部サービス・API
| サービス | 用途 | 認証方式 |
|---|---|---|
| GitHub API (REST) | アクティビティデータの取得 | Fine-grained PAT |
| Anthropic API | 自然言語による要約生成 | API Key |
| Notion API | 作業記録の書き込み | Internal Integration Token |
| Slack Incoming Webhook | 完了通知 | Webhook URL |
| AWS S3 | セッションログの保管 | AWS 認証情報（IAM ユーザー / プロファイル） |
| Amazon DynamoDB | セッションメタデータの集約 | IAM ロール |
| AWS Lambda | レポート生成の実行環境 | IAM ロール |
| Amazon EventBridge Scheduler | 日次の定期実行 | — |

### 3.3 ディレクトリ構成
**ayumy リポジトリ（GitHub）** — スクリプトと設定のみ。セッションデータは含まない。

```
ayumy/
├── bin/
│   └── ayumy                        # CLI エントリポイント（サブコマンドのディスパッチ）
├── scripts/
│   ├── sync_session.sh              # セッション転送スクリプト（hook・手動共用）
│   └── setup_hooks.sh               # hook の設置スクリプト
├── hooks/
│   └── post-commit                  # 各リポジトリにシンボリックリンクで配置
├── lambda/
│   ├── handler.py                   # Lambda ハンドラ（report パッケージを呼び出すエントリポイント）
│   ├── report/                      # メインパッケージ: GitHub API + Claude API + Notion API
│   │   ├── __init__.py              # 型定義（Activity クラス）、共通ユーティリティ
│   │   ├── __main__.py              # エントリポイント（python -m report）
│   │   ├── github.py                # GitHub アクティビティ取得
│   │   ├── notion.py                # Notion API 書き込み
│   │   ├── session.py               # Claude Code セッションログ読み取り
│   │   ├── slack.py                 # Slack 通知
│   │   ├── store.py                 # DynamoDB セッション書き込み
│   │   └── summarizer.py            # Claude API 要約生成
│   ├── requirements.txt             # Lambda デプロイ用の依存パッケージ
│   └── requirements-dev.txt         # ローカル開発用の依存パッケージ（boto3 を含む）
├── template.yaml                    # AWS SAM テンプレート（Lambda, EventBridge, IAM ロール, S3 バケット）
├── docs/
│   ├── Setup.md
│   ├── Spec.md
│   └── InitialDevelopment.md
├── CLAUDE.md
└── README.md
```

**S3 バケット**

```
s3://{bucket}/
└── claude-sessions/                  # クライアントマシンから転送された JSONL（DynamoDB 書き込み後に削除）
    ├── {project-name}/
    │   └── {session-id}.jsonl
    └── ...
```

実行ログは CloudWatch Logs に出力する。

## 4. フェーズ 1: セッションログの転送
### 4.1 概要
Claude Code セッションの JSONL を S3 バケットに転送する。

- **自動転送（post-commit hook）**: commit を契機に、当該プロジェクトの未同期セッションをバックグラウンドで転送
- **手動転送（`ayumy sync`）**: commit せずに作業を中断する場合など、任意のタイミングで実行
- **手動転送＋レポート生成（`ayumy sync --report`）**: S3 への転送後に Lambda を呼び出してレポート生成まで実行

いずれも共通の転送スクリプト `scripts/sync_session.sh` を使用する。`--report` 指定時は転送完了後に `aws lambda invoke` で Lambda 関数を呼び出す。

### 4.2 データソース
Claude Code は会話を `~/.claude/projects/` 以下にローカル保存している。

- 各プロジェクトがディレクトリとして存在（パスのスラッシュがダッシュに置換された名前）
- 個別セッションは JSONL ファイル（`{session-id}.jsonl`）として保存
- メタデータ（セッション ID、タイムスタンプ、ブランチ等）は JSONL の各エントリに埋め込まれている
- 外部インデックスファイルは存在しない

JSONL の各エントリは以下の構造を持つ（Claude Code が生成するデータの観測に基づく。公式仕様は存在しない）:

| フィールド | 型 | 説明 |
|---|---|---|
| `type` | String | エントリ種別（`"user"`, `"assistant"`, `"summary"` 等） |
| `timestamp` | String | ISO 8601 形式のタイムスタンプ（例: `"2026-03-28T10:00:00+09:00"`）。常に存在するが、不正な値は観測されていない |
| `message.content` | String / List | 文字列またはブロックのリスト。`type=user` は通常文字列だが `tool_result` を含むリストの場合もある |

`type=assistant` の `message.content` リスト内のブロック:

| フィールド | 型 | 説明 |
|---|---|---|
| `type` | String | ブロック種別（`"text"`, `"tool_use"` 等） |
| `name` | String | `type=tool_use` の場合のツール名 |

`type=user` の `message.content` がリストの場合のブロック:

| フィールド | 型 | 説明 |
|---|---|---|
| `type` | String | ブロック種別（`"tool_result"` 等） |
| `content` | String | ツール実行結果のテキスト |
| `is_error` | Boolean | エラー結果かどうか |

`tool_result` の `content` に `[... <short-sha>] <message>` 形式の行が含まれる場合、git commit の実行結果として SHA とコミットメッセージを抽出する:

- 1つの `tool_result` に複数のコミット行が含まれる場合は全て抽出する
- pre-commit hook の出力が先行する場合にも対応する（行単位でパターンを検索）
- 通常の `[branch sha]` 形式に加え、`[branch (root-commit) sha]` や `[detached HEAD sha]` にも対応する
- これにより squash merge で GitHub API から取得できないコミットを補完する

Claude Code が生成するため、タイムスタンプのフォーマットは安定しており、パース時に防御的な例外処理（`ValueError` の catch 等）は行わない

転送対象のセッションは、マーカーファイル（`.ayumy_last_sync`）との mtime 比較で決定する。

- マーカーが存在しない場合（初回）は全 JSONL を対象とする
- スキャン前に一時マーカー（`.ayumy_last_sync.tmp`）を作成し、転送成功後に `mv` で本マーカーに昇格させる
- これにより、転送中に更新されたファイルが次回検出漏れしないようにする

### 4.3 転送スクリプト（`sync_session.sh`）
hook と手動実行の両方から呼ばれる共通スクリプト。

```
sync_session.sh [--project <project-name>] [--all] [--background] [--report] [--date DATE]
```

| オプション | 動作 |
|---|---|
| `--project <name>` | 指定プロジェクトの差分セッションのみ転送。`<name>` は `~/.claude/projects/` 以下のディレクトリ名（例: `-Users-username-Documents-github-repo`） |
| `--all` | 全プロジェクトから差分セッションを一括転送 |
| `--background` | バックグラウンドで実行（hook 用） |
| `--report` | S3 転送後に Lambda 関数を呼び出してレポート生成を実行 |
| `--date DATE` | 指定日または日付範囲のレポートを生成・再生成（`--report` 必須）。`YYYY-MM-DD` または `YYYY-MM-DD..YYYY-MM-DD` 形式 |
| 引数なし | カレントディレクトリに対応するプロジェクトを自動判定 |

要件:

- **環境変数 `AYUMY_S3_BUCKET`**: セッションログの保管先 S3 バケット名
- **AWS 認証情報**: AWS CLI が使用可能な状態であること（`~/.aws/credentials` または環境変数）
- **冪等性**: `aws s3 cp` による上書きで同じ JSONL の複数回転送でも問題ない

### 4.4 post-commit hook
`ayumy/hooks/post-commit` として管理し、各リポジトリの `.git/hooks/post-commit` にシンボリックリンクで配置する。

hook はリポジトリパスからプロジェクト名を解決し、`sync_session.sh --project {name} --background` を呼び出すラッパーである。

- hook の失敗は commit に影響を与えない（exit 0 を保証）
- エラーは stderr に出力するのみ

hook の配布方法（`ayumy setup-hooks` コマンドで設置）:

- **単体設置**: 対象リポジトリで `ayumy setup-hooks` を実行
- **一括設置**: `ayumy setup-hooks --all <dir>` で指定ディレクトリ直下のリポジトリに設置
- **手動設置**: `ln -s {AYUMY_REPO}/hooks/post-commit {REPO}/.git/hooks/post-commit`

`--all` の対象は `.git` ディレクトリを持つ通常のリポジトリのみ。Git worktree やサブモジュール（`.git` がファイルのケース）は対象外。

### 4.5 手動同期（`ayumy sync`）
commit せずに作業を中断する場合や、hook で転送されなかったセッションを補完する。

```bash
ayumy sync                                                      # current directory のプロジェクトを同期
ayumy sync --all                                                # 全プロジェクトの未同期分を一括同期
ayumy sync --project -Users-username-Documents-github-my-project # 特定プロジェクトを指定
ayumy sync --report                                             # 同期後にレポート生成（Lambda 実行）まで行う
ayumy sync --all --report                                       # 全プロジェクト同期 + レポート生成
ayumy sync --report --date 2026-03-25                           # 指定日のレポートを生成・再生成
ayumy sync --report --date 2026-03-01..2026-03-05               # 日付範囲のレポートを一括生成
```

`ayumy sync` は `bin/ayumy` CLI を通じて `sync_session.sh` を呼び出す。`bin/ayumy` はサブコマンドをディスパッチするエントリポイントであり、クライアントマシンのセットアップ時に PATH に追加する（例: `export PATH="$HOME/ayumy/bin:$PATH"`）。手動実行時はフォアグラウンドで実行し、転送結果を標準出力に表示する。`--report` 指定時は Lambda の実行結果も標準出力に表示する。

### 4.6 セキュリティに関する注意
- JSONL には会話の生データが含まれるため、会話中やツール実行時に機密情報（API キー、パスワード等）をログに残さないよう注意する
- S3 バケットはパブリックアクセスブロックを有効化し、IAM ポリシーで自アカウントのみにアクセスを制限する
- S3 のサーバーサイド暗号化（SSE-S3）を有効化する
- 必要に応じて特定プロジェクトを除外するフィルタリング機能を設ける

## 5. フェーズ 2: データ統合・要約・Notion 書き込み
### 5.1 GitHub アクティビティの取得
対象期間は実行方式によって異なる。

| 実行方式 | 対象期間 |
|---|---|
| 定期実行（EventBridge） | 前日 JST 00:00:00 〜 当日 JST 00:00:00 |
| 手動実行（`ayumy sync --report`） | 当日 JST 00:00:00 〜 現在時刻 |
| 日付指定（`ayumy sync --report --date DATE`） | 指定日 JST 00:00:00 〜 翌日 JST 00:00:00（各日付ごと） |

- Lambda event の `source` フィールドで実行方式を判定する（`"manual"` → 手動、それ以外 → 定期）
- `target_date` フィールドが指定されている場合は `source` に関わらずその日付の全日範囲を対象とする
- `target_date` は `YYYY-MM-DD` または `YYYY-MM-DD..YYYY-MM-DD` 形式。範囲指定時は各日付に対して順にレポートを生成する
- `target_date` 指定時は backfill（未レポート日の自動検出）をスキップし、指定された日付のみを処理する

対象リポジトリは S3 上のセッションログから特定する。各プロジェクトディレクトリの `.ayumy_repo` メタデータファイルからリポジトリ名を読み取り、そのリポジトリのみ `GET /repos/{owner}/{repo}` で取得する。

| アクティビティ | エンドポイント | フィルタ | 取得項目 |
|---|---|---|---|
| Commits | `GET /search/commits` | `repo:{full_name} author-date:{since_date}..{until_date}` | メッセージ、作成者、日時、SHA、URL |
| Pull Requests | `GET /repos/{owner}/{repo}/pulls` | `state=all`, `sort=updated`, 前日以降 | タイトル、番号、状態、作成者、ラベル、draft フラグ、URL、作成日時、merge 日時、close 日時 |
| Issues | `GET /repos/{owner}/{repo}/issues` | `since`, `state=all`, PR を除外 | タイトル、番号、状態、作成者、ラベル、URL、作成日時、close 日時、close 理由（state_reason） |

Commits の取得には Search Commits API を使用し、`author-date` の range 構文（`YYYY-MM-DD..YYYY-MM-DD`）で期間を指定する。検索範囲の上限は `max(since_date, (until - 1day).date())` で算出し、不要な翌日分のページングを回避する。Search API は日付精度のみをサポートするため、取得後に `since <= author_date < until` で post-filter し、手動実行時の部分日（当日 00:00 〜 現在時刻）にも対応する。これによりブランチの存在有無にかかわらず対象期間のコミットを取得できる。ただし squash merge によって `author-date` が書き換えられたコミットは検出できないため、セッション JSONL の `tool_result` から抽出したコミット情報で補完する（§5.3 参照）

Search API には 30 リクエスト/分の secondary rate limit がある。10 リクエストごとに経過時間をチェックし、20 秒のウィンドウ内であれば残り時間だけ sleep してからカウンタをリセットする

#### 5.1.1 Backfill 時の Hybrid 取得経路
PR/Issue の `updated_at` 経路は対象日以降に状態が更新されると `updated_at` がウィンドウから外れて取得対象から漏れる。例えば T 日に open された PR が T+1 日に merge された場合、T 日の再生成では PR が取得できず Timeline に「PR opened」イベントが現れない

通常運用（前日定期実行・手動当日実行）では影響軽微なため `updated_at` 経路を維持する。`target_date` 指定時、または `scan_backfill_dates` で検出された未レポート日に対しては Hybrid 経路に切り替える

| アクティビティ | 取得経路 |
|---|---|
| Pull Requests | `GET /search/issues` を `is:pr` + `created:`/`merged:`/`closed:` のレンジクエリで3回呼び出し、状態遷移した PR を取得する。さらに `fetch_commits` 結果の各 SHA に対して `GET /repos/{owner}/{repo}/commits/{sha}/pulls` を呼び、対象日にコミットだけがあった PR も補足する。両者を PR 番号で union し、各番号を `GET /repos/{owner}/{repo}/pulls/{N}` で個別取得する |
| Issues | `GET /search/issues` を `is:issue` + `created:`/`closed:` のレンジクエリで2回呼び出し、状態遷移した Issue を取得する。番号で union する |

Search クエリの日付範囲は UTC/JST の境界ずれを吸収するため `since - 1day` 〜 `until` まで広げる。取得後に各イベントタイムスタンプ（`created_at` / `merged_at` / `closed_at`）が `[since, until)` に入るかで post-filter する。commit 由来 PR は対象日にコミットが存在する事実をもって採用するため post-filter の対象外とする。削除済み PR/Issue は 404 となるためスキップする

Search 呼び出しはリポジトリあたり最大 5 回（PR 3 + Issue 2）増えるため、`fetch_commits` の Search 呼び出しと共通の throttle カウンタで管理する

### 5.2 Claude Code セッションログの読み取り
DynamoDB の `ayumy-sessions` テーブルから対象日付をパーティションキーとして Query し、セッションメタデータを取得する。結果をリポジトリ別にグルーピングし、各リポジトリ内のセッションを `start_time` 順にソートする。

バックフィル検出は DynamoDB の Scan で行う。`reported_at` が未設定、または `updated_at > reported_at` のアイテムが存在する過去日付を対象とする（最大3日分）。セッションが更新された場合は `updated_at` が `reported_at` を超えるため、自動的に再生成対象となる

### 5.3 DynamoDB へのセッション書き込み
レポート生成の前処理として、S3 上の未アーカイブ JSONL をパースし、セッションメタデータを DynamoDB に書き込む。日付フィルタなしで全エントリを処理し、JST 日付ごとにグルーピングする。

**テーブル設計**（テーブル名: `ayumy-sessions`、オンデマンドモード、PITR 有効）

| Key | Attribute | Type | 説明 |
|---|---|---|---|
| PK | `date` | String | JST 日付（`YYYY-MM-DD`） |
| SK | `repo#session_id` | String | リポジトリ名 + セッション ID |
| | `repo` | String | リポジトリ名 |
| | `project` | String | プロジェクトディレクトリ名 |
| | `start_time` | String | ISO 8601 |
| | `end_time` | String | ISO 8601 |
| | `user_messages` | List | ユーザーメッセージ |
| | `tools_used` | List | 使用ツール |
| | `session_commits` | List | セッション中の git commit 結果（`[{sha, message, timestamp}]`、未検出時は空リスト） |
| | `updated_at` | String | ISO 8601、書き込み・更新時刻 |
| | `reported_at` | String | ISO 8601、レポート生成時刻（未生成時は未設定） |

書き込み時の動作:

- 同一キー（PK + SK）のアイテムは上書きされる（冪等性を担保）
- ユーザーメッセージも `session_commits` もないグループはスキップする
- リポジトリ名は `.ayumy_repo` メタデータファイルから解決する。メタデータがないプロジェクトはスキップする
- 書き込み成功後、処理した JSONL を S3 から削除する。書き込み失敗時は S3 を削除せず、次回実行時に再試行する
- 書き込み失敗時も DynamoDB に前回成功分のデータが残っているため、レポート生成フローは継続する

レポート生成後の動作:

- 対象日付の全アイテムの `reported_at` を現在時刻に更新する
- これにより `scan_backfill_dates` が同じ日を再検出しなくなる

### 5.4 要約生成（Claude API）
使用モデル: `claude-sonnet-4-20250514`

GitHub アクティビティと Claude Code セッションログの両方をコンテキストとして渡し、リポジトリごとの要約を生成する。文体は常体で統一し、ですます調は使用しない。

- **リポジトリ別の要点**: 各リポジトリで行われた作業の要点を 2〜5 項目の箇条書きで記述する。最初の項目はそのリポジトリの最重要の要点として単独でも通じる内容にする（Slack 通知ではこの項目を 1 文サマリとして流用する）
- **Claude Code での作業**: 上記の要点の中に Claude Code セッションでの相談・実装方針の検討内容も含めて構わない
- PR/Issue のステータス別一覧と時系列のイベントは Notion 本文の生成時にプログラムで組み立てるため、Claude API の出力には含めない

入力フォーマット:

```
以下は {日付} の GitHub アクティビティおよび Claude Code での作業記録です。
日本語で簡潔に要約してください。

---
# GitHub アクティビティ
## {リポジトリ名}
### Commits
- {コミットメッセージ}
### Pull Requests
- [merged] #12 機能Aの追加
### Issues
- [closed] #8 バグ修正

---
# Claude Code セッション
## プロジェクト: {project-name}
### セッション 1 (14:00 - 15:30)
- ユーザー: 認証機能のリファクタリングについて相談
- ツール使用: ファイル編集 (auth.ts, middleware.ts)
```

出力にはリポジトリごとの作業要点（箇条書き）とタグの提案を含める。

### 5.5 Slack 通知
Notion への書き込み完了後、Slack Incoming Webhook で指定チャンネルに通知を送信する。

通知内容:
- Notion ページへのリンク（リポジトリごとに 1 行）。Claude API が生成した summary 箇条書きの先頭項目がある場合は 1 文サマリとしてリンクの後ろに付加する
- 実行メトリクス: ayumy バージョン、経過時間（Lambda 実行時は timeout との比率）、ピークメモリ（Lambda 実行時は memory limit との比率）

アクティビティが 0 件で Notion ページが作成されなかった場合は、正常稼働を示す簡易通知を送信する。処理中にエラーが発生した場合もエラー内容を通知する。

通知が失敗しても処理全体は正常終了とする（通知はベストエフォート）。

### 5.6 処理済み JSONL の削除
DynamoDB への書き込みが正常に完了した後、処理した JSONL ファイルを S3 から削除する。削除対象は `ingest` で処理したオブジェクトキーに限定し、処理中に到着した遅延ファイルが誤って削除されるのを防ぐ。セッションデータは DynamoDB に永続化されているため、JSONL の保持は不要

## 6. Notion データベース仕様
### 6.1 データベースプロパティ
Date × Repository 単位でページを作成する。1日に複数ページが生成される。再実行時は対象日の既存ページをアーカイブ（soft-delete）してから再作成し、冪等性を担保する。GitHub activity に存在しないリポジトリは Notion ページを作成しない。

| プロパティ名 | 型 | 説明 | 例 |
|---|---|---|---|
| Name | Title | 日付とリポジトリ名 | `26-03-01: ayumy` |
| Date | Date | 対象日 | `2025-03-01` |
| Repository | Select | リポジトリ名 | `ayumy` |
| Tags | Multi-select | 作業内容の分類タグ | `feature`, `productive` |
| Commits | Number | リポジトリのコミット数 | `5` |
| Merged | Number | リポジトリのマージ PR 数 | `2` |
| Closed | Number | リポジトリのクローズ Issue 数 | `1` |
| Sessions | Number | リポジトリのセッション数 | `3` |
| Version | Text | レポート生成時の ayumy バージョン | `0.2.0` |

### 6.2 ページ本文（children blocks）
Notion ページの本文は Summary、ステータス別セクション、Timeline で構成する。Summary は Claude API が生成し、それ以外は GitHub アクティビティから決定論的に組み立てる。ブロックタイプは `heading_2`、`bulleted_list_item`、`table` を使い分ける。

```
[heading_2]            Summary
[bulleted_list_item]   リポジトリの作業要点（2〜5項目）
[heading_2]            Done（該当がある場合のみ）
[bulleted_list_item]   マージ済み・クローズ済み PR、クローズ済み Issue
[heading_2]            In Progress（該当がある場合のみ）
[bulleted_list_item]   作業中の PR や Issue（draft PR を含む）
[heading_2]            Todo（該当がある場合のみ）
[bulleted_list_item]   対象日に新規作成された Issue（バックログ）
[heading_2]            Timeline（該当がある場合のみ）
[table]                Time / Type / Detail の3列で commit・PR・Issue のイベントを時系列順に列挙
```

ステータスの振り分け基準:

- Done: マージ済み PR、クローズ済み（unmerged）PR、クローズ済み Issue
- In Progress: オープン PR（draft 含む）、対象日より前に作成されたオープン Issue
- Todo: 対象日に新規作成され、まだオープンの Issue

Done セクションの各項目には状態を示す prefix を付ける。通常完了したものには `✅ `、イレギュラーな完了には `⚠️ (理由) ` を付け、後者は以下を区別する:

- マージされず close された PR: `⚠️ (closed) `
- `not_planned` で close された Issue: `⚠️ (not planned) `
- `duplicate` で close された Issue: `⚠️ (duplicate) `

Timeline には commit と、PR/Issue のうち対象日のウィンドウ内で発生した状態遷移（opened / merged / closed）を 1 行ずつ表に記録する。同じ PR/Issue が同日に opened と merged の両方を行った場合は別行で記載する。

GitHub アイテムへのリンクは PR/Issue が `repo#xx: Title`、commit が `{sha-prefix}: {commit message}` の形式とし、それぞれ GitHub URL でリンク化する。

### 6.3 タグの分類基準
| タグ | 基準 |
|---|---|
| `feature` | 新機能追加に関する Commit / PR |
| `bugfix` | バグ修正に関する Commit / PR / Issue |
| `docs` | ドキュメント更新 |
| `refactor` | リファクタリング |
| `ci` | CI/CD やビルド設定の変更 |
| `review` | PR レビューが主な活動だった場合 |
| `productive` | 複数の PR マージや Issue クローズがある |
| `maintenance` | 依存関係更新、CI 修正など保守作業が中心 |
| `blocked` | PR レビュー待ちや Issue の議論が中心 |
| `light` | アクティビティが少ない日 |

タグは Claude API の要約生成時に自動判定させる。

## 7. AWS Lambda の構成
### 7.1 実行方式
レポート生成は AWS Lambda で実行する。日次の定期実行に加え、`ayumy sync --report` による手動実行にも対応する。

**定期実行（EventBridge Scheduler）**
毎日 JST 00:00（UTC 15:00）に EventBridge Scheduler が Lambda 関数を呼び出す。

**手動実行**
```bash
ayumy sync --report                    # クライアントマシンから（S3 転送 + Lambda 実行）
ayumy sync --report --date 2026-03-25  # 指定日のレポートを生成・再生成
ayumy sync --report --date 2026-03-01..2026-03-05  # 日付範囲のレポートを一括生成
```
- `aws lambda invoke --invocation-type Event` で Lambda 関数を `{"source": "manual"}` ペイロード付きで非同期呼び出しする
- `--date` 指定時はペイロードに `"target_date"` を追加する（`"YYYY-MM-DD"` または `"YYYY-MM-DD..YYYY-MM-DD"`）
- 対象期間の判定は §5.1 に従う
- 実行結果は Slack 通知で確認する

### 7.2 環境変数
Lambda 関数の環境変数として設定する。機密情報は AWS Secrets Manager に保管し、Lambda から参照する。

**Lambda 環境変数**
| 環境変数 | 説明 |
|---|---|
| `AYUMY_S3_BUCKET` | セッションログの保管先 S3 バケット名 |
| `AYUMY_DYNAMO_TABLE` | セッションメタデータの DynamoDB テーブル名 |
| `AYUMY_LAMBDA_TIMEOUT` | Lambda 関数の timeout 秒数（template.yaml の `LambdaTimeoutSeconds` パラメータと連動） |
| `NOTION_DATABASE_ID` | 書き込み先の Notion データベース ID |

**Secrets Manager に保管**
| シークレット | 説明 |
|---|---|
| `GITHUB_PAT` | GitHub Fine-grained PAT（全 owner リポジトリへの read 権限） |
| `ANTHROPIC_API_KEY` | Anthropic API キー |
| `NOTION_SECRET` | Notion Internal Integration トークン |
| `SLACK_WEBHOOK_URL` | Slack Incoming Webhook URL |

### 7.3 Lambda 関数の構成
- **ランタイム**: Python 3.12
- **ハンドラ**: `lambda/handler.py`（`lambda/report` パッケージを呼び出すエントリポイント）
- **タイムアウト**: 300秒（5分）
- **メモリ**: 512MB
- **依存パッケージ**: デプロイ: `requests`, `anthropic`, `PyGithub`（`boto3` は Lambda ランタイム同梱版を利用）、開発: 左記 + `boto3`
- **IAM ロール**: S3 バケットへの読み書き、DynamoDB テーブルへの読み書き、Secrets Manager の読み取り、CloudWatch Logs への書き込み

### 7.4 デプロイ
AWS SAM（`template.yaml`）で Lambda 関数、EventBridge Scheduler、IAM ロール、S3 バケット、DynamoDB テーブルを管理する。

```bash
sam build && sam deploy
```

## 8. 運用上の考慮事項
### 8.1 ネットワーク要件
- クライアントマシンからインターネットへのアクセス（S3 への転送、Lambda の呼び出し）
- 外出先からも転送可能（VPN 不要）

### 8.2 API レートリミット
- GitHub API: 認証済みで 5,000 リクエスト/時
- Anthropic API: プランに応じたレートリミットあり（1日数回程度なら問題なし）
- Notion API: 3 リクエスト/秒（1ページの書き込みのみであるため問題なし）

### 8.3 エラーハンドリング
- API 呼び出し失敗時のリトライ処理
- アクティビティが0件の日はスキップまたは「活動なし」と記録
- post-commit hook は必ず exit 0（commit をブロックしない）
- S3 転送失敗時、JSONL はソース側に残るため次回転送時にリトライ可能
- AWS 認証情報が無効な場合も hook は正常終了し、認証修正後に `ayumy sync --all` で補完可能

### 8.4 ランニングコスト見積もり
課金が発生するのは Anthropic API と AWS。GitHub API と Notion API は無料枠内で収まる。

**Anthropic API（`claude-sonnet-4-20250514`）**
- 入力: $3 / 1M tokens、出力: $15 / 1M tokens

**1日あたりのトークン使用量（目安）**
| 項目 | トークン数 |
|---|---|
| 入力（プロンプト + GitHub アクティビティ + JSONL 抽出データ） | ~10,000 |
| 出力（構造化された日本語要約） | ~1,500 |

**コスト概算**
| 期間 | コスト |
|---|---|
| 1日 | ~$0.05（入力 $0.03 + 出力 $0.02） |
| 1ヶ月 | ~$1.5 |
| 1年 | ~$18 |

※ セッションログが大量にある日はトークン数が増加する。上記は平均的な開発日の見積もり。

**AWS**
| サービス | 概算 |
|---|---|
| Lambda | 無料枠内（月100万リクエスト、1日1〜数回の実行） |
| S3 | 月数円（年間 1〜2 GB 程度） |
| EventBridge Scheduler | 無料枠内 |
| Secrets Manager | ~$0.40/月（シークレット4件） |

### 8.5 ストレージ管理
- セッションログは S3 経由で DynamoDB に永続化し、クライアントマシンのディスクを消費しない
- DynamoDB 書き込み後に S3 上の JSONL は削除されるため、S3 ストレージの増加は一時的
- 実行ログは CloudWatch Logs に出力し、保持期間を設定して管理する

## 9. 将来の拡張案
- **クライアントマシン側の定期自動同期**: cron で `ayumy sync --all` を定期実行し、手動同期の手間を省く
- **複数クライアントマシン対応**: 競合解決（ファイル名にホスト名を含める等）
- **週次・月次レポート**: 日次データを集約した定期サマリー
- **ダッシュボード**: Notion データベースのビューを活用した可視化
- **claude.ai の会話記録**: データエクスポート機能との連携
- **過去日の再処理**: 日付を指定して再実行できるオプション
