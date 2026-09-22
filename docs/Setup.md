# Setup
本ドキュメントでは Ayumy を自身の環境で稼働するためのセットアップ手順を記載しています。Claude Code にこのドキュメントを参照するように依頼して構築を進めることを推奨します。稼働後の日常的な運用ガイドは [Manual.md](Manual.md) を参照してください。なお、ローカルマシンは macOS のみのサポートです

- [1. AWS CLI](#1-aws-cli)
- [2. Notion](#2-notion)
- [3. Slack](#3-slack)
- [4. Repository](#4-repository)
- [5. AWS SAM](#5-aws-sam)
- [6. GitHub PAT](#6-github-pat)
- [7. Claude API](#7-claude-api)
- [8. Local Machine](#8-local-machine)

## 1. AWS CLI
1. 自身の AWS 環境を用意し、AWS CLI をインストール
2. `aws login` 等で CLI 認証（S3 への書き込み、Lambda の呼び出し、CloudFormation の操作権限が必要）
3. 使用するリージョンを決め、AWS CLI のデフォルトリージョンに設定する。以降のシークレットの登録とデプロイは、すべてこのリージョンで行う

## 2. Notion
1. [Notion Integrations](https://www.notion.so/my-integrations) で Internal Integration を作成
   - Internal Integration は Free plan でも利用可能
   - 機能セクションの「コンテンツを読み取る」「コンテンツを更新」「コンテンツを挿入」にチェックを入れる
2. Integration Token を取得し AWS Secrets Manager に登録
   - シークレットのタイプ: その他のシークレットのタイプ
   - シークレット名: `ayumy/notion-secret`
   - プレーンテキストで Integration トークンを貼り付け
3. Notion にデータベースを作成して Integration を接続。データベースページの URL `https://www.notion.so/{database-id}?v=...` から ID を取得して控えておく（[§5.2](#5-aws-sam) で利用）
4. データベースに [Spec: Database Properties](Spec.md#database-properties) のプロパティを作成する
   - Repository / Tags の select オプションはレポート書き込み時に自動追加されるため事前作成は不要だが、配色を制御したい場合は手動で追加する
   - Tags のオプション名は [lambda/report/summarizer/tags.py](../lambda/report/summarizer/tags.py) を参照

## 3. Slack
1. [Slack API](https://api.slack.com/apps) で Create New App → Blank App で App を作成
2. 任意: Basic Information → Display Information に以下設定する
   - App icon に [docs/assets/logo/logo-symbol.png](assets/logo/logo-symbol.png) を設定
   - Background color に `#383A3D` を指定
   - Short description `Traces of daily craft, woven by AI` と入力（元々空欄だが App icon 変更により必須項目になる）
3. OAuth & Permissions → Bot Token Scopes に `chat:write` を追加し、Install to Workspace で認可
4. 発行された Bot User OAuth Token `xoxb-***` を AWS Secrets Manager に登録
   - シークレットのタイプ: その他のシークレットのタイプ
   - シークレット名: `ayumy/slack-bot-token`
   - プレーンテキストで Bot User OAuth Token を貼り付け
5. 通知先の channel を Slack クライアントで開き、`/invite @<app-name>` で bot を招待
6. channel 名をクリックし、About タブ最下部にある Channel ID（`C` または `G` で始まる）を控えておく（[§5.2](#5-aws-sam) で利用）
7. [Amazon Q Developer in chat applications](https://console.aws.amazon.com/chatbot/) で Slack を認可する
   - Lambda が停止したときのアラーム通知に利用する
   - Configure a chat client で Slack を選び、表示される Slack の認可画面で許可する
   - [§3.5](#3-slack) と同じ channel で `/invite @Amazon Q` を実行する
   - Workspace details に表示される Workspace ID を控えておく（[§5.2](#5-aws-sam) で利用）

## 4. Repository
1. リポジトリをクローン `git clone https://github.com/n-yU/ayumy.git ~/ayumy`。ホームディレクトリ直下以外にクローンした場合は、[§8.1](#8-local-machine) の PATH もそのパスに合わせる
2. クローンしたディレクトリで `make config-init` を実行し、設定ファイル `lambda/config/config.yml` を生成する
   - Lambda がこのファイルを読み込むため、生成しないままデプロイすると実行時に失敗する
   - 中身はデフォルト値のままでも動く。変更できる設定は [Manual: Config](Manual.md#config) を参照

## 5. AWS SAM
1. AWS SAM CLI をインストール: `brew install aws-sam-cli`
2. `sam build && sam deploy --guided` で初回デプロイを実行
   - Stack Name: `ayumy`
   - Region: AWS CLI のデフォルトリージョンと揃える (`ap-northeast-1` etc.)
   - `NotionDatabaseId` に [§2.3](#2-notion) で控えたデータベース ID を入力
   - `SlackChannelId` に [§3.6](#3-slack) で控えた Channel ID を入力
   - `SlackWorkspaceId` に [§3.7](#3-slack) で控えた Workspace ID を入力
   - `SessionBucketNameOverride` は空欄のままでよい。session ログを保管する S3 バケットが `ayumy-<アカウント ID>-<リージョン>-an` という名前で作られる
   - Confirm changes before deploy: `Y`
   - Allow SAM CLI IAM role creation: `Y`
   - Disable rollback: `N`
   - Save arguments to configuration file: `Y`
3. Outputs に表示される `ReportFunctionName` と `SessionBucketName` を控えておく（[§8.2](#8-local-machine) で利用）
4. （補足）
   - 2 回目以降のデプロイは `make lambda-deploy` だけで実行できる。デプロイ用の S3 バケットを変更する場合は、`samconfig.toml` の `s3_bucket` を編集してから `sam deploy --no-resolve-s3` を実行する
   - Ayumy を更新してパラメータが増えたときは、`samconfig.toml` の `parameter_overrides` に値を追記してからデプロイする

## 6. GitHub PAT
1. GitHub Settings → Developer settings → Fine-grained personal access tokens で PAT を作成
   - Resource owner: 自身の個人アカウント
   - Repository access: All repositories
   - Repository permissions: Contents / Issues / Pull requests を Read-only
   - Expiration: 任意（有効期限は GitHub の Web UI で確認）
2. 発行された PAT `github_pat_***` を AWS Secrets Manager に登録
   - シークレットのタイプ: その他のシークレットのタイプ
   - シークレット名: `ayumy/github-pat`
   - プレーンテキストで PAT をそのまま貼り付け

## 7. Claude API
1. [Claude Console](https://platform.claude.com/settings/keys) の "組織の設定" → "APIキー" よりキーを作成（注: API は従量課金制で、Claude のサブスクリプションプランとは別に請求される）
2. 発行された API キー `sk-ant-***` を AWS Secrets Manager に登録
   - シークレットのタイプ: その他のシークレットのタイプ
   - シークレット名: `ayumy/anthropic-api-key`
   - プレーンテキストで API キーを貼り付け

## 8. Local Machine
1. PATH を通す: `export PATH="$HOME/ayumy/bin:$PATH"`（`~/.zshrc` 等に追加）
2. 環境変数を設定
   - `AYUMY_S3_BUCKET`: [§5.3](#5-aws-sam) で控えた `SessionBucketName`
   - `AYUMY_LAMBDA_FUNCTION`: [§5.3](#5-aws-sam) で控えた `ReportFunctionName`
3. レポート対象にするリポジトリごとに `ayumy setup-hooks` を実行し、pre-push hook を設置
   - Notion ページのアイコンを尋ねられるため、Notion のアイコンピッカー上の名前とカラーを回答する
   - 回答内容は `lambda/config/config.yml` に追記され、`make lambda-deploy` を実行して反映する
