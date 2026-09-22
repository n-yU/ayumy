# CLAUDE.md
このファイルは Claude Code (claude.ai/code) がこのリポジトリで作業する際のガイドラインを提供する。

## プロジェクト概要
**ayumy** — GitHub 上の日次開発アクティビティ（Commit, PR, Issue）と Claude Code セッションログを自動収集し、Claude API で自然言語の要約を生成して Notion データベースに記録するシステム。

## アーキテクチャ
2 段階構成（セッションログは S3 に保管、セッションメタデータは DynamoDB に集約、レポート生成は AWS Lambda で実行）:

1. **Stage 1（pre-push hook / 手動同期）**: 各リポジトリでの push を契機に、`~/.claude/projects/` から未同期の Claude Code セッションの JSONL を S3 バケットにアップロードする。アップロード失敗時は push を中止する。`ayumy sync --report` で S3 転送後に Lambda を呼び出してレポート生成まで実行できる。
2. **Stage 2（AWS Lambda）**: S3 上のセッションログをパースして DynamoDB に書き込み、S3 から JSONL を削除する。DynamoDB からセッションメタデータを読み取り、GitHub API によるアクティビティ取得を行い、Claude API で要約を生成して Notion に書き込み、Slack に通知する。EventBridge Scheduler による日次の定期実行に加え、`ayumy sync --report` による手動実行にも対応する。

## リポジトリ構成
```
scripts/sync_session.sh              # セッション転送スクリプト（hook・手動共用）
scripts/setup_hooks.sh               # hook の設置スクリプト
hooks/pre-push                       # Git hook（各リポジトリにシンボリックリンクで配置）
lambda/handler.py                    # Lambda ハンドラ（report パッケージを呼び出す entrypoint）
lambda/config/                       # チューニング定数の YAML と loader（追跡対象は template のみ、config.yml は生成物）
lambda/report/                       # メインパッケージ: GitHub API + Claude API + Notion API
lambda/report/prompts/               # Claude API に渡すプロンプト本文（`string.Template` の `$` 記法で実行時に値を差し込む）
lambda/report/schemas/               # Claude API に渡す tool 定義
lambda/requirements.in               # Lambda デプロイ依存の source（直接依存のみ、バージョン範囲指定）
lambda/requirements-dev.in           # ローカル開発依存の source（boto3 等を追加、`-r requirements.in` で本体を参照）
lambda/requirements.txt              # `requirements.in` から `uv pip compile --generate-hashes` で生成した hash 付き lock
lambda/requirements-dev.txt          # `requirements-dev.in` から同様に生成した hash 付き lock
template.yaml                        # AWS SAM テンプレート（Lambda, EventBridge, IAM ロール, S3 バケット, DynamoDB テーブル）
```

## コーディング規約
### import の使い分け
自リポジトリのモジュールと第三者ライブラリは、モジュールを取り込んで `<module>.<name>` の形で参照する。標準ライブラリは対象外とし、現状の個別取り込みを維持する

名前を個別に取り込むのは以下に限る。判定は参照するファイルごとに行う
- モジュール名と定数名が重複する場合（`config` の `CONFIG`）。前置しても読み手が得る情報は増えず、名前が伸びるだけであるため
- 型注釈にしか使わない取り込み（`github.Issue` / `github.Repository`）
- そのファイルの引数名・ローカル変数名とモジュール名が衝突する場合（`notice` / `blocks`）。モジュールが隠れるため
- モジュール名が別の subpackage と衝突する場合（`domain/session.py` と `report/session/`）

モジュール名の衝突は、識別子の側を役割で名付け直せるなら改名してモジュール経由に寄せる。対象そのものを指す一般名（`cost` / `activity` / `summary`）は `cost_display` / `github_activity` / `summary_lines` のように具体化できることが多い。名前を歪めてまで寄せる必要はない

機能を提供する subpackage（`github` / `notion` / `session` / `slack` / `summarizer`）は `__init__.py` の再 export を経由して参照し、内部モジュールを直接指さない。型とヘルパーを置く `domain` / `shared` は再 export を持たないため、モジュールを直接指す

第三者ライブラリ側の対象と別名は ruff の flake8-import-conventions で固定しているため、ここには列挙しない

### 命名
モジュール経由で参照する名前は、モジュール名と重複する部分を落とす（`github.Client` / `summary.Usage` / `tags.DEFINITIONS`）。参照側でモジュール名が文脈を与えるため、接頭辞は情報を持たない

接頭辞を残すのは以下に限る
- 個別に取り込む名前。参照側にモジュール名が現れず、名前だけで読めなければならない
- 落とすと役割が読めなくなる名前（`SessionLogParser` / `GitHubActivity`）。前者は解析する主体だと読めなくなり、後者はモジュール名の後ろに置くと名前空間の区切りに見える
- `config` モジュールの型。最上位の型は落とすと何も残らず、セクションごとの型だけ落とすと同じモジュールの中で不揃いになる

### 型注釈
`dict` / `list` を型引数なしで書かない。中身が定まらない場合も `dict[str, Any]` と書く

同じ用途が繰り返し現れるなら `type` 文で別名を与え、注釈から用途が読めるようにする
- 別名は使う側ではなく、その形を組み立てるモジュールに置く（Notion / Slack の block はそれぞれの `blocks`、DynamoDB の項目は両方の store から使うため `domain/dynamo.py`）
- 1 度しか現れない用途には別名を作らず `dict[str, Any]` と書く
- 別名の実体は `dict[str, Any]` とし、TypedDict にはしない。組み立てる箇所が 1 つに集まっており、構造を型で追っても検査できる範囲が増えないため

