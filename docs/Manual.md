# Manual
本ドキュメントでは稼働後の日常的な運用ガイドとして、基本的な操作・Notion レポートと Slack 通知の読み方・トラブル時の対処法をまとめています。セットアップ手順は [Setup.md](Setup.md) を、さらに詳しいシステムの仕様は [Spec.md](Spec.md) を参照してください

- [Overview](#overview)
- [Daily Operations](#daily-operations)
  - [自動 session 同期](#自動-session-同期)
  - [手動 session 同期](#手動-session-同期)
  - [手動レポート生成](#手動レポート生成)
  - [別リポジトリでの作業](#別リポジトリでの作業)
- [Config](#config)
- [Reading Notion Reports](#reading-notion-reports)
  - [ページプロパティ](#ページプロパティ)
  - [ページアイコン](#ページアイコン)
  - [ページ本文](#ページ本文)
- [Reading Slack Notifications](#reading-slack-notifications)
  - [通知の内容](#通知の内容)
  - [エラー時の通知](#エラー時の通知)
  - [実行ラベル](#実行ラベル)
  - [GitHub アクティビティがないリポジトリ](#github-アクティビティがないリポジトリ)
- [Troubleshooting](#troubleshooting)
  - [pre-push hook の再設置](#pre-push-hook-の再設置)
  - [AWS 認証切れからの復旧](#aws-認証切れからの復旧)
  - [過去日レポートの再生成](#過去日レポートの再生成)
  - [CloudWatch Logs の確認](#cloudwatch-logs-の確認)

## Overview
- git push と日次の定期実行をきっかけに、GitHub アクティビティと Claude Code の session を集めて Notion にレポートを書き込む。レポートは `ayumy sync --report` で手動でも生成できる
- ローカルマシンと AWS Lambda の 2 段階で動く。普段の運用で触れるのはローカルマシン側だけ

## Daily Operations
各コマンドの使い方は `--help`（または `-h`）で確認できる

### 自動 session 同期
- `ayumy setup-hooks`（[Setup §8.3](Setup.md#8-local-machine)）で設置した pre-push hook が、各リポジトリでの push をきっかけに、未同期の session を S3 に転送する
- 転送が終わるまで push は完了しない。AWS の認証切れ等で転送に失敗すると、push は中止される
- 対応する Claude Code の session がないリポジトリでは、その旨を表示したうえで通常どおり push する
- worktree で開いた session も、その worktree からの push で転送される

### 手動 session 同期
push せずに session だけを転送したいときや、hook を経由せずに同期したいときに使う

| Command | Behavior |
|---|---|
| `ayumy sync` | 現在のリポジトリ（worktree を含む）に対応するプロジェクトを同期 |
| `ayumy sync --all` | 全プロジェクトの未同期分を一括同期 |
| `ayumy sync --project <name>` | 特定プロジェクト（`~/.claude/projects/` 配下のディレクトリ名）を同期 |

### 手動レポート生成
session の転送に続けてレポートの生成まで実行したいときや、過去の日付のレポートを作り直したいときに使う

| Command | Behavior |
|---|---|
| `ayumy sync --report` | 同期後に当日分のレポートを生成 |
| `ayumy sync --all --report` | 全プロジェクト同期 + レポート生成 |
| `ayumy sync --report --date 2026-03-25` | 指定日のレポートを生成・再生成 |
| `ayumy sync --report --date 2026-03-01..2026-03-05` | 日付範囲を一括生成 |

`--report` を付けると Lambda を非同期で呼び出す。レポート生成結果は Slack に通知される。実行方法ごとの対象期間は [Spec: Target Window](Spec.md#target-window) を参照

### 別リポジトリでの作業
- 1 つの session で複数のリポジトリを扱うとレポートに正しく記録されないため、別リポジトリで作業するときは、そのリポジトリ下の新しい session で作業する
- 1 つの session のまま別リポジトリに移って作業すると、レポートは次のようになる
  - 会話の内容は、session を開いたリポジトリのレポートに入る
  - 移った先で作った commit や PR は、その日に移った先のリポジトリで session を開いていなければ、どのレポートにも載らない
- やむを得ず 1 つの session で別のリポジトリを扱うときは、Claude Code に `git -C <path>` で git を実行させない
  - `git -C` で作った commit は、session を開いたリポジトリの commit としてレポートに載り、リンク先も存在しないページになる
  - 自身の CLAUDE.md に「`git -C` を使わず、`cd` で移動してから git を実行する」等を予め記載して制御しておく

## Config
- 使用する Claude のモデル等の設定は `lambda/config/config.yml` で変更できる
- 編集した後は `make lambda-deploy` で Lambda に反映する
- Ayumy を更新して設定項目が増えたときは、`make config-diff` でデフォルトの設定と自分の `config.yml` を見比べ、増えた項目を書き足す

## Reading Notion Reports
Notion のレポートページは、日次×リポジトリごとに 1 ページ作成される。GitHub アクティビティが 0 件のリポジトリのページは作成されない

### ページプロパティ
日付、リポジトリ名、タグと、Commits / Merged / Closed / Sessions 等の件数が Properties に入る。プロパティの一覧は [Spec: Database Properties](Spec.md#database-properties) を参照

### ページアイコン
ページのアイコンはリポジトリごとに設定でき、`ayumy setup-hooks` の実行時（[Setup §8.3](Setup.md#8-local-machine)）に尋ねられる。後から変えるときは `lambda/config/config.yml` の `notion.repository_icons` を書き換える。どちらの場合も、`make lambda-deploy` を実行するまで反映されない

### ページ本文
- **Summary** — Claude API が生成したリポジトリの作業要点（2〜5 項目）
- **Done / In Progress / TODO** — PR と Issue がステータスごとに記録される
  - Done に載るのは対象日にマージ・クローズされたものだけ。別の日に完了したものは、その日のページに載る
  - 未完了の Issue は、同じリポジトリの PR の本文やコメントで Issue 番号に触れていれば In Progress に、触れていなければ TODO に載る
  - 該当するものがないセクションは表示されない
- **Timeline** — 対象日の GitHub 関連の作業が時系列に記録される
  - PR の下に、その PR の commit が入れ子で並ぶ。対象日に merge commit があれば、`🔻` を付けて最後に記録される
  - PR に紐づかない commit や、Issue の open / close は一番上の階層に記録される

Summary 以外の各行の先頭には、次の記号が付く。ステータスの振り分け方は [Spec: Page Body](Spec.md#page-body) を参照

| Symbol | Meaning |
|---|---|
| `🟢` / `🟣` / `🔴` | PR: open / merged / closed |
| `🟩` / `🟪` / `⬜` | Issue: open / close / close（not planned, duplicate） |
| `🔸` / `🔻` | commit / merge commit |

## Reading Slack Notifications
### 通知の内容
レポートの生成処理が終わると下記情報が Slack に届く。GitHub アクティビティも Claude Code の session もない日は、デフォルトでは通知されない（`config.yml` で変更できる）

- リポジトリごとの Notion ページへのリンク
- 実行メトリクス: バージョン、経過時間、ピークメモリ
- Claude API コスト: 今回の実行の利用金額、当月の累計、当月の Claude API 呼び出し回数
  - 月ごとの項目には、前月の同じ期間と比べた増減（`MoM ±X%`）が表示される
  - 前月のデータがなく計算できない項目では `(MoM ...)` は表示されない

### エラー時の通知
処理中にエラーが起きたときや、タイムアウトが近づいて処理を途中で打ち切ったときも通知され、いずれも次回の実行で再試行する。Lambda が異常終了した場合や Ayumy からエラーを通知できなかった場合は、代わりに Amazon Q（[Setup §3.7](Setup.md#3-slack)）から通知が届く

### 実行ラベル
見出しの末尾には、その通知がどの実行によるものかを示すラベルが付く。日次の定期実行で通常どおり生成された通知にはラベルが付かない

| Label | Meaning |
|---|---|
| `[manual]` | `ayumy sync --report` による手動実行 |
| `[backfill]` | `--date` 指定の実行、または未報告日の自動検出 |

### GitHub アクティビティがないリポジトリ
Claude Code で作業したものの、対象日に commit, PR, Issue 等の GitHub アクティビティがなかったリポジトリは、Notion のレポートページが作成されず、Slack 通知に `Session-only:` としてリポジトリ名だけが表示される。後日 push して GitHub に反映すると、その日のレポートが自動で作成し直される

## Troubleshooting
### pre-push hook の再設置
hook が動いていないときや、別のパスから設置し直したいときは、対象のリポジトリで `ayumy setup-hooks` を実行する

### AWS 認証切れからの復旧
転送に失敗して push が止まるときは AWS の認証切れであることが多い。その場合は `aws login` 等で認証し直してから、もう一度 push する。転送できなかった session は手元に残るため、次の転送でまとめて送られる

### 過去日レポートの再生成
[手動レポート生成](#手動レポート生成) で `--date` を指定して再生成できる。転送済みの session は AWS 側に保存されているため、転送し直す必要はない。Notion では、同じ日の既存ページをアーカイブしてから新しいページを作る。作り直した回数は新しいページの `Regens` に残る。初回の生成が `0` で、再生成のたびに 1 ずつ増える

### CloudWatch Logs の確認
Lambda の詳細なログは CloudWatch Logs に出力される。Slack 通知だけでは分からないエラーの原因や API のレスポンスを確かめたいときに活用する
