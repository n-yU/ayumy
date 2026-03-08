# Ayumy: Spec
## 1. 概要
GitHub 上で自分が owner であるすべてのリポジトリにおける日次の開発アクティビティ（Commit, Pull Request, Issue）と、Claude Code での会話記録を自動収集し、Claude API で自然言語の要約を生成したうえで、Notion データベースに記録するシステム。

## 2. 目的
- 日々の開発作業を自動的に記録・蓄積する
- Claude Code での思考・意思決定の過程も含めて記録する
- 自然言語の要約により、振り返りや共有が容易な形で残す
- 手動の日報作成を不要にする

## 3. システム構成
### 3.1 アーキテクチャ
本システムは2フェーズで構成される。常時稼働マシン（Raspberry Pi 等）上で日次処理を実行し、セッションログの生データは LAN 内にのみ保持する。

```
[開発マシン]
  git commit → post-commit hook ─┐
  ayumy sync（手動）─────────────┤
                                  ▼
                         rsync over SSH
                                  │
[常時稼働マシン]                  ▼
  ~/ayumy-data/claude-sessions/ に JSONL 蓄積
  cron (毎日 UTC 00:00) → daily_report.py
    ├─→ JSONL + GitHub API → Claude API で要約生成
    ├─→ Notion API で記録
    ├─→ Slack Webhook で通知
    └─→ 処理済み JSONL を削除
```

### 3.2 使用する外部サービス・API
| サービス | 用途 | 認証方式 |
|---|---|---|
| GitHub API (REST) | アクティビティデータの取得 | Fine-grained PAT |
| Anthropic API | 自然言語による要約生成 | API Key |
| Notion API | 作業記録の書き込み | Internal Integration Token |
| Slack Incoming Webhook | 完了通知 | Webhook URL |

### 3.3 ディレクトリ構成
**ayumy リポジトリ（GitHub）** — スクリプトと設定のみ。セッションデータは含まない。

```
ayumy/
├── scripts/
│   ├── daily_report.py               # メインスクリプト: GitHub API + Claude API + Notion API
│   └── sync_session.sh               # セッション転送スクリプト（hook・手動共用）
├── hooks/
│   └── post-commit                   # 各リポジトリにシンボリックリンクで配置
├── Spec.md
├── CLAUDE.md
└── README.md
```

**常時稼働マシン上のデータディレクトリ**

```
~/ayumy-data/
├── claude-sessions/                  # 開発マシンから転送された JSONL
│   ├── {project-name}/
│   │   └── {session-id}.jsonl
│   └── ...
└── logs/                             # 実行ログ
```

## 4. フェーズ 1: セッションログの転送
### 4.1 概要
Claude Code セッションの JSONL を常時稼働マシンに rsync で転送する。

- **自動転送（post-commit hook）**: commit を契機に、当該プロジェクトのアクティブセッションをバックグラウンドで転送
- **手動転送（`ayumy sync`）**: commit せずに作業を中断する場合など、任意のタイミングで実行

いずれも共通の転送スクリプト `scripts/sync_session.sh` を使用する。

### 4.2 データソース
Claude Code は会話を `~/.claude/projects/` 以下にローカル保存している。

- 各プロジェクトがディレクトリとして存在（パスのスラッシュがダッシュに置換された名前）
- 個別セッションは JSONL ファイルとして保存
- `sessions-index.json` にメタデータ（サマリー、メッセージ数、ブランチ、タイムスタンプ）が含まれる

「アクティブなセッション」とは、直近（例: 過去1時間以内）に `sessions-index.json` のタイムスタンプが更新されたセッションを指す。

### 4.3 転送スクリプト（`sync_session.sh`）
hook と手動実行の両方から呼ばれる共通スクリプト。

```
sync_session.sh [--project <project-name>] [--all] [--background]
```

| オプション | 動作 |
|---|---|
| `--project <name>` | 指定プロジェクトのアクティブセッションのみ転送 |
| `--all` | 全プロジェクトから当日更新されたセッションを一括転送 |
| `--background` | バックグラウンドで実行（hook 用） |
| 引数なし | カレントディレクトリに対応するプロジェクトを自動判定 |