自分自身のクラスを返すメソッドは `typing.Self` を返し、インスタンスの生成には `type(self)` / `cls` を使う

型注釈にしか使わない import は置き場所を分ける
- `TYPE_CHECKING` の下に置くのは、読み込みの重い第三者ライブラリの型に限る
- 標準ライブラリと自リポジトリのモジュールは通常の import にする
- `from __future__ import annotations` を入れるのは、`TYPE_CHECKING` の下で取り込んだ名前を関数の注釈に使うファイルだけ

## 技術詳細
- **実行環境**: AWS Lambda（SAM でデプロイ）
- **クライアント対応 OS**: クライアント側のスクリプト（`scripts/`, `hooks/`, `bin/ayumy`）は macOS のみサポート
- **ローカル開発**: uv で `.venv` を管理。shell テスト実行には bats が必要（`brew install bats-core`）
- **worktree での作業**: worktree を作った後は `make lambda-install` を実行して `.venv` を用意する。`lambda/config/config.yml` 等の追跡対象外ファイルは `.worktreeinclude` に列挙してあり、worktree の作成時にコピーされるため `make config-init` は不要
- **テスト・lint・format コマンド**: `make test`（Python + shell 一括）／ `make test-python` ／ `make test-shell` ／ `make test-cov`（Python カバレッジ計測。Shell カバレッジは CI でのみ取得）／ `make format` ／ `make format-check` ／ `make lint`（Ruff + mypy）／ `make lint-fix` ／ `make typecheck`（mypy のみ）を使う。target 一覧と用途は `make help` で確認できる
- **型検査**: mypy を `lambda/` と `tests/` に掛ける。`lambda/` では注釈のない関数定義を禁止し、`tests/` では注釈を求めない代わりに関数の中身を検査する。設定は pyproject.toml に置く
- **Lambda デプロイ・build コマンド**
  - デプロイ: `make lambda-deploy`（AWS 認証確認 + `sam build` + `sam deploy --no-confirm-changeset`）
  - ローカル invoke: `make lambda-invoke`（`sam build` + `sam local invoke`）
  - raw `sam build` / `sam deploy` は Makefile が扱わないケースに限定する（初回 `sam deploy --guided`、S3 バケット変更時の `sam deploy --no-resolve-s3`）
- **依存管理**: 直接依存は `lambda/requirements.in` / `lambda/requirements-dev.in` に記述し、`make lock` で `uv pip compile --generate-hashes` を呼んで hash 付きの `lambda/requirements.txt` / `lambda/requirements-dev.txt` を再生成する。Lambda デプロイ・CI・`make lambda-install` はいずれも生成された `.txt` を読むため、`.in` を変更したら必ず `make lock` を実行し `.txt` を commit する。`uv` のバージョンが異なると `.txt` の出力が変わり CI drift check が誤検知するため、ローカルでも CI 側（`.github/workflows/ci.yml` の `astral-sh/setup-uv`）と同じバージョンを使う
- **避けるコマンド**: `uv run pytest` を使わない（CWD の `pyproject.toml` を project marker として検出し `uv.lock` を暗黙生成してしまうため。本リポジトリは `pip-compile` ベースの `requirements*.txt` を lock として運用し、`uv.lock` は管理対象外としている）
- **言語**: Python 3.12、デプロイ依存: `requests`, `anthropic`, `PyGithub`、開発依存: 左記 + `boto3`, `mypy`（boto3 の stub は S3 と Secrets Manager のみ）
- **Claude モデル**: 要約生成モデルは `lambda/config/config.yml` で定義（デフォルト `claude-sonnet-4-6`）。このファイルは追跡対象外で、[config.template.yml](lambda/config/config.template.yml) から `make config-init` で生成する
- **GitHub API**: REST、Fine-grained PAT、セッションログから特定したリポジトリのみ対象
- **Notion API**: Internal Integration Token、データベースプロパティは [Spec: Database Properties](docs/Spec.md#database-properties) に定義
- **Hook 設計**: フォアグラウンド同期実行で、転送失敗時は非ゼロ終了で push を中止する（silent fail 防止）。セッション ID 単位の上書きで冪等性を担保

## 環境変数
Lambda 側は [template.yaml](template.yaml) の `Environment` で定義する。認証情報は環境変数に置かず Secrets Manager から取得する

クライアントマシン側に設定する変数は [Setup.md](docs/Setup.md) を参照する

## 開発メモ
- 仕様書は [Spec.md](docs/Spec.md)（日本語）— コードから読み取れない内容の原典
- [Manual.md](docs/Manual.md) は運用者目線で書く。実装寄りの用語（「振る舞いを調整する値」等）や構造の説明（「〜に集約されている」等）は使わず、「何ができるか」「どこで変更するか」を具体的に示す。実装・仕様レベルの細部は Spec.md 側に委ねる
  - 見出しは H2 を英語、H3 以下を日本語で書く
- [README.md](README.md) は、初めて訪れた読者に向けて丁寧体（です・ます調）で書く。表のセルや体言止めの箇条書きは対象外とする
- コード内コメントは英語で書くが、[config.template.yml](lambda/config/config.template.yml) のコメントだけは例外として日本語で書く。利用者が生成した config.yml を読みながら設定を変えるため、Manual.md と同じ運用者目線で書く
- JSONL の生データは S3 バケットに保管し、リモートリポジトリには push しない
- アクティビティの取得対象期間: 前日 JST 00:00:00 〜 当日 JST 00:00:00
- commit の前に、変更と食い違う記述がないかを [Spec.md](docs/Spec.md), [README.md](README.md), [Setup.md](docs/Setup.md), [Manual.md](docs/Manual.md) で確かめ、ずれがあれば同じ commit で直す
