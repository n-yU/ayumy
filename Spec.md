# GitHub 日次作業記録 自動化システム 仕様書

## 1. 概要

GitHub 上で自分が owner であるすべてのリポジトリにおける日次の開発アクティビティ（Commit, Pull Request, Issue）と、Claude Code での会話記録を自動収集し、Claude API で自然言語の要約を生成したうえで、Notion データベースに記録するシステム。

## 2. 目的

- 日々の開発作業を自動的に記録・蓄積する
- Claude Code での思考・意思決定の過程も含めて記録する
- 自然言語の要約により、振り返りや共有が容易な形で残す
- 手動の日報作成を不要にする

## 3. システム構成

### 3.1 アーキテクチャ

本システムは「commit 時のセッションログ同期」と「日次の要約生成」の2フェーズで構成される。

**フェーズ 1: commit 時のセッションログ同期（Git post-commit hook）**

各リポジトリへの commit を契機に、その時点でアクティブな Claude Code セッションの JSONL を `logs` リポジトリに自動 push する。

```
[各リポジトリで git commit]
    │
    └─→ post-commit hook 発火
        ├─→ ~/.claude/projects/ からアクティブなセッションの JSONL を特定
        └─→ logs リポジトリ (private) の claude-sessions/ に commit & push
```

**フェーズ 2: 日次の要約生成・Notion 書き込み（GitHub Actions）**

```
[GitHub Actions (logs リポジトリ, cron 毎日1回)]
    │
    ├─→ リポジトリ内の claude-sessions/ から前日分の JSONL を読み取り
    ├─→ GitHub API: 全 owner リポジトリの Commit / PR / Issue を取得
    ├─→ Claude API: 全データを統合して自然言語で日次要約を生成
    └─→ Notion API: 要約をデータベースページとして作成
```

### 3.2 使用する外部サービス・API

| サービス | 用途 | 認証方式 |
|---|---|---|
| GitHub API (REST) | アクティビティデータの取得 | Personal Access Token (Fine-grained PAT) |
| Anthropic API | 自然言語による要約生成 | API Key |
| Notion API | 作業記録の書き込み | Internal Integration Token |

### 3.3 設計方針

- **イベント駆動でのログ同期**: 各リポジトリの commit を契機に、その時点でアクティブな Claude Code セッションの JSONL を `logs` リポジトリに push する。cron による定期同期は行わない
- **要約生成は GitHub Actions に一元化**: Claude API の呼び出しを含むすべての処理を Actions 側で実行する
- **生データの集約管理**: Claude Code の JSONL は `logs` リポジトリ（private）にのみ格納し、各プロジェクトリポジトリには配置しない

### 3.4 リポジトリ構成

すべてを1つの private リポジトリ `logs` に集約する。

```
logs/ (private)
├── .github/
│   └── workflows/
│       └── daily_report.yml          # GitHub Actions ワークフロー
├── scripts/
│   ├── daily_report.py               # Actions 側: データ統合・要約・Notion 書き込み
│   └── push_active_session.sh        # post-commit hook から呼ばれるスクリプト
├── hooks/
│   └── post-commit                   # 各リポジトリにシンボリックリンクで配置
├── claude-sessions/                  # commit 時に push される Claude Code 生データ
│   ├── {project-a}/
│   │   ├── {session-id-1}.jsonl
│   │   └── {session-id-2}.jsonl
│   └── {project-b}/
│       └── ...
└── README.md
```

## 4. フェーズ 1: post-commit hook によるセッションログ同期

### 4.1 概要

各リポジトリに Git の `post-commit` hook を設置し、commit が発生するたびに、その時点でアクティブな Claude Code セッションの JSONL を `logs` リポジトリに自動 push する。

### 4.2 データソース

Claude Code は会話を `~/.claude/projects/` 以下にローカル保存している。

- 各プロジェクトがディレクトリとして存在（パスのスラッシュがダッシュに置換された名前）
- 個別セッションは JSONL ファイルとして保存
- `sessions-index.json` にメタデータ（サマリー、メッセージ数、ブランチ、タイムスタンプ）が含まれる

### 4.3 アクティブセッションの特定

「アクティブなセッション」とは、commit を行った時点で進行中（まだクローズされていない）の Claude Code セッションを指す。以下の方法で特定する。

1. commit を行ったリポジトリのパスから、対応する `~/.claude/projects/{project-name}/` を特定
2. `sessions-index.json` の最終更新タイムスタンプを参照し、直近（例: 過去1時間以内）に更新されたセッションを抽出
3. 該当する JSONL ファイルを `logs` リポジトリの `claude-sessions/` に同期