要件:

- **環境変数 `AYUMY_HOST`**: 常時稼働マシンの SSH ホスト名（例: `pi@raspberrypi.local`）
- **環境変数 `AYUMY_DATA_DIR`**: 常時稼働マシン上のデータディレクトリパス（デフォルト: `~/ayumy-data`）
- **冪等性**: ファイル単位で上書きする設計とし、同じ JSONL の複数回転送でも問題ない

### 4.4 post-commit hook
`ayumy/hooks/post-commit` として管理し、各リポジトリの `.git/hooks/post-commit` にシンボリックリンクで配置する。

hook はリポジトリパスからプロジェクト名を解決し、`sync_session.sh --project {name} --background` を呼び出すラッパーである。

- hook の失敗は commit に影響を与えない（exit 0 を保証）
- エラーは stderr に出力するのみ

hook の配布方法:

- **手動設置**: `ln -s {AYUMY_REPO}/hooks/post-commit {REPO}/.git/hooks/post-commit`
- **Git テンプレート**: `git config --global init.templateDir {AYUMY_REPO}/hooks-template`
- **セットアップスクリプト**: 既存の全リポジトリに一括設置

### 4.5 手動同期（`ayumy sync`）
commit せずに作業を中断する場合や、hook で転送されなかったセッションを補完する。

```bash
ayumy sync                        # current directory のプロジェクトを同期
ayumy sync --all                  # 全プロジェクトの当日分を一括同期
ayumy sync --project my-project   # 特定プロジェクトを指定
```

`ayumy sync` は `sync_session.sh` を呼び出すシェルエイリアスまたはラッパーとして実装する。手動実行時はフォアグラウンドで実行し、転送結果を標準出力に表示する。

### 4.6 セキュリティに関する注意
- JSONL には会話の生データが含まれるため、機密情報の漏洩に注意
- 常時稼働マシンへの SSH 接続は鍵認証のみ（パスワード認証は無効化）
- データディレクトリのパーミッションは 700
- 必要に応じて特定プロジェクトを除外するフィルタリング機能を設ける

## 5. フェーズ 2: データ統合・要約・Notion 書き込み
### 5.1 GitHub アクティビティの取得
対象期間: 前日 UTC 00:00:00 〜 当日 UTC 00:00:00

対象リポジトリは `GET /user/repos`（`affiliation=owner`, `per_page=100`）で全件取得する。

| アクティビティ | エンドポイント | フィルタ | 取得項目 |
|---|---|---|---|
| Commits | `GET /repos/{owner}/{repo}/commits` | `since`, `until` | メッセージ、作成者、日時、SHA |
| Pull Requests | `GET /repos/{owner}/{repo}/pulls` | `state=all`, `sort=updated`, 前日以降 | タイトル、番号、状態、作成者、ラベル |
| Issues | `GET /repos/{owner}/{repo}/issues` | `since`, `state=all`, PR を除外 | タイトル、番号、状態、作成者、ラベル |

