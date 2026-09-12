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
  - [ページアイコン](#ページアイコン)
  - [ページ本文](#ページ本文)
- [Troubleshooting](#troubleshooting)
  - [pre-push hook の再設置](#pre-push-hook-の再設置)
  - [AWS 認証切れからの復旧](#aws-認証切れからの復旧)
  - [過去日レポートの再生成](#過去日レポートの再生成)
  - [Slack 通知の読み方](#slack-通知の読み方)
  - [CloudWatch Logs の確認](#cloudwatch-logs-の確認)

## Overview
- git push と日次の定期実行を起点に、GitHub アクティビティと Claude Code session を集約して Notion にレポートを書き込む。レポート生成は `ayumy sync --report` でも起動できる
- クライアントマシンと AWS Lambda の 2 段階で動作し、運用者が普段触れるのはクライアントマシン側のみ
- クライアント側のスクリプト（`scripts/`, `hooks/`, `bin/ayumy`）は macOS のみサポート

## Daily Operations
各コマンドは `--help`（または `-h`）で使い方を確認できる

### pre-push hook による自動転送
- `ayumy setup-hooks` で設置した pre-push hook が、各リポジトリの push を契機に未同期 session を S3 に転送する
- 転送が終わるまで push は完了しない。AWS 認証切れ等の場合は転送に失敗して push は中止される
- 対応する Claude session が存在しないリポジトリでは hook は何もせず通常通り push を通す

### 手動同期
push せずに session だけ転送したいときや、hook を経由しないタイミングで同期したいときに使う

| Command | Behavior |
|---|---|
| `ayumy sync` | 現在のディレクトリに対応するプロジェクトを同期 |
| `ayumy sync --all` | 全プロジェクトの未同期分を一括同期 |
| `ayumy sync --project <name>` | 特定プロジェクト（`~/.claude/projects/` 配下のディレクトリ名）を同期 |

### 手動レポート生成
session 転送に続けてレポート生成まで走らせるときや、既存日付のレポートを作り直すときに使う

| Command | Behavior |
|---|---|
| `ayumy sync --report` | 同期後に当日分のレポートを生成 |
| `ayumy sync --all --report` | 全プロジェクト同期 + レポート生成 |
| `ayumy sync --report --date 2026-03-25` | 指定日のレポートを生成・再生成 |
| `ayumy sync --report --date 2026-03-01..2026-03-05` | 日付範囲を一括生成 |

`--report` 指定時の Lambda 呼び出しは非同期のため、コマンド自体はすぐ完了する。結果は Slack 通知で確認する。実行方式別の対象期間は [Spec: GitHub Activity Fetch](Spec.md#github-activity-fetch) を参照

## Config
- 使用する Claude モデルなど各種設定は `lambda/config/config.yml` で変更できる
- 編集後は `make lambda-deploy` で Lambda に反映する
- ayumy を更新して設定項目が増えたときは、`make config-diff` でデフォルトの設定と自分の `config.yml` を見比べ、増えた項目を書き足す

## Reading Notion Reports
Notion ページは 1 日 × リポジトリ単位で作られる。GitHub アクティビティが 0 件のリポジトリには作られない

### ページプロパティ
日付・リポジトリ名・タグ・Commits / Merged / Closed / Sessions の件数などが Properties に入る。プロパティの一覧は [Spec: Database Properties](Spec.md#database-properties) を参照

### ページアイコン
ページのアイコンはリポジトリごとに設定できる。`ayumy setup-hooks` の実行時に尋ねられる。後から変えるときは `lambda/config/config.yml` の `notion.repository_icons` を書き換える。どちらも `make lambda-deploy` を実行するまで反映されない

### ページ本文
- **Summary** — Claude API が生成したリポジトリの作業要点（2〜5 項目）
- **Done / In Progress / TODO** — PR / Issue をステータス別に列挙。Done に載るのは対象日にマージ・クローズされたものだけで、別の日に完了したものはそちらの日のページに載る。該当が無いセクションは表示しない
- **Timeline** — 対象日の作業が時系列で並ぶ
  - PR ブロックの配下にその PR の commit がネストされる。配下の最後の行は `🔻` が付いた merge commit で、その PR がマージされたことを示す
  - main commit や Issue の open / close は最上位に並ぶ

Summary 以外の各行の冒頭には以下の記号が付く。ステータスの振り分け方は [Spec: Page Body](Spec.md#page-body) を参照

| Symbol | Meaning |
|---|---|
| `🟢` / `🟣` / `🔴` | PR の open / merged / closed |
| `🟩` / `🟪` / `⬜` | Issue の open / close / close（not planned・duplicate） |
| `🔸` / `🔻` | commit / merge commit |

## Troubleshooting
### pre-push hook の再設置
hook が動いていないときや、別のパスから設置し直したいときは、対象のリポジトリで `ayumy setup-hooks` を実行する

`ayumy setup-hooks` は過去に同コマンドで設置された旧 `post-commit` symlink を併せて除去する。ただし手動 `ln` で別のパス表記により設置した hook は対象外のため、自分で削除する

### AWS 認証切れからの復旧
転送に失敗して push が止まるときは、AWS の認証切れであることが多い。その場合は `aws login` で認証を更新して push し直す。転送できなかった session は手元に残るため、次の転送でまとめて送られる

### 過去日レポートの再生成
[手動レポート生成](#手動レポート生成) の `--date` 指定で再生成する。転送済みの session は AWS 側に保存されているため再転送は不要。Notion 側は同日の既存ページをアーカイブしてから再作成する

作り直した回数は新しいページの `Regens` に残る。初回の生成が `0` で、再生成のたびに 1 ずつ増える

### Slack 通知の読み方
Notion への書き込みが終わると、以下の情報が Slack に届く

- リポジトリごとの Notion ページリンク
- 実行メトリクス: バージョン、経過時間、ピークメモリ
- Claude API コスト: 今回の実行の利用金額、当月累計、当月の Claude API 呼び出し回数。月次項目には前月同期間との差分（`MoM ±X%`）を並記し、前月データが無く計算できない項目では `(MoM ...)` を表示しない

処理中にエラーが出たり、タイムアウトに近づいて処理を途中で打ち切った場合も通知される（次回実行時に再試行）。アクティビティが 0 件のときはデフォルトで通知されない（`config.yml` で変更可能）。Lambda が異常終了した場合や、エラーが ayumy から通知されなかった場合は、代わりに Amazon Q から通知が届く

見出しの末尾には、その通知がどの実行によるものかを示すラベルが付く

| Label | Meaning |
|---|---|
| `[manual]` | `ayumy sync --report` による手動実行 |
| `[backfill]` | `--date` 指定の実行、または未報告日の自動検出 |

日次の定期実行で通常どおり生成された通知にはラベルが付かない。両方に該当する場合は `[manual] [backfill]` と並ぶ

Claude Code で作業したが commit / push まで進まなかったリポジトリは Notion ページが作成されず、Slack 通知にリポジトリ名だけが表示される

- 全リポジトリが該当するときは Daily Report の代わりに専用の簡易通知が届く（`config.yml` で変更可能）
- 一部リポジトリのみ該当するときは通常の Daily Report の下部にリポジトリ名が並ぶ
- 後日 push で追いつけば、その日のレポートは自動で再生成される

### CloudWatch Logs の確認
Lambda の詳細ログは CloudWatch Logs に出力される。Slack 通知だけでは分からないエラー原因や API レスポンスを確認したいときに参照する