### 4.4 post-commit hook の処理フロー

```
post-commit hook 発火
    │
    ├─→ 1. 現在のリポジトリパスから Claude Code のプロジェクト名を解決
    │
    ├─→ 2. ~/.claude/projects/{project-name}/sessions-index.json を参照
    │      直近に更新されたセッション ID を取得
    │
    ├─→ 3. 該当する JSONL ファイルを
    │      {LOGS_REPO}/claude-sessions/{project-name}/{session-id}.jsonl にコピー
    │
    └─→ 4. logs リポジトリで commit & push（バックグラウンド実行）
```

### 4.5 hook スクリプトの設計

`logs/hooks/post-commit` として管理し、各リポジトリの `.git/hooks/post-commit` にシンボリックリンクまたはコピーで配置する。

hook スクリプトの要件:

- **環境変数 `LOGS_REPO_PATH`**: `logs` リポジトリのローカルパスを指定
- **バックグラウンド実行**: commit の体感速度に影響を与えないよう、`logs` リポジトリへの push はバックグラウンドで行う
- **冪等性**: 同じセッションの JSONL が複数回 push されても問題ないよう、ファイル単位で上書きする設計とする
- **エラーの静音化**: hook の失敗が本来の commit ワークフローを妨げないよう、エラーは stderr に出力するのみで exit 0 を返す

### 4.6 hook の配布方法

各リポジトリに hook を設置する方法として以下を想定する。

- **手動設置**: `ln -s {LOGS_REPO}/hooks/post-commit {REPO}/.git/hooks/post-commit`
- **Git テンプレート**: `git config --global init.templateDir {LOGS_REPO}/hooks-template` で新規リポジトリに自動適用
- **セットアップスクリプト**: `logs` リポジトリに含める初期設定スクリプトで、既存の全リポジトリに一括設置

### 4.7 ファイル命名規則

```
claude-sessions/{project-name}/{session-id}.jsonl
```

- `{project-name}`: Claude Code が使用するプロジェクト識別名
- `{session-id}`: セッション固有の ID

### 4.8 セキュリティに関する注意

- `logs` リポジトリは必ず **private** にすること
- JSONL には会話の生データが含まれるため、機密情報の漏洩に注意
- 必要に応じて `.gitignore` やフィルタリングで特定プロジェクトを除外可能にする

## 5. フェーズ 2: データ統合・要約・Notion 書き込み

### 5.1 GitHub アクティビティの取得

#### 5.1.1 対象リポジトリ

- GitHub API `GET /user/repos` で取得
- パラメータ `affiliation=owner` により、自分が owner のリポジトリのみを対象とする
- ページネーション対応（`per_page=100`）で全件取得

#### 5.1.2 対象アクティビティ

前日 UTC 00:00:00 〜 当日 UTC 00:00:00 の範囲を取得対象とする。

**Commits**

- エンドポイント: `GET /repos/{owner}/{repo}/commits`
- パラメータ: `since`, `until`
- 取得項目: コミットメッセージ、作成者、日時、SHA

**Pull Requests**

- エンドポイント: `GET /repos/{owner}/{repo}/pulls`
- パラメータ: `state=all`, `sort=updated`, `direction=desc`
- 前日以降に更新されたもののみをフィルタ
- 取得項目: タイトル、番号、状態（open / closed / merged）、作成者、ラベル

**Issues**

- エンドポイント: `GET /repos/{owner}/{repo}/issues`
- パラメータ: `since`, `state=all`
- Pull Request を除外（`pull_request` キーが存在しないもの）
- 取得項目: タイトル、番号、状態（open / closed）、作成者、ラベル

### 5.2 Claude Code セッションログの読み取り

1. `claude-sessions/` 以下の全 JSONL ファイルを走査
2. 各ファイルの最終更新日時（git log またはファイル内タイムスタンプ）で前日分をフィルタ
3. JSONL から以下を抽出:
   - ユーザーのプロンプト（質問・指示の内容）
   - Claude の応答の要点
   - 使用したツール（ファイル編集、コマンド実行など）
   - 対象プロジェクト名

### 5.3 要約生成（Claude API）

#### 5.3.1 使用モデル

- Anthropic Claude API（`claude-sonnet-4-20250514` 推奨）

#### 5.3.2 プロンプト設計方針

GitHub アクティビティと Claude Code セッションログの両方をコンテキストとして渡し、以下の観点で統合的な要約を生成する。