### 5.2 Claude Code セッションログの読み取り
1. `~/ayumy-data/claude-sessions/` 以下の全 JSONL を走査し、最終更新日時で前日分をフィルタ
2. JSONL から抽出する項目: ユーザーのプロンプト、Claude の応答の要点、使用したツール、対象プロジェクト名

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
- Claude: JWT トークンの更新ロジックを提案、実装を支援
- ツール使用: ファイル編集 (auth.ts, middleware.ts)
```

出力には全体サマリー、リポジトリごとの作業概要、タグの提案、ステータスの判定を含める。

### 5.4 Slack 通知
Notion への書き込み完了後、Slack Incoming Webhook で指定チャンネルに通知を送信する。

通知内容:
- 全体サマリー（Claude API が生成した2〜3文の要約）
- Notion ページへのリンク

通知が失敗しても処理全体は正常終了とする（通知はベストエフォート）。

### 5.5 処理済み JSONL の cleanup
要約生成と Notion 書き込みが正常に完了した後、処理対象の JSONL ファイルを削除する。削除前にログ出力で対象ファイルを記録する。

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
Notion ページの本文には Claude が生成した要約を記載する。ブロックタイプとして `heading_2` と `paragraph` を使い分けて構造化する。

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

タグは Claude API の要約生成時に自動判定させる。

### 6.4 ステータスの判定基準
| ステータス | 基準 |
|---|---|
| `productive` | 複数の PR マージや Issue クローズがある |
| `maintenance` | 依存関係更新、CI 修正など保守作業が中心 |
| `blocked` | PR レビュー待ちや Issue の議論が中心 |
| `light` | アクティビティが少ない日 |

ステータスも Claude API による要約時に判定させる。

## 7. 常時稼働マシンの構成
### 7.1 cron 設定
```
0 0 * * * cd ~/ayumy && python3 scripts/daily_report.py >> ~/ayumy-data/logs/daily_report.log 2>&1
```

毎日 UTC 00:00（JST 09:00）に実行する。

### 7.2 環境変数
`~/.ayumy.env` に記載し、スクリプト内で読み込む。

| 環境変数 | 説明 |
|---|---|
| `GITHUB_PAT` | GitHub Fine-grained PAT（全 owner リポジトリへの read 権限） |
| `ANTHROPIC_API_KEY` | Anthropic API キー |
| `NOTION_TOKEN` | Notion Internal Integration トークン |
| `NOTION_DATABASE_ID` | 書き込み先の Notion データベース ID |
| `AYUMY_DATA_DIR` | データディレクトリのパス（デフォルト: `~/ayumy-data`） |
| `SLACK_WEBHOOK_URL` | Slack Incoming Webhook URL |

### 7.3 必要なソフトウェア
- Python 3.12 以上（`requests`, `anthropic`）
- rsync, SSH サーバー（sshd）

## 8. セットアップ手順
### 8.1 常時稼働マシン
1. SSH サーバーを有効化し、鍵認証を設定（パスワード認証は無効化推奨）
2. データディレクトリを作成: `mkdir -p ~/ayumy-data/claude-sessions ~/ayumy-data/logs && chmod 700 ~/ayumy-data`
3. `ayumy` リポジトリをクローン: `git clone https://github.com/{user}/ayumy.git ~/ayumy`
4. Python 依存をインストール: `pip install requests anthropic`
5. `~/.ayumy.env` を作成（§7.2 参照）
6. cron を設定（§7.1 参照）

### 8.2 GitHub PAT
1. Fine-grained PAT を作成（スコープ: 全 owner リポジトリへの Contents / Issues / Pull Requests の read 権限）
2. 常時稼働マシンの `~/.ayumy.env` に `GITHUB_PAT` として記載

### 8.3 開発マシン
1. 常時稼働マシンへの SSH 鍵認証を設定（`ssh-copy-id` 等）
2. 環境変数 `AYUMY_HOST`（例: `pi@raspberrypi.local`）と `AYUMY_DATA_DIR`（デフォルト: `~/ayumy-data`）を設定
3. 対象リポジトリに hook を設置（§4.4 参照）
4. 接続テスト: `rsync --dry-run test.txt ${AYUMY_HOST}:~/ayumy-data/`

