.PHONY: lambda-install lambda-invoke lambda-deploy test oidc-deploy scan-sessions aws-auth-check

# Verify AWS credentials are valid before running AWS commands
aws-auth-check:
	@aws sts get-caller-identity > /dev/null 2>&1 || { \
		echo "AWS credentials are not valid or have expired. Run 'aws login' and retry."; \
		exit 1; \
	}

# Install lambda dependencies into local .venv via uv (includes dev deps like boto3)
lambda-install:
	test -d .venv || uv venv
	uv pip install --python .venv/bin/python -r lambda/requirements-dev.txt

# Invoke Lambda function locally for testing
lambda-invoke:
	sam build && sam local invoke ReportFunction

# Build and deploy Lambda function to AWS
lambda-deploy: aws-auth-check
	sam build && sam deploy --no-confirm-changeset

# Run unit tests
test: lambda-install
	.venv/bin/python -m pytest tests/ -v

# Scan DynamoDB session items
scan-sessions:
	aws dynamodb scan \
		--table-name ayumy-sessions \
		--projection-expression "#d, #rs, updated_at" \
		--expression-attribute-names '{"#d": "date", "#rs": "repo#session_id"}' \
		--output table

# Deploy OIDC bootstrap stack for GitHub Actions
oidc-deploy: aws-auth-check
	aws cloudformation deploy \
		--template-file .github/oidc-bootstrap.yml \
		--stack-name ayumy-github-oidc \
		--capabilities CAPABILITY_NAMED_IAM \
		--no-fail-on-empty-changeset \
		--region ap-northeast-1
