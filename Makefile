.PHONY: lambda-install lambda-invoke lambda-deploy

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
