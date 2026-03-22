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
本システムは2フェーズで構成される。セッションログは S3 バケットに保管し、レポート生成は AWS Lambda で実行する。

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
    ├─→ JSONL + GitHub API → Claude API で要約生成
    ├─→ Notion API で記録
    ├─→ Slack Webhook で通知
    └─→ 処理済み JSONL を processed/ に移動

[S3]
  s3://{bucket}/
  └── claude-sessions/ に JSONL 蓄積
```

### 3.2 使用する外部サービス・API
| サービス | 用途 | 認証方式 |
|---|---|---|
| GitHub API (REST) | アクティビティデータの取得 | Fine-grained PAT |
| Anthropic API | 自然言語による要約生成 | API Key |
| Notion API | 作業記録の書き込み | Internal Integration Token |
| Slack Incoming Webhook | 完了通知 | Webhook URL |
| AWS S3 | セッションログの保管 | AWS 認証情報（IAM ユーザー / プロファイル） |
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
│   │   └── summarizer.py            # Claude API 要約生成
│   ├── requirements.txt             # Lambda デプロイ用の依存パッケージ
│   └── requirements-dev.txt         # ローカル開発用の依存パッケージ（boto3 を含む）
├── template.yaml                    # AWS SAM テンプレート（Lambda, EventBridge, IAM ロール, S3 バケット）
├── docs/
│   ├── Setup.md
│   ├── Spec.md
│   └── Development.md
├── CLAUDE.md
└── README.md
```

**S3 バケット**

