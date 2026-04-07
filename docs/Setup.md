# Setup Guide

## 1. AWS CLI
1. AWS CLI をインストール
2. `aws login` で認証（S3 への書き込み、Lambda の呼び出し、CloudFormation の操作権限が必要）

## 2. Notion
1. [Notion Integrations](https://www.notion.so/my-integrations) で Internal Integration を作成（Free plan で利用可能）
   - 機能: コンテンツの読み取り・挿入・更新を有効化
2. Notion にデータベースを作成し、Integration を接続
   - データベースページの URL から ID を取得: `https://www.notion.so/{database-id}?v=...`
3. データベースに以下のプロパティを作成する（Spec.md §6.1 参照）

| プロパティ名 | 型 | 備考 |
|---|---|---|
| Name | Title | デフォルトで存在。`YY-MM-DD: repo` 形式で自動設定 |
| Date | Date | |
| Repository | Select | オプションは自動追加される |
| Tags | Multi-select | 下記のオプションを事前登録 |
| Commits | Number | |
| PRs Merged | Number | |
| Issues Closed | Number | |
| Claude Sessions | Number | |

Tags のオプション: `feature`, `bugfix`, `docs`, `refactor`, `ci`, `review`, `productive`, `maintenance`, `blocked`, `light`

Tags のオプションは allowlist として機能する（Spec.md §6.3）。要約生成時にこれらのオプションが候補としてプロンプトに注入され、allowlist 外の値はバリデーションで除外される。オプションの追加・削除は Notion DB の UI から直接行う。

4. AWS Secrets Manager（ap-northeast-1）に登録
   - シークレットのタイプ: その他のシークレットのタイプ
   - シークレット名: `ayumy/notion-secret`
   - プレーンテキストで Integration トークンを貼り付け

## 3. AWS SAM
1. AWS SAM CLI をインストール: `brew install aws-sam-cli`
2. `sam build && sam deploy --guided` で初回デプロイを実行
   - Stack Name: `ayumy`
   - Region: `ap-northeast-1`
   - `NotionDatabaseId` パラメータに「2. Notion」で取得したデータベース ID を入力
   - Confirm changes before deploy: `Y`
   - Allow SAM CLI IAM role creation: `Y`
   - Disable rollback: `N`
   - Save arguments to configuration file: `Y`
3. Outputs に表示される `ReportFunctionName` と `SessionBucketName` を控える（「7. クライアントマシン」で使用）

2回目以降のデプロイは `sam build && sam deploy` のみでよい。デプロイ用 S3 バケットを変更する場合は `samconfig.toml` の `s3_bucket` を編集し、`sam deploy --no-resolve-s3` で実行する。

## 4. GitHub PAT
1. GitHub Settings → Developer settings → Fine-grained personal access tokens で PAT を作成
   - Resource owner: 自分の個人アカウント
   - Repository access: All repositories
   - Repository permissions: Contents / Issues / Pull requests を Read-only
   - Expiration: 任意（有効期限は GitHub の Web UI で確認）
2. AWS Secrets Manager（ap-northeast-1）に登録
   - シークレットのタイプ: その他のシークレットのタイプ
   - シークレット名: `ayumy/github-pat`
   - プレーンテキストで `github_pat_...` の値をそのまま貼り付け

## 5. Anthropic API
1. [Anthropic Console](https://console.anthropic.com/) で API キーを発行（API は従量課金で、サブスクリプションプランとは別）
2. AWS Secrets Manager（ap-northeast-1）に登録
   - シークレットのタイプ: その他のシークレットのタイプ
   - シークレット名: `ayumy/anthropic-api-key`
   - プレーンテキストで API キーを貼り付け

## 6. Slack
1. [Slack API](https://api.slack.com/apps) で App を作成（From scratch）
2. Incoming Webhooks を有効化し、通知先チャンネルを選択して Webhook URL を発行
3. AWS Secrets Manager（ap-northeast-1）に登録
   - シークレットのタイプ: その他のシークレットのタイプ
   - シークレット名: `ayumy/slack-webhook-url`
   - プレーンテキストで Webhook URL を貼り付け

※ レガシーな Incoming WebHooks App ではなく、Slack App の Incoming Webhooks 機能を使用する

## 7. クライアントマシン
1. リポジトリをクローン: `git clone https://github.com/{user}/ayumy.git ~/ayumy`
2. PATH を通す: `export PATH="$HOME/ayumy/bin:$PATH"`（`~/.zshrc` 等に追加）
3. 環境変数を設定（`~/.zshrc` 等に追加）
   - `AYUMY_S3_BUCKET`: 「3. AWS SAM」の Outputs の `SessionBucketName`
   - `AYUMY_LAMBDA_FUNCTION`: 「3. AWS SAM」の Outputs の `ReportFunctionName`
4. 対象リポジトリに hook を設置: `ayumy setup-hooks --all <repositories-dir>`
