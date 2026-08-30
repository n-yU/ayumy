# Manual
Ayumy の構築後の日常運用ガイド。基本的な操作・Notion レポートの読み方・トラブル時の対処をまとめる。要件・仕様の詳細は [Spec.md](Spec.md) を参照

- [Overview](#overview)
- [Daily Operations](#daily-operations)
  - [pre-push hook による自動転送](#pre-push-hook-による自動転送)
  - [手動同期](#手動同期)
  - [手動レポート生成](#手動レポート生成)
- [Config](#config)
- [Reading Notion Reports](#reading-notion-reports)
  - [ページプロパティ](#ページプロパティ)
  - [ページ本文](#ページ本文)
- [Troubleshooting](#troubleshooting)
  - [pre-push hook の再設置](#pre-push-hook-の再設置)
  - [AWS 認証切れからの復旧](#aws-認証切れからの復旧)
  - [過去日レポートの再生成](#過去日レポートの再生成)
  - [Slack 通知の読み方](#slack-通知の読み方)
  - [CloudWatch Logs の確認](#cloudwatch-logs-の確認)

## Overview
- git push と日次の定期実行を起点に、GitHub アクティビティと Claude Code session を集約して Notion にレポートを書き込む。レポート生成は `ayumy sync --report` でも起動できる
- クライアントマシンと AWS Lambda の 2 フェーズで動作し、運用者が普段触れるのはクライアントマシン側のみ
- クライアント側のスクリプト（`scripts/`, `hooks/`, `bin/ayumy`）は macOS のみサポート

## Daily Operations
各コマンドは `--help` (または `-h`) で使い方を確認できる

### pre-push hook による自動転送
- `ayumy setup-hooks` で設置した pre-push hook が、各リポジトリの push を契機に未同期 session を S3 に転送する
- 転送はフォアグラウンドで実行され、失敗時は非ゼロ終了で push を中止する。AWS 認証切れなどの障害は push 時点で顕在化する
- 対応する Claude session が存在しないリポジトリでは hook は何もせず通常通り push を通す

### 手動同期
push を伴わずに session だけ転送したい場合、または hook を経由しないタイミングで同期したい場合に使う

| Command | Behavior |
|---|---|
| `ayumy sync` | 現在のディレクトリに対応するプロジェクトを同期 |
| `ayumy sync --all` | 全プロジェクトの未同期分を一括同期 |
| `ayumy sync --project <name>` | 特定プロジェクト（`~/.claude/projects/` 配下のディレクトリ名）を同期 |

### 手動レポート生成
session 転送に続けてレポート生成まで走らせたい場合や、既存日付のレポートを再生成したい場合に使う

| Command | Behavior |
|---|---|
| `ayumy sync --report` | 同期後に当日分のレポートを生成 |
| `ayumy sync --all --report` | 全プロジェクト同期 + レポート生成 |
| `ayumy sync --report --date 2026-03-25` | 指定日のレポートを生成・再生成 |
| `ayumy sync --report --date 2026-03-01..2026-03-05` | 日付範囲を一括生成 |

`--report` 指定時の Lambda 呼び出しは非同期のため、コマンド自体はすぐ完了する。実行結果は Slack 通知で確認する。実行方式別の対象期間は [Spec: GitHub Activity Fetch](Spec.md#github-activity-fetch) を参照

## Config
- 使用する Claude モデルなど各種設定は [lambda/config/config.yml](../lambda/config/config.yml) で変更できる
- 編集後は `make lambda-deploy` で Lambda に反映する

## Reading Notion Reports
1 日 × リポジトリ単位で Notion ページが作成される。GitHub アクティビティが 0 件のリポジトリにはページは作成されない

### ページプロパティ
日付・リポジトリ名・タグ・Commits / Merged / Closed / Sessions の件数などが Properties に入る。プロパティの一覧は [Spec: Database Properties](Spec.md#database-properties) を参照

### ページアイコン
ページのアイコンはリポジトリごとに設定できる。`ayumy setup-hooks` の実行時に尋ねられ、後から変えたい場合は `lambda/config/config.yml` の `notion.repository_icons` を書き換える。いずれも `make lambda-deploy` を実行するまで反映されない

### ページ本文
- **Summary** — Claude API が生成したリポジトリの作業要点（2〜5 項目）
- **Done / In Progress / TODO** — PR / Issue をステータス別に列挙。Done に載るのは対象日にマージ・クローズされたものだけで、別の日に完了したものはそちらの日のページに載る。該当が無いセクションは表示しない
- **Timeline** — 対象日の作業を時系列で並べる。PR ブロックは `🔀` を冒頭に置き、配下にその PR の commit をネストする。merge commit や直接 commit、Issue の open / close は最上位に `🔸` / `🟢` / `✅` などの prefix 付きで並ぶ

ステータス振り分けやイレギュラーな完了（unmerged close / not_planned / duplicate）の prefix 規則は [Spec: Page Body](Spec.md#page-body) を参照

## Troubleshooting
### pre-push hook の再設置
hook が動いていない、または別パスから設置し直したい場合は、対象のリポジトリで `ayumy setup-hooks` を実行する

`ayumy setup-hooks` は過去に同コマンドで設置された旧 `post-commit` symlink を併せて除去する。手動 `ln` で別パス表記により設置された legacy hook は対象外で、手で削除する

### AWS 認証切れからの復旧
push 時の転送失敗で push が中止される場合、AWS の認証切れであることが多い。`aws login` で認証を更新してから再度 push する。S3 に未転送の JSONL はソース側に残るため、認証復旧後の次回転送でリトライされる

### 過去日レポートの再生成
[手動レポート生成](#手動レポート生成) の `--date` 指定で再生成する。session は DynamoDB に永続化されているため再転送は不要。Notion 側は同日の既存ページをアーカイブしてから再作成する

### Slack 通知の読み方
Notion への書き込み完了後、以下の情報が Slack に届く。アクティビティが 0 件の日や処理中にエラーが出た場合も内容を反映した通知が届く

- リポジトリごとの Notion ページリンク
- 実行メトリクス: バージョン、経過時間、ピークメモリ
- Claude API コスト: 今回の実行の利用金額、当月累計、当月の Claude API 呼び出し回数。月次項目には前月同期間との差分（`MoM ±X%`）を並記し、前月データが無く計算できない項目では `(MoM ...)` を表示しない

見出しの末尾には、その通知がどの実行によるものかを示すラベルが付く

| Label | Meaning |
|---|---|
| `[manual]` | `ayumy sync --report` による手動実行 |
| `[backfill]` | `--date` 指定の実行、または未報告日の自動検出 |

日次の定期実行で通常どおり生成された通知にはラベルが付かない。両方に該当する場合は `[manual] [backfill]` と並ぶ

Claude Code で作業したが commit / push まで進まなかったリポジトリは Notion ページが作成されず、Slack 通知にリポジトリ名だけが表示される

- 全リポジトリが該当する日は Daily Report の代わりに専用の簡易通知が届く
- 一部リポジトリのみ該当する日は通常の Daily Report の下部にリポジトリ名が並ぶ
- 後日 push で追いつけば、その日のレポートは自動で再生成される

### CloudWatch Logs の確認
Lambda の詳細ログは CloudWatch Logs に出力される。Slack 通知だけでは分からないエラー原因や API レスポンスを確認したい場合に参照する
