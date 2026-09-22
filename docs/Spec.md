# Spec
Ayumy の仕様のうち、コードや設定を読んでも分からない内容を記す。外部との契約・設計判断とその理由・外部仕様の観測結果を扱う

- [Overview](#overview)
- [Goals](#goals)
- [System Components](#system-components)
  - [Architecture](#architecture)
  - [External Services and APIs](#external-services-and-apis)
- [Stage 1: Session Log Transfer](#stage-1-session-log-transfer)
  - [Stage 1 Overview](#stage-1-overview)
  - [Data Source](#data-source)
  - [Transfer Script](#transfer-script)
  - [Pre-push Hook](#pre-push-hook)
  - [Security Notes](#security-notes)
- [Stage 2: Data Integration, Summarization, and Notion Writing](#stage-2-data-integration-summarization-and-notion-writing)
  - [GitHub Activity Fetch](#github-activity-fetch)
  - [Session Log Read](#session-log-read)
  - [Session Write to DynamoDB](#session-write-to-dynamodb)
  - [Summary Generation](#summary-generation)
  - [Cost Execution Log Persistence](#cost-execution-log-persistence)
  - [Slack Notification](#slack-notification)
  - [Processed JSONL Cleanup](#processed-jsonl-cleanup)
- [Notion Database Specification](#notion-database-specification)
  - [Database Properties](#database-properties)
  - [Page Body](#page-body)
  - [Tag Classification](#tag-classification)
- [AWS Lambda Configuration](#aws-lambda-configuration)
  - [Lambda Execution Modes](#lambda-execution-modes)
  - [Lambda Function Configuration](#lambda-function-configuration)
- [Operational Considerations](#operational-considerations)
  - [Error Handling](#error-handling)

## Overview
GitHub 上の日次開発アクティビティ（Commit, Pull Request, Issue）と Claude Code での会話記録を自動収集するシステム。集めた内容は Claude API が自然言語で要約し、Notion データベースに記録する。対象リポジトリは S3 上の session ログから特定する

## Goals
- 日々の開発作業を自動的に記録・蓄積する
- Claude Code での思考・意思決定の過程も含めて記録する
- 自然言語の要約により、振り返りや共有が容易な形で残す
- 手動の日報作成を不要にする

## System Components
### Architecture
本システムは動作する場所によって 2 段階に分かれる

- [Stage 1: Session Log Transfer](#stage-1-session-log-transfer) — クライアントマシンで動き、session ログを S3 バケットへ送る
- [Stage 2: Data Integration, Summarization, and Notion Writing](#stage-2-data-integration-summarization-and-notion-writing) — AWS Lambda で動き、S3 の session ログを DynamoDB に取り込んでレポートを生成する

2 段階に分けるのは、session ログがクライアントマシンにしか存在せず、集約と要約はクライアントの稼働状態に左右されない実行環境を必要とするためである。全体の構成図は [README.md](../README.md) に置く

DynamoDB テーブル設計は [Session Write to DynamoDB](#session-write-to-dynamodb) と [Cost Execution Log Persistence](#cost-execution-log-persistence) を参照

### External Services and APIs
| Service | Purpose | Authentication |
|---|---|---|
| GitHub API (REST) | アクティビティデータの取得 | Fine-grained PAT |
| Claude API | 自然言語による要約生成 | API Key |
| Notion API | 作業記録の書き込み | Internal Integration Token |
| Slack Web API | 完了通知 | Bot User OAuth Token |
| AWS S3 | session ログの保管 | AWS 認証情報（IAM ユーザー / プロファイル） |
| Amazon DynamoDB | session メタデータの集約 | IAM ロール |
| AWS Lambda | レポート生成の実行環境 | IAM ロール |
| Amazon EventBridge Scheduler | 日次の定期実行 | — |

## Stage 1: Session Log Transfer
### Stage 1 Overview
Claude Code session の JSONL を S3 バケットに転送する。クライアント側のスクリプト（`scripts/`, `hooks/`, `bin/ayumy`）は macOS のみサポートする

- **自動転送（pre-push hook）**: push を契機に、当該プロジェクトの未同期 session を同期転送。失敗時は push を中止する
- **手動転送（`ayumy sync`）**: push せずに作業を中断する場合など、任意のタイミングで実行
- **手動転送＋レポート生成（`ayumy sync --report`）**: S3 への転送後に Lambda を呼び出してレポート生成まで実行

いずれも共通の転送スクリプト [scripts/sync_session.sh](../scripts/sync_session.sh) を使用する。`--report` 指定時は転送完了後に `aws lambda invoke` で Lambda 関数を呼び出す

S3 上のオブジェクトキーは `claude-sessions/{project-name}/{session-id}.jsonl` とする。Stage 2 はこのキー構造を前提に project 単位で session を読み、取り込んだ後に削除する（[Processed JSONL Cleanup](#processed-jsonl-cleanup)）

### Data Source
Claude Code は会話を `~/.claude/projects/` 以下にローカル保存している

- 各プロジェクトがディレクトリとして存在（パスのスラッシュとドットがダッシュに置換された名前。置換対象の文字は公開されていない）
- 個別 session は JSONL ファイル（`{session-id}.jsonl`）として保存
- メタデータ（session ID、タイムスタンプ、ブランチ等）は JSONL の各エントリに埋め込まれている
- 外部インデックスファイルは存在しない

JSONL の構造に公式仕様は無く、パース処理は観測に基づいて書かれている。Claude Code が生成するためタイムスタンプの形式は安定しており、解釈の失敗を想定した例外処理は置かない。一方、JSON として壊れた行は warning に記録して読み飛ばす。作業ディレクトリの値が想定した型でない場合は warning に記録し、値を空として処理を続ける

各エントリが持つ作業ディレクトリに、Bash ツールの実行コマンド冒頭の `cd` を反映して実際の実行先を求め、そのコマンドが project の作業ディレクトリで動いたかを判定する。ツールの実行結果に現れる git commit の出力からは SHA と commit message を取り出し、squash merge で GitHub API から取得できない commit を補う（[Session Write to DynamoDB](#session-write-to-dynamodb)）

転送対象の session は、マーカーファイル（`.ayumy_last_sync`）との mtime 比較で決定する

- マーカーが存在しない場合（初回）は全 JSONL を対象とする
- スキャン前に一時マーカー（`.ayumy_last_sync.tmp`）を作成し、転送成功後に `mv` で本マーカーに昇格させる
- これにより、転送中に更新されたファイルが次回検出漏れしないようにする

### Transfer Script
hook と手動実行の両方から呼ばれる共通スクリプトで、受け付けるオプションは `--help` で確認できる。転送は session ID 単位の上書きで行い、同じ JSONL を何度送っても結果が変わらないようにする

#### Project Resolution
`--cwd` と引数なしでは、渡されたディレクトリに対応するプロジェクトを探して転送する。該当するのは次のいずれかに当たるプロジェクトで、複数が該当すればすべてを転送し、1 つも該当しなければその旨を表示して正常終了する

- JSONL が最初に記録する `cwd` が、渡されたディレクトリと一致する
- 渡されたディレクトリのパスから組み立てた名前がプロジェクトディレクトリ名と一致し、かついずれかの session が渡されたディレクトリを `cwd` として記録している
- `cwd` の照合で 1 つも該当せず、パスから組み立てた名前のディレクトリが `cwd` を 1 つも記録していない古い session だけを持つ

判定の基本を最初の `cwd` に置くのは、Claude Code がディレクトリ名を作る際に置換する文字が公開されておらず、パスから組み立てた名前では worktree のようなドットを含むパスを取りこぼすためである。ただし session の途中で作業ディレクトリを移ると、Claude Code はその session を移動先のパスから名付けたプロジェクトに記録し、最初の `cwd` は移動前のディレクトリのまま残る。この場合に限って名前を手掛かりにするが、名前はドットとパスの区切りの違いしかない別のパスと重なり得るため、`cwd` の記録と組み合わせる

#### Repository Name
リポジトリ名は転送前に `.ayumy_repo` へ記録する

- `--repo` で名前を渡された場合はそれを記録する。ただし session の途中で移ってきたことで該当したプロジェクトには、記録が無いときだけ書く。Lambda は最初の `cwd` を基準に別リポジトリでの作業を除外するため、リポジトリ名も移動前のディレクトリ側に揃える
- 記録の無いプロジェクトでは、session が `cwd` として記録するディレクトリの origin remote から解決する。ディレクトリが消えた worktree や Git 管理外で開いた session では解決できない
- 解決できないプロジェクトは転送せず、飛ばしたことを表示する。Lambda 側でも取り込まずに飛ばすため、転送しても削除対象に入らないまま S3 に残り続けるからである

### Pre-push Hook
`ayumy/hooks/pre-push` として管理し、各リポジトリの `.git/hooks/pre-push` にシンボリックリンクで配置する

hook はリポジトリのルートと push 先のリポジトリ名を `sync_session.sh --cwd {dir} --repo {name}` としてフォアグラウンドで呼び出すラッパーである。プロジェクトの特定とリポジトリ名の記録は転送スクリプト側が担う

- 転送に失敗した場合は非ゼロ終了で push を中止する。これにより AWS 認証切れなど upload 不能な状態を push 時点で顕在化させる
- 当該リポジトリに対応する Claude session が存在しない場合はその旨を表示して exit 0 とし、push を通す
- リポジトリ名は push 先の remote URL から解決する。remote URL を引けない場合は `--repo` を渡さず、記録は転送スクリプト側の解決に任せる

設置は `ayumy setup-hooks` が担う。過去に同コマンドが作成した旧 `post-commit` symlink は、参照先が一致するものに限って併せて除去する。手動で別のパス表記を使って設置した hook は一致しないため対象外で、運用者が自分で削除する

設置では hook を置いたあと、そのリポジトリの Notion ページアイコン（[Database Properties](#database-properties)）を対話で尋ね、設定ファイルの対応表に追記する。リポジトリを追加したときに設定が漏れないよう指定を必須とし、答えが空または色が不正なら非ゼロで終了する。既に設定があるリポジトリには尋ねず、origin remote が無いリポジトリはアイコンを紐づける先が無いため警告して飛ばす。設定ファイルが未生成のまま追記すると中身がアイコン 1 行だけのファイルになるため、その場合は生成用の make target を伝えて非ゼロで終了する

設置先は Git に hook の参照先を問い合わせて決めるため、通常のリポジトリに加えて worktree やサブモジュールでも同じ手順で設置できる。worktree で実行した場合は共通ディレクトリに設置され、同じリポジトリのすべての worktree に効く

### Security Notes
- JSONL には会話の生データが含まれるため、会話中やツール実行時に機密情報（API キー、パスワード等）をログに残さないよう注意する
- S3 バケットはパブリックアクセスブロックを有効化し、IAM ポリシーで自アカウントのみにアクセスを制限する
- S3 のサーバーサイド暗号化（SSE-S3）を有効化する
- 必要に応じて特定プロジェクトを除外するフィルタリング機能を設ける

## Stage 2: Data Integration, Summarization, and Notion Writing
### GitHub Activity Fetch
対象リポジトリは S3 上の session ログから特定する。各プロジェクトディレクトリの `.ayumy_repo` メタデータファイルからリポジトリ名を読み取り、そのリポジトリだけを取得対象とする。所有するリポジトリをすべて調べないのは、session の無いリポジトリまで API を呼ぶ必要が無いためである

commit は Search API で対象期間を指定して集め、PR と Issue は更新日時を起点に取得する。対象日終了時点で未完了の Issue に限り、timeline も取得して参照元の PR を集める（[Status Sections](#status-sections)）。Search API の呼び出しは secondary rate limit を受けるため、一定時間あたりのリクエスト数を共通の throttle で抑える

#### Target Window
対象期間は実行方式によって異なる

| Execution Mode | Range |
|---|---|
| 定期実行（EventBridge） | 前日 JST 00:00:00 〜 当日 JST 00:00:00 |
| 手動実行（`ayumy sync --report`） | 当日 JST 00:00:00 〜 現在時刻 |
| 日付指定（`ayumy sync --report --date DATE`） | 指定日 JST 00:00:00 〜 翌日 JST 00:00:00（各日付ごと） |

Lambda event の `source` が `"manual"` なら手動実行、それ以外なら定期実行として扱う。`target_date` が指定されている場合は `source` に関わらず指定日ごとに全日範囲を順に処理し、未レポート日の自動検出（backfill）は行わない

#### Commit Fetch
GitHub Search の date 比較は UTC 解釈のため、JST の 1 日分は連続する 2 つの UTC 日付にまたがる。検索範囲を JST 境界より広く取り、取得後にタイムゾーン対応の `since <= author_date < until` で絞り込む。手動実行時の部分日もこの絞り込みで扱える

Search は squash merge 後にブランチが削除された PR の commit を返さない。これを補うため、取得した PR ごとに commit 一覧も取得し、同じ絞り込みをかけてから SHA 単位で Search の結果と統合する。どちらの経路でも取れなかった commit は、session JSONL の `tool_result` から抽出した commit 情報で補完する（[Session Write to DynamoDB](#session-write-to-dynamodb)）

各 commit には紐づく PR 番号を付与する。この番号は Notion Timeline で commit を親 PR ブロック配下にネストする際の参照キーになり、Search で取得した commit の番号は Hybrid 経路の PR 取得にも使う

#### Hybrid Backfill Fetch
PR/Issue の `updated_at` 経路は対象日以降に状態が更新されると `updated_at` がウィンドウから外れて取得対象から漏れる。例えば T 日に open された PR が T+1 日に merge された場合、T 日の再生成では PR が取得できず Timeline に PR ブロックが現れない

通常運用（前日定期実行・手動当日実行）では影響軽微なため `updated_at` 経路を維持する。`target_date` 指定時、またはバックフィル検出（[Session Log Read](#session-log-read)）で見つかった未レポート日に対しては Hybrid 経路に切り替える

どちらも `GET /search/issues` のレンジクエリで状態遷移したものを集め、そこに下表の番号を合わせて 1 つのリストにまとめる。`session_pulls` / `session_issues` は DynamoDB から読む（[Session Write to DynamoDB](#session-write-to-dynamodb)）

| Activity | Search Query | Additional Numbers | Individual Fetch |
|---|---|---|---|
| Pull Requests | `is:pr` + `created:` / `merged:` / `closed:` を 3 回 | commit に付与済みの関連 PR 番号と `session_pulls` | 全番号を `GET /repos/{owner}/{repo}/pulls/{N}` で取得 |
| Issues | `is:issue` + `created:` / `closed:` を 2 回 | `session_issues` | Search に含まれない番号のみ `GET /repos/{owner}/{repo}/issues/{N}` で取得し、PR が返ったものは除外 |

Search クエリの日付範囲は UTC/JST の境界ずれを吸収するため広めに取り、取得後に `created_at` / `merged_at` / `closed_at` のいずれかが `[since, until)` に入るものへ絞り込む。削除済み PR/Issue は 404 となるためスキップする

次の 2 つは別の根拠で採用するため、この絞り込みの対象外とする

- commit 由来 PR — 対象日に commit が存在する事実をもって採用する
- session 由来 PR/Issue — session 中に対象日に操作された事実をもって採用する

### Session Log Read
DynamoDB の `ayumy-sessions` テーブルから対象日付をパーティションキーとして Query し、session メタデータを取得する。結果をリポジトリ別にグルーピングし、各リポジトリ内の session を `start_time` 順にソートする

バックフィル検出は DynamoDB の Scan で行う。`reported_at` が未設定、または `updated_at > reported_at` のアイテムが存在する過去日付を対象とする（最大3日分）。更新された session は `updated_at` が `reported_at` を超えるため、自動的に再生成対象となる

### Session Write to DynamoDB
レポート生成の前処理として、S3 上の未アーカイブ JSONL をパースし、session メタデータを DynamoDB に書き込む。日付フィルタなしで全エントリを処理し、JST 日付ごとにグルーピングする

テーブル名は `ayumy-sessions`、オンデマンドモードかつ PITR 有効

<details>
<summary>DynamoDB Table Schema</summary>

| Key | Attribute | Type | Description |
|---|---|---|---|
| PK | `date` | String | JST 日付（`YYYY-MM-DD`） |
| SK | `repo#session_id` | String | リポジトリ名 + session ID |
| | `repo` | String | リポジトリ名 |
| | `project` | String | プロジェクトディレクトリ名 |
| | `start_time` | String | ISO 8601 |
| | `end_time` | String | ISO 8601 |
| | `user_messages` | List | ユーザーメッセージ |
| | `tools_used` | List | 使用ツール |
| | `session_commits` | List | session 中の git commit 結果（`[{sha, message, timestamp}]`、未検出時は空リスト） |
| | `session_pulls` | List | session 中の Bash tool 操作で言及された PR 番号のソート済みリスト（未検出時は空リスト） |
| | `session_issues` | List | session 中の Bash tool 操作で言及された Issue 番号のソート済みリスト（未検出時は空リスト） |
| | `content_hash` | String | 内容属性から算出したハッシュ（`updated_at` / `reported_at` は対象外） |
| | `updated_at` | String | ISO 8601、書き込み・更新時刻 |
| | `reported_at` | String | ISO 8601、レポート生成時刻（未生成時は未設定） |

</details>

書き込み時の動作

- 同一キー（PK + SK）のアイテムは、内容が変わっていれば上書きされる（冪等性を担保）
- 保存済みの `content_hash` と一致するアイテムは書き込まない。日をまたいで続く session は push のたびにファイル全体が再アップロードされるため、内容が変わっていない過去日まで `updated_at` が新しくなり、バックフィル検出（[Session Log Read](#session-log-read)）が同じ日を繰り返し拾ってしまうのを防ぐ
- ユーザーメッセージも `session_commits` もないグループはスキップする
- リポジトリ名は `.ayumy_repo` メタデータファイルから解決する。メタデータがないプロジェクトはスキップする
- assistant の Bash tool_use のコマンドから PR/Issue 番号を抽出する。`gh` / `git` の引数として PR/Issue を明示的に操作した箇所のみが対象で、本文中で言及されただけの URL や `#番号` はノイズとなるため対象外とする。`git` 由来の番号は PR/Issue の種別を判別できないため両方の候補として保持し fetch 側で振り分ける（[Hybrid Backfill Fetch](#hybrid-backfill-fetch)）
- 抽出は project の作業ディレクトリ内で実行されたコマンドのみを対象とする。Bash tool の冒頭で `cd <他 repo path> && ...` により別ディレクトリへ移動した場合、そのコマンド由来の commit / PR / Issue 番号は除外する。`cd` のパス指定が解決できない形式（別ユーザーの `~user/...` 等）も project 外として扱う
- 除外した commit / PR / Issue 番号は、作業先のリポジトリにも振り分けない。Lambda は作業先のパスからリポジトリ名を引けず、ユーザーメッセージもどのリポジトリでの作業かを切り分けられないためである。会話は session を開いた側のリポジトリに残り、別リポジトリでの作業は作業先で session を開き直す運用で扱う
- 書き込み成功後、処理した JSONL を S3 から削除する。ハッシュの一致で書き込みを落としたアイテムも取り込み済みとして扱う。書き込み失敗時は S3 を削除せず、次回実行時に再試行する。書き込み失敗時も DynamoDB に前回成功分のデータが残っているため、レポート生成フローは継続する

レポート生成後の動作

- 対象日付の全アイテムの `reported_at` を現在時刻に更新する
- これによりバックフィル検出（[Session Log Read](#session-log-read)）が同じ日を再び拾わなくなる

### Summary Generation
GitHub アクティビティと Claude Code session ログの両方をコンテキストとして渡し、リポジトリごとの要約を生成する。GitHub アクティビティは Notion 本文と同じ対象日基準で絞り、対象日に完了していないアイテムを完了として渡さない。文体は常体で統一し、ですます調は使用しない

- **リポジトリ別の要点**: 各リポジトリで行われた作業の要点を 2〜5 項目の箇条書きで記述する。最初の項目はそのリポジトリの最重要の要点として単独でも通じる内容にする（Slack 通知ではこの項目を 1 文サマリとして流用する）
- **Claude Code での作業**: 上記の要点の中に Claude Code session での相談・実装方針の検討内容も含めて構わない
- PR/Issue のステータス別一覧と時系列のイベントは Notion 本文の生成時にプログラムで組み立てるため、Claude API の出力には含めない

番号と識別子の書き方は [システムプロンプト](../lambda/report/prompts/summary_system.txt) で指定し、そちらを single source of truth とする。装飾として使わせる記法は [Page Body の Summary](#summary) が解釈するものに揃える

出力がルールから外れた場合の後処理は設けない。種別の前置を機械的に削ると「その PR #155 では」のような自然な文まで削ることになり、表示が冗長になる程度の実害と釣り合わない

出力にはリポジトリごとの作業要点（箇条書き）とタグの提案を含める

対象日に GitHub アクティビティがなく session ログだけがあるリポジトリ（以下 session-only）は Claude API 入力から除外する。Notion ページを作成しない現状仕様（[Database Properties](#database-properties)）で捨てられる要約分の API コスト発生を抑えるため

- 判定は session 由来 commit を GitHub アクティビティにマージした後の状態で行う
- 対象日の全リポジトリが session-only の場合は Claude API 呼び出し自体を skip し、コスト記録も残さない
- session-only 発生時の Slack 通知での扱いは [Slack Notification](#slack-notification) に従う
- session store 側の "reported" スタンプは通常通り打つ。翌日以降 push で追いつけば `updated_at > reported_at` の backfill 判定でレポート生成が再走する

Claude API の応答構造が想定を逸脱した場合、要約生成は原因を含む例外を投げ、[Classification Policy](#classification-policy) に沿って当該日のレポート生成を失敗させる。その場での自動再試行は挟まない。当該日は未報告のまま残るため、後続の実行で補完対象になる。すぐに作り直したい場合は、運用者が `ayumy sync --report` で明示的に再実行する。検証範囲は必須項目と型に限定する

### Slack Notification
Notion への書き込み完了後、Slack Web API の `chat.postMessage` で指定チャンネルに通知を送信する。通知が失敗しても処理全体は正常終了とする（通知はベストエフォート）

#### Notification Content
- Notion ページへのリンク（リポジトリごとに 1 行）。Claude API が生成した summary 箇条書きの先頭項目がある場合は 1 文サマリとしてリンクの後ろに付加する。Notion 側と同じ記法（[Page Body の Summary](#summary)）を解釈して mrkdwn に変換し、Slack の特殊記法を無効化するエスケープを済ませてから置き換える
- 実行メトリクス: ayumy バージョン、経過時間（Lambda 実行時は timeout との比率）、ピークメモリ（Lambda 実行時は memory limit との比率）
- Claude API コスト: 今回の実行の利用金額、当月累計・前月同期間比、当月の Claude API 呼び出し回数・前月同期間比。実行メトリクスと同じ context block に統合して 1 行で表示する。前月データが無く比率を計算できない項目は `(MoM ...)` 部分を丸ごと省略する（永続化された履歴の詳細は [Cost Execution Log Persistence](#cost-execution-log-persistence)）

アクティビティが 0 件で Notion ページが作成されなかった場合は、正常稼働を示す簡易通知を送れる。処理中にエラーが発生した場合はエラー内容を通知する

残り実行時間が閾値を下回って処理を打ち切った場合は、打ち切った日付と理由を通知する（[Abrupt Termination](#abrupt-termination)）

session-only の扱い（[Summary Generation](#summary-generation)）に応じて表示を分ける

- 対象日の全リポジトリが session-only の場合は session-only 専用の簡易通知を送る
- 部分的 session-only の場合は通常の Daily Report 通知の下部に session-only リポジトリ名を context として付記する

アクティビティ 0 件と全リポジトリ session-only の簡易通知は、config でそれぞれ送るかどうかを切り替える。デフォルトではアクティビティ 0 件の通知を送らず、session-only の通知は送る。Daily Report のヘッダーを持つ通知が 1 件も無い run では、実行メトリクスだけのメッセージも送らない

#### Run Origin Labels
Daily Report のヘッダー末尾には実行の由来を示すラベルを付ける。手動実行では `[manual]`、未報告日の補完では `[backfill]` を並べ、両方に該当する場合は `[manual] [backfill]` となる。定期実行で補完対象でない日を処理した場合は無印とし、通常運用時の見た目を変えない

- Daily Report のヘッダーを持つ通知すべてに同じ規則で付ける
- `[backfill]` は GitHub の取得経路を切り替える判定（[Hybrid Backfill Fetch](#hybrid-backfill-fetch)）をそのまま流用する。日付を明示指定した手動実行は指定日すべてが補完扱いとなるため、当日を指定した場合も付く
- ヘッダーは代替テキストにも流用されるため、プッシュ通知のプレビュー段階でも由来を判別できる

#### Message Splitting
日付範囲を指定した一括実行では、日数分の通知が 1 メッセージに積み上がる。Slack の 1 メッセージあたりのブロック数上限を超える場合は、複数のメッセージに分けて channel に連投する

- 分割は日単位の境界でのみ行い、1 日分の通知が 2 つのメッセージにまたがらないようにする
- 実行メトリクスは最後のメッセージに載る
- Block Kit を解釈しないクライアント向けの代替テキストも同じ切れ目で分割する

#### Warning Thread
Classification Policy で warning に分類した失敗は 1 run 単位で集約する。上記の親メッセージを送信した後、その最後のメッセージの `ts` を `thread_ts` に指定して thread 返信として投稿する。運用者は CloudWatch の `logger.warning` 出力に加え、Slack の thread でも警告を把握できる

- 集約は明示的な呼び出しで行い、logging のハンドラ経由で自動収集しない。第三者ライブラリが出力する warning まで拾ってしまうためである
- thread 投稿の本文は発生元ごとにまとめ、各項目の件名と関連識別子（commit SHA、PR 番号、S3 key 等）を並べる
- 集約 warning が 0 件の run では thread 投稿しない
- thread 投稿がブロック数上限を超える場合は複数の返信に分割する
- 親メッセージが 1 件も無い run では、集約 warning を単独のメッセージとして投稿する。ブロック数上限で分割した続きは、その最初のメッセージへの thread 返信にする
- thread 投稿の失敗は親通知の成功を壊さないよう独立して suppress する（ベストエフォート方針を継承）

### Cost Execution Log Persistence
Claude API 呼び出しのコスト管理として、要約生成のたびに 1 実行 = 1 record を DynamoDB に永続化する

テーブル名は `ayumy-costs`、オンデマンドモードかつ PITR 有効

<details>
<summary>DynamoDB Table Schema</summary>

| Key | Attribute | Type | Description |
|---|---|---|---|
| PK | `year_month` | String | 実行時刻の JST 月（`YYYY-MM`）、月次 Query の効率化用 |
| SK | `sk` | String | `<実行 JST date>#<executed_at>` 形式で日時順に並ぶ |
| | `target_date` | String | 対象レポート日（JST `YYYY-MM-DD`） |
| | `executed_at` | String | ISO 8601 UTC、Lambda 実行時刻 |
| | `model` | String | 実行時の Claude モデル ID |
| | `input_usd_per_1m_tokens` | Number | 実行時の入力単価 |
| | `output_usd_per_1m_tokens` | Number | 実行時の出力単価 |
| | `input_tokens` | Number | 入力トークン数 |
| | `output_tokens` | Number | 出力トークン数 |
| | `spend_usd` | Number | 単価 × トークン数を実行時に計算した USD |

</details>

`year_month` と SK の日付部分は **実行時刻の JST** を基準に決まる（対象レポート日ではない）。理由は backfill 実行のコストも「支払いが発生した実行月」に含めることで、Anthropic の請求サイクルと Slack 表示（当月累計）を一致させるため

書き込みは要約生成（[Summary Generation](#summary-generation)）の成功時に PutItem で全 attribute を 1 度書き込む。1 行 = 1 回の Claude API 呼び出しに対応するため、Slack に表示する月次「Claude API 呼び出し回数」は当月・前月同期間の行数をそのまま集計すればよい

`model` と単価を行ごとに保持することで、期中でモデル差し替えや pricing 改定が起きても実行時点の値を遡って再解釈しない。過去分は無期限に保持し、TTL は設定しない

DynamoDB は SUM / COUNT 相当の集計関数を提供しない。Slack 通知に表示する月次メトリクスは、当月・前月同期間の各行を Query で取得したうえでアプリケーション側で集計する

### Processed JSONL Cleanup
DynamoDB への書き込みが正常に完了した後、処理した JSONL ファイルを S3 から削除する。削除対象は `ingest` で処理したオブジェクトキーに限定し、処理中に到着した遅延ファイルが誤って削除されるのを防ぐ。session データは DynamoDB に永続化されているため、JSONL の保持は不要

## Notion Database Specification
### Database Properties
Date × Repository 単位でページを作成する。1日に複数ページが生成される。再実行時は対象日の既存ページをアーカイブ（soft-delete）してから再作成し、冪等性を担保する。GitHub activity に存在しないリポジトリは Notion ページを作成しない

アーカイブしたページはクエリから辿れなくなるため、作り直した回数はアーカイブの直前に Repository 単位で読み取り、1 を足した値を新しいページに引き継ぐ。対象日にページが無いリポジトリは初回の生成として `0` から数える

| Property | Type | Description | Example |
|---|---|---|---|
| Name | Title | 日付とリポジトリ名 | `26-03-01: ayumy` |
| Date | Date | 対象日 | `2025-03-01` |
| Repository | Select | リポジトリ名 | `ayumy` |
| Tags | Multi-select | 作業内容の分類タグ | `feature`, `refactor` |
| Commits | Number | 対象日の commit 数 | `5` |
| Merged | Number | 対象日にマージした PR 数 | `2` |
| Closed | Number | 対象日にクローズした Issue 数 | `1` |
| Sessions | Number | リポジトリの session 数 | `3` |
| Regens | Number | ページを作り直した回数（初回生成は `0`） | `2` |
| Version | Text | レポート生成時の ayumy バージョン | `0.2.0` |

ページには絵文字ではなく Notion 組み込みのアイコンを設定し、データベースの一覧でリポジトリを見分けられるようにする。アイコンと色はリポジトリごとに `lambda/config/config.yml` の `notion` セクションで指定し、エントリの無いリポジトリにはデフォルトのアイコンを当てる。名前は Notion のアイコンピッカー上の表示名を受け付け、実在しない名前は API がエラーを返す

### Page Body
Notion ページの本文は Summary、ステータス別セクション、Timeline で構成する。Summary は Claude API が生成し、それ以外は GitHub アクティビティから決定論的に組み立てる。ブロックタイプは `heading_2` と `bulleted_list_item` を使い分け、Timeline では `bulleted_list_item` の `children` フィールドで PR 配下の commit をネストする

```
[heading_2]            Summary
[bulleted_list_item]   リポジトリの作業要点（2〜5項目）
[heading_2]            Done（該当がある場合のみ）
[bulleted_list_item]   対象日に完了した PR や Issue
[heading_2]            In Progress（該当がある場合のみ）
[bulleted_list_item]   作業中の PR や Issue（draft PR を含む）
[heading_2]            TODO（該当がある場合のみ）
[bulleted_list_item]   PR から参照されていない Issue（バックログ）
[heading_2]            Timeline（該当がある場合のみ）
[bulleted_list_item]   PR ブロック親 + 配下 commit を `children` でネスト、直接 commit と Issue open / close を最上位に時系列で混ぜて配置
```

GitHub アイテムへのリンクは PR / Issue が `#xx: Title`、commit が `{sha-prefix}: {commit message}` の形式とし、それぞれ GitHub URL でリンク化する。ページはリポジトリ単位で作られ、リポジトリ名は Repository プロパティと Name タイトルに出るため、本文の各行では省く

#### Row Symbols
Summary を除く各行の行頭には記号を置く。形が行の種別を、色が状態を指す。色は GitHub が PR / Issue の状態に使う色に合わせ、読み手が GitHub 上の見え方から状態を推測できるようにする。記号の一覧は [Manual.md](Manual.md) に置く

PR / Issue の状態判定は [Status Sections](#status-sections) と共通とする。ウィンドウより前に完了した PR は Timeline にのみ残るため、その場合は完了時の状態を使う

#### Summary
各項目は Claude が生成した文字列をそのまま載せず、インラインコード・太字・番号参照の 3 種の記法を解釈して rich_text に展開する。記法は先頭から順に切り出して入れ子にせず、ある記法の内側に書かれた記号は解釈せずそのまま残す。番号参照はリポジトリ名を伴わなければページのリポジトリ、伴えばそのリポジトリへリンクし、表示するテキストは書かれたまま残す

上記以外の記法は記号のまま表示される。書かせない側の担保は [Summary Generation](#summary-generation) の生成ルールに持たせる

#### Status Sections
ステータスは取得時点の state ではなく、完了時刻が対象日ウィンドウ内かで振り分ける。完了時刻は PR なら merge、マージされず close された PR と Issue なら close の時刻を指す。対象日より前に完了したアイテムは、session 内での言及や close 後の更新で取得対象に入っただけであるためいずれのセクションにも載せない

- Done: 対象日に完了した PR / Issue
- In Progress: 対象日終了時点で未完了の PR（draft 含む）、対象日終了時点までに PR から参照された未完了 Issue
- TODO: 対象日終了時点で未完了で、PR から参照されていない Issue

未完了 Issue の振り分けは作成日ではなく、PR から参照されたかで決める。日付が変わっても着手したとは限らず、作成した当日に着手することもある。参照は Issue の timeline に記録されるイベントから取る。複数 PR で対応する Issue の PR には closing keyword を付けない運用に合わせ、closing keyword の無い参照も数える

数えるのは同じリポジトリの PR からの参照に限る。他リポジトリの PR はページに載らず、In Progress に入った理由をページから読み取れないためである。対象日終了時点より後の参照も数えず、過去日を再生成しても結果が変わらないようにする。参照元の PR の状態は問わず、PR がすべて merge 済みでも In Progress に残す。これにより、複数 PR で対応する Issue が PR の合間に TODO へ戻ることはない

各項目には [Row Symbols](#row-symbols) の記号を prefix として付ける。Done の Issue のうち `not_planned` / `duplicate` で close されたものは同じ記号を共有するため、記号に続けて `(理由) ` を添えて区別する

#### Timeline
Timeline は `bulleted_list_item` のネスト構造で表現する。PR 親エントリは `{状態記号} #xx: Title` の形式で表示し、その PR に紐づく commit を `children` フィールドにネストする。配下は commit の時刻順に並べ、merge commit（PR の `merge_commit_sha` と一致する commit）だけは時刻によらず末尾へ固定する。merge 方式によって merge commit の author 時刻が PR 内 commit より早くなる場合があり、末尾固定にすることで「配下の最後の行が merge」という読み方が保たれる

PR に関する行は親エントリ 1 か所に集約し、merge / close を示す行を最上位に別途置かない。PR が merge されたか close されたかは親エントリの記号が示す

最上位に置く要素の種類と表記は以下の通り

- 直接 commit（PR に紐づかない commit）: `🔸 sha: message`
- Issue open: `🟩 open: #xx: Title`
- Issue close（completed）: `🟪 close: #xx: Title`
- Issue close（not_planned / duplicate）: `⬜ close (理由): #xx: Title`

並び順は対象日ウィンドウ内における最初の活動時刻を基準に、PR ブロックと他のトップレベル要素を時系列で混ぜて並べる。PR ブロックの並び順キーは PR open（in range の場合）, 最初の配下 commit, merge 時刻, merge commit の時刻, close 時刻（merge されなかった場合）のうち最も早いものを採る。merge commit の時刻を含めるのは、merge が対象日ウィンドウの外へずれても、ウィンドウ内に入った merge commit を PR ブロックごと残すためである。同時刻のタイブレークは PR 親エントリを他のトップレベル要素より先に置く

### Tag Classification
タグは Claude API の要約生成時に自動判定させる。タグ名と判定基準（description）はコード側（[lambda/report/summarizer/tags.py](../lambda/report/summarizer/tags.py)）で single source of truth として管理する。Notion DB の multi-select オプションには description フィールドがないため、コード側に置いたうえで Claude API のシステムプロンプトに注入する

## AWS Lambda Configuration
### Lambda Execution Modes
レポート生成は AWS Lambda で実行する。日次の定期実行に加え、`ayumy sync --report` による手動実行にも対応する

定期実行では毎日 JST 00:00（UTC 15:00）に EventBridge Scheduler が Lambda 関数を呼び出す

手動実行は、クライアント側が Lambda に渡す event で定期実行と区別する

- 実行方式は `source` で示し、手動実行では `"manual"` を渡す
- 日付を指定した実行では `target_date` に単一日または範囲を渡す
- 対象期間の判定は [Target Window](#target-window) に従う
- 呼び出しは非同期で行い、実行結果は Slack 通知で確認する。同期呼び出しでは Lambda の実行時間が AWS CLI の read timeout を超えるとエラーになるためである

### Lambda Function Configuration
ランタイム・タイムアウト・メモリ・IAM ロールは [template.yaml](../template.yaml) で定義し、依存パッケージは `lambda/requirements` の各ファイルで管理する

値の置き場所は 3 つに分ける

- **config.yml**: モデル ID・API throttle 値・truncation 長など、利用者が振る舞いを調整する値。追跡対象はデフォルト値だけを持つ [config.template.yml](../lambda/config/config.template.yml) とし、利用者ごとの設定を書く `config.yml` は各自の手元で生成する。既にあるファイルは上書きしない
- **環境変数**: 環境ごとに変わる値。S3 バケット名・DynamoDB テーブル名・Notion データベース ID など
- **Secrets Manager**: 外部サービスの認証情報

config.yml には環境依存値と認証情報を書かない

## Operational Considerations
### Error Handling
#### Classification Policy
Lambda 側で発生する失敗は以下の 3 区分で扱う。`logger.warning` / `logger.error` / `raise` のいずれを選ぶかはこの分類に従う

- **error として raise**: 当日のレポート生成の正しさに直接影響する失敗。1 日分のデータが欠落・誤動作するもの。通常発生することが想定されない失敗は影響度合いによらず原則ここに分類する
- **warning として記録**: 部分的なデータ欠落で、レポート自体は生成できるが運用者が後追いすべき失敗
- **suppress**: 意図された不在を表すケース（プロジェクトに `.ayumy_repo` が無い、shlex 解析失敗で番号抽出を諦める 等）

`except Exception` は原則使わず、想定する具体例外型を捕捉する。broad catch を残すのは以下のグループのみとし、いずれも「なぜ broad か」を示す inline comment を 1 行付与する。同一グループ内で複数の箇所が該当する場合もある

- Lambda entrypoint（秘密情報の取得失敗をログに残して再送出するため）
- pipeline 最終 fallback（Slack 通知に届けるため）
- pipeline 日次 loop（1 日分の失敗を error / warning に分類するため）
- Slack 送信（ベストエフォート方針のため）

#### Abrupt Termination
上記 3 区分はいずれも Python の例外として捕まる失敗を前提とし、コード内でどう扱うかの判断規則になっている。Lambda のタイムアウト・メモリ超過・プロセスの強制終了では処理が最後まで到達せず、通知を送る後処理も走らないため、これらは分類の外側にある

このうちタイムアウトは残り実行時間から予測できる。各日の処理に入る前と要約生成に入る前に残り時間を確認し、閾値を下回っていれば通知を積んでから処理を打ち切る。閾値は config で調整する

- 確認を要約生成の前に置くのは、生成した後に打ち切ると Claude API の費用が無駄になるため
- 打ち切った日は未報告のまま残り、後続の実行で補完対象になる

メモリ超過とプロセスの強制終了は予測できないため、CloudWatch のアラームで事後に検知する。関数のエラーメトリクスを監視し、ALARM への遷移を通知トピック経由で Slack に流す

- 失敗が 1 件記録された時点で発報し、通知するのは ALARM への遷移のみとする
- 実行のない時間帯は欠測をデータ不足として扱い、翌日以降の障害も遷移として拾えるようにする
- Slack への配信は Amazon Q Developer in chat applications を経由する。ワークスペース側の認可は運用者の手作業となり、手順は [Setup.md](Setup.md) に置く
- Amazon Q に与える権限は、アラームの通知に要る CloudWatch の読み取りだけに絞る。channel の参加者は Amazon Q を通じてその権限の範囲で AWS を操作できるため、ログの読み取りは含めない

このメトリクスが拾うのはランタイム側の終了だけではない。Lambda entrypoint が 500 を返すのは失敗の通知が Slack に届いた場合に限り、届かないまま終わった失敗は再送出してメトリクスに残す

- 通知を積んだだけでは Slack が受け取った証拠にならないため、失敗で終わった run は送信が 1 件でも落ちていれば届かなかった側として扱う。警告として記録したうえで最後まで到達した run はこの判定の対象外とする
- 秘密情報の取得や対象日付の解釈など、Slack への通知経路が整う前に起きる失敗も同じく残る