- **全体サマリー**: その日の作業全体を2〜3文で要約
- **リポジトリ別の要点**: 各リポジトリで行われた作業の概要
- **Claude Code での作業**: どのプロジェクトで何を相談・実装したか
- **主な成果・進捗**: マージされた PR、クローズされた Issue など
- **継続中の作業**: オープンな PR や Issue

#### 5.3.3 入力フォーマット（Claude API に渡すデータ）

```
以下は {日付} の GitHub アクティビティおよび Claude Code での作業記録です。
日本語で簡潔に要約してください。

---
# GitHub アクティビティ

## {リポジトリ名1}
### Commits
- {コミットメッセージ1}
- {コミットメッセージ2}
### Pull Requests
- [merged] #12 機能Aの追加
### Issues
- [closed] #8 バグ修正

## {リポジトリ名2}
...

---
# Claude Code セッション

## プロジェクト: {project-a}
### セッション 1 (14:00 - 15:30)
- ユーザー: 認証機能のリファクタリングについて相談
- Claude: JWT トークンの更新ロジックを提案、実装を支援
- ツール使用: ファイル編集 (auth.ts, middleware.ts)

### セッション 2 (17:00 - 17:30)
- ユーザー: テストの追加を依頼
- Claude: auth 関連のユニットテストを作成
...
```

#### 5.3.4 出力フォーマット（期待する応答）

Claude に構造化された要約を返すよう指示し、以下のセクションを含める。

- 全体サマリー
- リポジトリごとの作業概要（GitHub + Claude Code を統合）
- タグの提案
- ステータスの判定

## 6. Notion データベース仕様

### 6.1 データベースプロパティ

| プロパティ名 | 型 | 説明 | 例 |
|---|---|---|---|
| Name | Title | 日次レポートのタイトル | `Daily Report 2025-03-01` |
| Date | Date | 対象日 | `2025-03-01` |
| Repositories | Multi-select | アクティビティがあったリポジトリ名 | `my-app`, `api-server` |
| Tags | Multi-select | 作業内容の分類タグ | `feature`, `bugfix`, `docs`, `refactor`, `ci`, `review`, `ai-assisted` |
| Status | Select | その日の全体的な進捗状態 | `productive`, `maintenance`, `blocked`, `light` |
| Commits | Number | コミット総数 | `12` |
| PRs Merged | Number | マージされた PR 数 | `3` |
| Issues Closed | Number | クローズされた Issue 数 | `2` |
| Claude Sessions | Number | Claude Code セッション数 | `4` |

### 6.2 ページ本文（children blocks）

Notion ページの本文には Claude が生成した自然言語の要約を記載する。ブロックタイプとして `heading_2` と `paragraph` を使い分けて構造化する。

### 6.3 タグの分類基準

| タグ | 基準 |
|---|---|
| `feature` | 新機能追加に関する Commit / PR |
| `bugfix` | バグ修正に関する Commit / PR / Issue |
| `docs` | ドキュメント更新 |
| `refactor` | リファクタリング |
| `ci` | CI/CD やビルド設定の変更 |
| `review` | PR レビューが主な活動だった場合 |
| `ai-assisted` | Claude Code を活用した作業が含まれる場合 |

タグは Claude API の要約生成時に、コミットメッセージ・PR タイトル・Claude Code セッション内容から自動判定させる。

### 6.4 ステータスの判定基準

| ステータス | 基準 |
|---|---|
| `productive` | 複数の PR マージや Issue クローズがある |
| `maintenance` | 依存関係更新、CI 修正など保守作業が中心 |
| `blocked` | PR レビュー待ちや Issue の議論が中心 |
| `light` | アクティビティが少ない日 |

ステータスも Claude API による要約時に判定させる。

## 7. GitHub Actions ワークフロー仕様

### 7.1 トリガー

- **スケジュール実行**: 毎日 UTC 00:00（JST 09:00）
- **手動実行**: `workflow_dispatch` で任意のタイミングでも実行可能

### 7.2 Secrets（`logs` リポジトリに設定）

| Secret 名 | 説明 |
|---|---|
| `MY_GITHUB_PAT` | GitHub Fine-grained PAT（全 owner リポジトリへの read 権限） |
| `NOTION_TOKEN` | Notion Internal Integration トークン |
| `NOTION_DATABASE_ID` | 書き込み先の Notion データベース ID |
| `ANTHROPIC_API_KEY` | Anthropic API キー |

