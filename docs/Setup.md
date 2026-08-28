# Setup Guide

## 1. AWS CLI
1. AWS CLI をインストール
2. `aws login` で認証（S3 への書き込み、Lambda の呼び出し、CloudFormation の操作権限が必要）

## 2. Notion
1. [Notion Integrations](https://www.notion.so/my-integrations) で Internal Integration を作成（Free plan で利用可能）
   - 機能: コンテンツの読み取り・挿入・更新を有効化
2. Notion にデータベースを作成し、Integration を接続
   - データベースページの URL から ID を取得: `https://www.notion.so/{database-id}?v=...`
3. データベースに [Spec: Database Properties](Spec.md#database-properties) のプロパティを作成する。Repository / Tags の select オプションはレポート書き込み時に自動追加されるため事前作成は不要だが、配色を制御したい場合は手動で追加する（Tags の option 名はコード側 [lambda/report/tags.py](../lambda/report/tags.py) を参照）

4. AWS Secrets Manager（ap-northeast-1）に登録
   - シークレットのタイプ: その他のシークレットのタイプ
   - シークレット名: `ayumy/notion-secret`
   - プレーンテキストで Integration トークンを貼り付け

## 3. Slack
1. [Slack API](https://api.slack.com/apps) で App を作成（From scratch）
2. OAuth & Permissions の Bot Token Scopes に `chat:write` を追加し、Install to Workspace で認可
3. 発行された Bot User OAuth Token（`xoxb-` で始まる）を控える
4. 通知先 channel を Slack クライアントで開き、`/invite @<app-name>` で bot を invite
5. channel 名をクリック → About タブ最下部の Channel ID（`C` または `G` で始まる）を控える
6. AWS Secrets Manager（ap-northeast-1）に Bot Token を登録
   - シークレットのタイプ: その他のシークレットのタイプ
   - シークレット名: `ayumy/slack-bot-token`
   - プレーンテキストで Bot User OAuth Token を貼り付け

## 4. AWS SAM
1. AWS SAM CLI をインストール: `brew install aws-sam-cli`
2. `sam build && sam deploy --guided` で初回デプロイを実行
   - Stack Name: `ayumy`
   - Region: `ap-northeast-1`
   - `NotionDatabaseId` に「2. Notion」で取得したデータベース ID を入力
   - `SlackChannelId` に「3. Slack」で取得した channel ID を入力
   - Confirm changes before deploy: `Y`
   - Allow SAM CLI IAM role creation: `Y`
   - Disable rollback: `N`
   - Save arguments to configuration file: `Y`
3. Outputs に表示される `ReportFunctionName` と `SessionBucketName` を控える（「7. クライアントマシン」で使用）

2回目以降のデプロイは `make lambda-deploy` のみでよい。デプロイ用 S3 バケットを変更する場合は `samconfig.toml` の `s3_bucket` を編集し、`sam deploy --no-resolve-s3` で実行する

## 5. GitHub PAT
1. GitHub Settings → Developer settings → Fine-grained personal access tokens で PAT を作成
   - Resource owner: 自分の個人アカウント
   - Repository access: All repositories
   - Repository permissions: Contents / Issues / Pull requests を Read-only
   - Expiration: 任意（有効期限は GitHub の Web UI で確認）
2. AWS Secrets Manager（ap-northeast-1）に登録
   - シークレットのタイプ: その他のシークレットのタイプ
   - シークレット名: `ayumy/github-pat`
   - プレーンテキストで `github_pat_...` の値をそのまま貼り付け

## 6. Anthropic API
1. [Anthropic Console](https://console.anthropic.com/) で API キーを発行（API は従量課金で、サブスクリプションプランとは別）
2. AWS Secrets Manager（ap-northeast-1）に登録
   - シークレットのタイプ: その他のシークレットのタイプ
   - シークレット名: `ayumy/anthropic-api-key`
   - プレーンテキストで API キーを貼り付け

## 7. クライアントマシン
クライアント側のスクリプト（`scripts/`, `hooks/`, `bin/ayumy`）は macOS のみサポートする

1. リポジトリをクローン: `git clone https://github.com/{user}/ayumy.git ~/ayumy`
2. PATH を通す: `export PATH="$HOME/ayumy/bin:$PATH"`（`~/.zshrc` 等に追加）
3. 環境変数を設定（`~/.zshrc` 等に追加）
   - `AYUMY_S3_BUCKET`: 「4. AWS SAM」の Outputs の `SessionBucketName`
   - `AYUMY_LAMBDA_FUNCTION`: 「4. AWS SAM」の Outputs の `ReportFunctionName`
4. hook を設置: 対象のリポジトリごとに `ayumy setup-hooks` を実行する
