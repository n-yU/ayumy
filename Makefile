.PHONY: lambda-install lambda-invoke lambda-deploy test oidc-deploy

# Install lambda dependencies into local .venv via uv (includes dev deps like boto3)
lambda-install:
	test -d .venv || uv venv
	uv pip install --python .venv/bin/python -r lambda/requirements-dev.txt

# Invoke Lambda function locally for testing
lambda-invoke:
	sam build && sam local invoke ReportFunction

# Build and deploy Lambda function to AWS
lambda-deploy:
	sam build && sam deploy --no-confirm-changeset

# Run unit tests
test: lambda-install
	.venv/bin/python -m pytest tests/ -v

# Deploy OIDC bootstrap stack for GitHub Actions
oidc-deploy:
	aws cloudformation deploy \
		--template-file .github/oidc-bootstrap.yml \
		--stack-name ayumy-github-oidc \
		--capabilities CAPABILITY_NAMED_IAM \
		--no-fail-on-empty-changeset \
		--region ap-northeast-1
