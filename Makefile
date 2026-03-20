.PHONY: lambda-install lambda-invoke

# Install lambda dependencies into local .venv via uv
lambda-install:
	uv pip install -r lambda/requirements.txt

# Invoke Lambda function locally for testing
lambda-invoke:
	sam build && sam local invoke ReportFunction
