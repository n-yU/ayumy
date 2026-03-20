.PHONY: lambda-install lambda-invoke

# Install lambda dependencies into local .venv via uv
lambda-install:
	test -d .venv || uv venv
	uv pip install --python .venv/bin/python -r lambda/requirements.txt

# Invoke Lambda function locally for testing
lambda-invoke:
	sam build && sam local invoke ReportFunction
