.DEFAULT_GOAL := help

.PHONY: help help-% lambda-install lambda-invoke lambda-deploy lock test test-python test-shell test-cov format format-check lint lint-fix oidc-deploy scan-sessions aws-auth-check

FORMAT_TARGETS := lambda tests

##@ Help

help: ## Show this help message
	@awk 'BEGIN {FS = ":.*?## "} \
		/^##@ / {printf "\n\033[1;33m%s\033[0m\n", substr($$0, 5); next} \
		/^[a-zA-Z_%-]+:.*?## / { \
			name = $$1; \
			if (name == "help-%") name = "help-<target>"; \
			printf "  \033[36m%-16s\033[0m %s\n", name, $$2 \
		}' $(MAKEFILE_LIST)

help-%: ## Show description and usage for <target>
	@awk -v target="$*" 'BEGIN {FS = ":.*?## "} \
		$$1 == target {printf "\033[36m%s\033[0m: %s\n", $$1, $$2; found=1} \
		END {if (!found) {printf "Target \"%s\" not found.\n", target; exit 1}}' $(MAKEFILE_LIST)
	@awk -v target="$*" '\
		$$0 ~ "^" target ":" {in_recipe=1; next} \
		in_recipe && /^\t# Usage:/ {sub(/^\t/, "  "); print; next} \
		in_recipe && /^\t/ {next} \
		in_recipe && /^[^\t]/ {exit}' $(MAKEFILE_LIST)

##@ Lambda

lambda-install: ## Install lambda deps into local .venv via uv (includes dev deps)
	test -d .venv || uv venv
	uv pip install --python .venv/bin/python -r lambda/requirements-dev.txt

lambda-invoke: ## Invoke Lambda function locally for testing
	sam build && sam local invoke ReportFunction

lambda-deploy: aws-auth-check ## Build and deploy Lambda function to AWS
	sam build && sam deploy --no-confirm-changeset

lock: ## Regenerate hash-pinned requirements*.txt from requirements*.in
	uv pip compile lambda/requirements.in --generate-hashes --python-version 3.12 --output-file lambda/requirements.txt
	uv pip compile lambda/requirements-dev.in --generate-hashes --python-version 3.12 --output-file lambda/requirements-dev.txt

##@ Test

test: lambda-install ## Run Python + shell tests together
	.venv/bin/python -m pytest tests/python/ -v
	bats tests/shell/

test-python: TARGET = tests/python/
test-python: lambda-install ## Run Python tests (override path via TARGET=...)
	# Usage: make test-python TARGET=tests/python/report/test_notion.py
	.venv/bin/python -m pytest $(TARGET) -v

test-shell: TARGET = tests/shell/
test-shell: ## Run shell tests via bats (override path via TARGET=...)
	# Usage: make test-shell TARGET=tests/shell/sync_session.bats
	bats $(TARGET)

test-cov: lambda-install ## Run Python tests with coverage measurement
	.venv/bin/python -m pytest tests/python/ \
		--cov=lambda --cov-report=term-missing --cov-report=xml

##@ Lint & Format

format: lambda-install ## Apply Ruff formatter and isort-equivalent import sort
	.venv/bin/python -m ruff format $(FORMAT_TARGETS)
	.venv/bin/python -m ruff check --fix --select I $(FORMAT_TARGETS)

format-check: lambda-install ## Check formatting and lint violations without modifying files
	.venv/bin/python -m ruff format --check --diff $(FORMAT_TARGETS)
	.venv/bin/python -m ruff check $(FORMAT_TARGETS)

lint: lambda-install ## Run Ruff lint checks
	.venv/bin/python -m ruff check $(FORMAT_TARGETS)

lint-fix: lambda-install ## Apply auto-fixable Ruff lint fixes
	.venv/bin/python -m ruff check --fix $(FORMAT_TARGETS)

##@ AWS

aws-auth-check: ## Verify AWS credentials are valid before AWS commands
	@aws sts get-caller-identity > /dev/null 2>&1 || { \
		echo "AWS credentials are not valid or have expired. Run 'aws login' and retry."; \
		exit 1; \
	}

scan-sessions: ## Scan DynamoDB session items
	aws dynamodb scan \
		--table-name ayumy-sessions \
		--projection-expression "#d, #rs, updated_at" \
		--expression-attribute-names '{"#d": "date", "#rs": "repo#session_id"}' \
		--output table

oidc-deploy: aws-auth-check ## Deploy OIDC bootstrap stack for GitHub Actions
	aws cloudformation deploy \
		--template-file .github/oidc-bootstrap.yml \
		--stack-name ayumy-github-oidc \
		--capabilities CAPABILITY_NAMED_IAM \
		--no-fail-on-empty-changeset \
		--region ap-northeast-1