```
s3://{bucket}/
├── claude-sessions/                  # クライアントマシンから転送された JSONL
│   ├── {project-name}/
│   │   └── {session-id}.jsonl
│   └── ...
└── processed/                        # 処理済み JSONL（無期限保持）
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

転送対象のセッションは、マーカーファイル（`.ayumy_last_sync`）との mtime 比較で決定する。

- マーカーが存在しない場合（初回）は全 JSONL を対象とする
- スキャン前に一時マーカー（`.ayumy_last_sync.tmp`）を作成し、転送成功後に `mv` で本マーカーに昇格させる
- これにより、転送中に更新されたファイルが次回検出漏れしないようにする

### 4.3 転送スクリプト（`sync_session.sh`）
hook と手動実行の両方から呼ばれる共通スクリプト。

```
sync_session.sh [--project <project-name>] [--all] [--background] [--report]
```

| オプション | 動作 |
|---|---|
| `--project <name>` | 指定プロジェクトの差分セッションのみ転送。`<name>` は `~/.claude/projects/` 以下のディレクトリ名（例: `-Users-username-Documents-github-repo`） |
| `--all` | 全プロジェクトから差分セッションを一括転送 |
| `--background` | バックグラウンドで実行（hook 用） |
| `--report` | S3 転送後に Lambda 関数を呼び出してレポート生成を実行 |
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

Lambda event の `source` フィールドで判定する。`"manual"` なら手動実行、それ以外（EventBridge の場合は `"aws.scheduler"` 等）なら定期実行として扱う。

対象リポジトリは S3 上のセッションログから特定する。各プロジェクトディレクトリの `.ayumy_repo` メタデータファイルからリポジトリ名を読み取り、そのリポジトリのみ `GET /repos/{owner}/{repo}` で取得する。

| アクティビティ | エンドポイント | フィルタ | 取得項目 |
|---|---|---|---|
| Commits | `GET /repos/{owner}/{repo}/commits` | `since`, `until` | メッセージ、作成者、日時、SHA |
| Pull Requests | `GET /repos/{owner}/{repo}/pulls` | `state=all`, `sort=updated`, 前日以降 | タイトル、番号、状態、作成者、ラベル |
| Issues | `GET /repos/{owner}/{repo}/issues` | `since`, `state=all`, PR を除外 | タイトル、番号、状態、作成者、ラベル |

### 5.2 Claude Code セッションログの読み取り
1. S3 バケットの `claude-sessions/` プレフィックス以下の全 JSONL を走査し、最終更新日時（`LastModified`）で対象期間内のファイルを抽出する
2. 各 JSONL エントリの `timestamp`（ISO 8601 UTC）を `since` / `until` と比較し、対象期間内のメッセージのみを抽出する。タイムスタンプのないエントリはスキップする。日をまたぐセッションでは、対象期間外のメッセージが混入するのを防ぐ
3. JSONL から抽出する項目: ユーザーのプロンプト、使用したツール、対象プロジェクト名
4. プロジェクト名（S3 パス由来、例: `-Users-nyu-Documents-github-ayumy`）を GitHub activity の既知リポジトリ名と最長サフィックスマッチングで解決する。一致しない場合は元のプロジェクト名をそのまま使用する

### 5.3 要約生成（Claude API）
使用モデル: `claude-sonnet-4-20250514`

GitHub アクティビティと Claude Code セッションログの両方をコンテキストとして渡し、以下の観点で統合的な要約を生成する。

- **全体サマリー**: その日の作業全体を2〜3文で要約
- **リポジトリ別の要点**: 各リポジトリで行われた作業の概要
- **Claude Code での作業**: どのプロジェクトで何を相談・実装したか
- **主な成果・進捗**: マージされた PR、クローズされた Issue など
- **継続中の作業**: オープンな PR や Issue

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

出力には全体サマリー、リポジトリごとの作業概要、タグの提案、ステータスの判定を含める。

### 5.4 Slack 通知
Notion への書き込み完了後、Slack Incoming Webhook で指定チャンネルに通知を送信する。

通知内容:
- 全体サマリー（Claude API が生成した2〜3文の要約）
- Notion ページへのリンク

アクティビティが 0 件で Notion ページが作成されなかった場合は、正常稼働を示す簡易通知を送信する。処理中にエラーが発生した場合もエラー内容を通知する。

通知が失敗しても処理全体は正常終了とする（通知はベストエフォート）。

### 5.5 処理済み JSONL のアーカイブ
要約生成と Notion 書き込みが正常に完了した後、処理対象の JSONL ファイルを S3 上で `claude-sessions/` から `processed/` に移動（コピー＋削除）する。アーカイブ対象は `fetch_sessions` で取得したオブジェクトキーに限定し、処理中に到着した遅延ファイルが誤ってアーカイブされるのを防ぐ。移動先はプロジェクト名のサブディレクトリを維持する（例: `processed/{project-name}/{session-id}.jsonl`）。JSONL は無期限に保持し、削除しない。

## 6. Notion データベース仕様
### 6.1 データベースプロパティ
Date × Repository 単位でページを作成する。1日に複数ページが生成される。再実行時は対象日の既存ページをアーカイブ（soft-delete）してから再作成し、冪等性を担保する。GitHub activity に存在しないリポジトリは Notion ページを作成しない。

| プロパティ名 | 型 | 説明 | 例 |
|---|---|---|---|
| Name | Title | 日付とリポジトリ名 | `26-03-01: ayumy` |
| Date | Date | 対象日 | `2025-03-01` |
| Repository | Select | リポジトリ名 | `ayumy` |
| Tags | Multi-select | 作業内容の分類タグ | `feature`, `ai-assisted` |
| Status | Select | リポジトリでの進捗状態 | `productive` |
| Commits | Number | リポジトリのコミット数 | `5` |
| PRs Merged | Number | リポジトリのマージ PR 数 | `2` |
| Issues Closed | Number | リポジトリのクローズ Issue 数 | `1` |
| Claude Sessions | Number | リポジトリのセッション数 | `3` |

### 6.2 ページ本文（children blocks）
Notion ページの本文には Claude が生成した要約を記載する。ブロックタイプとして `heading_2`、`paragraph`、`bulleted_list_item` を使い分けて構造化する。

```
[paragraph]            全体サマリー（その日の作業全体の要約）
[heading_2]            概要
[paragraph]            リポジトリの作業概要
[heading_2]            成果（該当がある場合のみ）
[bulleted_list_item]   マージされた PR、クローズされた Issue 等
[heading_2]            継続中の作業（該当がある場合のみ）
[bulleted_list_item]   オープンな PR や Issue 等
[heading_2]            Claude Code（セッションがある場合のみ）
[paragraph]            Claude Code での作業概要
```

### 6.3 タグの分類基準
| タグ | 基準 |
|---|---|
| `feature` | 新機能追加に関する Commit / PR |
| `bugfix` | バグ修正に関する Commit / PR / Issue |
| `docs` | ドキュメント更新 |
| `refactor` | リファクタリング |
| `ci` | CI/CD やビルド設定の変更 |
| `review` | PR レビューが主な活動だった場合 |

タグは Claude API の要約生成時に自動判定させる。

### 6.4 ステータスの判定基準
| ステータス | 基準 |
|---|---|
| `productive` | 複数の PR マージや Issue クローズがある |
| `maintenance` | 依存関係更新、CI 修正など保守作業が中心 |
| `blocked` | PR レビュー待ちや Issue の議論が中心 |
| `light` | アクティビティが少ない日 |

ステータスも Claude API による要約時に判定させる。

## 7. AWS Lambda の構成
### 7.1 実行方式
レポート生成は AWS Lambda で実行する。日次の定期実行に加え、`ayumy sync --report` による手動実行にも対応する。

**定期実行（EventBridge Scheduler）**
毎日 JST 00:00（UTC 15:00）に EventBridge Scheduler が Lambda 関数を呼び出す。

**手動実行**
```bash
ayumy sync --report    # クライアントマシンから（S3 転送 + Lambda 実行）
```
内部的には `aws lambda invoke` で Lambda 関数を `{"source": "manual"}` ペイロード付きで同期呼び出しし、実行結果を標準出力に表示する。Lambda はこのペイロードの `source` フィールドで手動実行を判定し、当日分のアクティビティを対象とする（§5.1）。

### 7.2 環境変数
Lambda 関数の環境変数として設定する。機密情報は AWS Secrets Manager に保管し、Lambda から参照する。

**Lambda 環境変数**
| 環境変数 | 説明 |
|---|---|
| `AYUMY_S3_BUCKET` | セッションログの保管先 S3 バケット名 |
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
- **メモリ**: 256MB
- **依存パッケージ**: デプロイ: `requests`, `anthropic`, `PyGithub`（`boto3` は Lambda ランタイム同梱版を利用）、開発: 左記 + `boto3`
- **IAM ロール**: S3 バケットへの読み書き、Secrets Manager の読み取り、CloudWatch Logs への書き込み

### 7.4 デプロイ
AWS SAM（`template.yaml`）で Lambda 関数、EventBridge Scheduler、IAM ロール、S3 バケットを管理する。

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
- Notion API: 3 リクエスト/秒（1ページの書き込みのみなので問題なし）

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
- セッションログは S3 に保管し、クライアントマシンのディスクを消費しない
- 処理済み JSONL は S3 上で `processed/` に移動して無期限保持（§5.5）。年間 1〜2 GB 程度
- 実行ログは CloudWatch Logs に出力し、保持期間を設定して管理する
- 必要に応じて S3 ライフサイクルポリシーで古いデータを Glacier 等に移行可能

## 9. 将来の拡張案
- **クライアントマシン側の定期自動同期**: cron で `ayumy sync --all` を定期実行し、手動同期の手間を省く
- **複数クライアントマシン対応**: 競合解決（ファイル名にホスト名を含める等）
- **週次・月次レポート**: 日次データを集約した定期サマリー
- **ダッシュボード**: Notion データベースのビューを活用した可視化
- **claude.ai の会話記録**: データエクスポート機能との連携
- **過去日の再処理**: 日付を指定して再実行できるオプション