### 7.3 ワークフロー定義（概要）

```yaml
name: Daily Work Log
on:
  schedule:
    - cron: '0 0 * * *'
  workflow_dispatch:

jobs:
  daily-log:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0  # claude-sessions の履歴にアクセスするため
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: pip install requests anthropic
      - run: python scripts/daily_report.py
        env:
          GITHUB_PAT: ${{ secrets.MY_GITHUB_PAT }}
          NOTION_TOKEN: ${{ secrets.NOTION_TOKEN }}
          NOTION_DATABASE_ID: ${{ secrets.NOTION_DATABASE_ID }}
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
```

## 8. セットアップ手順

### 8.1 `logs` リポジトリの作成

1. GitHub で **private** リポジトリ `logs` を作成
2. 上記のディレクトリ構成に従いファイルを配置
3. Settings > Secrets and variables > Actions に4つの Secret を登録

### 8.2 GitHub PAT の作成

1. Fine-grained PAT を作成
2. スコープ: 自分の全リポジトリへの Contents / Issues / Pull Requests の read 権限
3. `logs` リポジトリの Secret `MY_GITHUB_PAT` に登録

### 8.3 post-commit hook の設置

1. `logs` リポジトリをローカルにクローン
2. 環境変数 `LOGS_REPO_PATH` にクローン先のパスを設定（例: `~/.config/logs-repo-path` に記載）
3. 対象リポジトリに hook を設置:
   - 手動: `ln -s {LOGS_REPO}/hooks/post-commit {REPO}/.git/hooks/post-commit`
   - 一括: `logs` リポジトリのセットアップスクリプトを実行
4. SSH 鍵または PAT で `logs` リポジトリへの push が可能であることを確認

### 8.4 Notion

1. [Notion Integrations](https://www.notion.so/my-integrations) で Internal Integration を作成
2. 「6.1 データベースプロパティ」に従いデータベースを作成
3. データベースの「コネクト」から作成した Integration を追加

### 8.5 Anthropic

1. [Anthropic Console](https://console.anthropic.com/) で API キーを発行

## 9. 運用上の考慮事項

### 9.1 実行タイミングについて

- フェーズ 1（セッションログ同期）は commit のたびにイベント駆動で実行されるため、cron のようなタイミング管理は不要
- フェーズ 2（日次要約）の Actions cron 実行時点で、前日の commit に紐づくセッションログはすでに `logs` リポジトリに蓄積されている
- Claude Code を使ったが commit しなかった作業はログに含まれない点に注意（将来の拡張案として補完手段を検討）

### 9.2 API レートリミット

- GitHub API: 認証済みで 5,000 リクエスト/時。リポジトリ数が多い場合はリクエスト数に注意
- Anthropic API: プランに応じたレートリミットあり。1日1回の実行であれば問題なし
- Notion API: 3 リクエスト/秒。書き込みは1ページのため問題なし

### 9.3 コスト

- GitHub Actions: プライベートリポジトリは月2,000分の無料枠あり（実行時間は数分程度）
- Anthropic API: トークン使用量に応じた従量課金。日次1回の要約であれば少額
- Notion API: 無料

### 9.4 エラーハンドリング

- API 呼び出し失敗時のリトライ処理
- アクティビティが0件の日はスキップまたは「活動なし」と記録
- post-commit hook の失敗は本来の commit に影響を与えない設計（exit 0 を保証）
- `logs` リポジトリへの push が失敗した場合、次回 commit 時に未同期分も含めてリトライ
- GitHub Actions の失敗通知（メールまたは Slack 連携）

### 9.5 ストレージ管理

- `claude-sessions/` は JSONL ファイルが蓄積されるため、定期的なアーカイブまたは古いファイルの削除を検討
- git の履歴にも残るため、リポジトリサイズの肥大化に注意
- 必要に応じて Git LFS の利用や、一定期間経過後のファイル削除ポリシーを設ける

## 10. 将来の拡張案

- **commit を伴わない Claude Code セッションの補完**: cron ベースのフォールバック同期で、commit せずに終了したセッションも拾う
- **複数ユーザー対応**: Organization メンバーの活動もまとめて記録
- **週次・月次レポート**: 日次データを集約した定期サマリーの生成
- **Slack 通知**: Notion 記録と同時に Slack チャンネルにも投稿
- **ダッシュボード**: Notion データベースのビューを活用した可視化
- **claude.ai の会話記録**: データエクスポート機能との連携
- **複数マシン対応**: 複数の開発マシンからの同期時の競合解決