### 8.4 Notion
1. [Notion Integrations](https://www.notion.so/my-integrations) で Internal Integration を作成
2. §6.1 に従いデータベースを作成し、Integration を接続

### 8.5 Anthropic
1. [Anthropic Console](https://console.anthropic.com/) で API キーを発行

### 8.6 Slack
1. Slack App を作成し、Incoming Webhook を有効化
2. 通知先チャンネルを選択して Webhook URL を発行
3. 常時稼働マシンの `~/.ayumy.env` に `SLACK_WEBHOOK_URL` として記載

## 9. 運用上の考慮事項
### 9.1 ネットワーク要件
- 開発マシンと常時稼働マシンが同一 LAN 内にあること（rsync による転送のため）
- 外出先からも転送したい場合は Tailscale 等の VPN を導入する
- 常時稼働マシンからインターネットへのアクセス（各 API の呼び出し）

### 9.2 API レートリミット
- GitHub API: 認証済みで 5,000 リクエスト/時
- Anthropic API: プランに応じたレートリミットあり（日次1回なら問題なし）
- Notion API: 3 リクエスト/秒（1ページの書き込みのみなので問題なし）

### 9.3 エラーハンドリング
- API 呼び出し失敗時のリトライ処理
- アクティビティが0件の日はスキップまたは「活動なし」と記録
- post-commit hook は必ず exit 0（commit をブロックしない）
- rsync 転送失敗時、JSONL はソース側に残るため次回転送時にリトライ可能
- 常時稼働マシンがダウンした場合も hook は正常終了し、復旧後に `ayumy sync --all` で補完可能

### 9.4 ランニングコスト見積もり
課金が発生するのは Anthropic API のみ。GitHub API と Notion API は無料枠内で収まる。

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

### 9.5 ストレージ管理
- 処理済み JSONL は日次処理の最後に自動削除（§5.4）
- 実行ログは logrotate 等で管理

## 10. 開発手順
以下の順序で実装を進める。依存関係の少ないコンポーネントから着手し、先に作ったものが後のテストデータ・検証基盤となる構成。

### Phase 1: セッション転送スクリプト（`scripts/sync_session.sh`） [#1](https://github.com/n-yU/ayumy/issues/1)
外部 API 不要。ローカル環境のみで動作確認できる。
- `--project`, `--all`, `--background` オプションの実装
- `~/.claude/projects/` からアクティブセッションの JSONL を検出するロジック
- rsync による転送（冪等性の担保）
- ローカル検証: `AYUMY_HOST` を空にし、ローカルの別ディレクトリ（例: `/tmp/ayumy-data/`）を転送先として使用

### Phase 2: Git hook（`hooks/post-commit`） [#2](https://github.com/n-yU/ayumy/issues/2)
Phase 1 の `sync_session.sh` を前提としたラッパー。
- リポジトリパスからプロジェクト名を解決
- `sync_session.sh --project {name} --background` の呼び出し
- `exit 0` の保証（commit をブロックしない設計）

### Phase 3: 常時稼働マシンのコンテナ化（`Dockerfile`, `compose.yaml`）
`daily_report.py` の実行環境をコンテナとして構築する。
- Python 3.12 + 依存パッケージ（`requests`, `anthropic`）
- cron による日次実行
- `~/ayumy-data/` を volume mount でホストと共有
- `.ayumy.env` を `env_file` として読み込み
- SSH（rsync 受信側）はホストの sshd を使用し、コンテナには含めない

### Phase 4: メインスクリプト（`scripts/daily_report.py`）
以下のサブ機能を順に実装する。各機能は独立して動作確認可能。
1. GitHub アクティビティ取得 — REST API で Commits / PRs / Issues を取得・整形
2. JSONL セッションログの読み取り — `~/ayumy-data/claude-sessions/` のパース
3. Claude API で要約生成 — §5.3 のフォーマットに従い統合要約を生成
4. Notion API で書き込み — データベースプロパティとページ本文の作成（§6 準拠）
5. Slack 通知 — Incoming Webhook でサマリーと Notion リンクを送信（§5.4）
6. 処理済み JSONL の cleanup — 正常完了後に削除（§5.5）

### Phase 5: 結合テスト・運用準備
- 全コンポーネントの結合テスト（開発マシン → Pi → Notion の一連の流れ）
- cron 設定の投入と初回実行の確認
- エラーハンドリング・ログ出力の検証

## 11. 将来の拡張案
- **開発マシン側の定期自動同期**: cron で `ayumy sync --all` を定期実行し、手動同期の手間を省く
- **複数開発マシン対応**: 競合解決（ファイル名にホスト名を含める等）
- **週次・月次レポート**: 日次データを集約した定期サマリー
- **ダッシュボード**: Notion データベースのビューを活用した可視化
- **claude.ai の会話記録**: データエクスポート機能との連携
- **過去日の再処理**: 日付を指定して再実行できるオプション
