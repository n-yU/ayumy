#!/usr/bin/env bash
set -euo pipefail
umask 077

AYUMY_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$HOME/.ayumy.env"
CRON_ENTRY='0 15 * * * cd ~/ayumy && docker compose run --rm ayumy >> $AYUMY_DATA_DIR/logs/report.log 2>&1'

usage() {
  cat <<'USAGE'
Usage: ayumy setup-host [options]

Set up the host machine: create env file, build container, and configure cron.

Options:
  --skip-env    Skip env file creation
  --skip-cron   Skip cron configuration
  --help        Show this help message
USAGE
  exit "${1:-1}"
}

# Prompt for a value and return it.
# Arguments: $1 = variable name, $2 = description
prompt_var() {
  local var_name="$1"
  local description="$2"
  local value=""

  printf "%s (%s): " "$var_name" "$description" >&2
  read -r value
  echo "$value"
}

# Create ~/.ayumy.env interactively.
setup_env() {
  echo "=== Environment file setup ==="

  if [[ -f "$ENV_FILE" ]]; then
    printf "Existing %s found. Overwrite? [y/N]: " "$ENV_FILE"
    read -r answer
    if [[ "$answer" != [yY] ]]; then
      echo "Skipping env file creation."
      return 0
    fi
  fi

  local github_pat anthropic_api_key notion_token notion_database_id slack_webhook_url ayumy_data_dir

  github_pat="$(prompt_var "GITHUB_PAT" "GitHub Fine-grained PAT")"
  anthropic_api_key="$(prompt_var "ANTHROPIC_API_KEY" "Anthropic API key")"
  notion_token="$(prompt_var "NOTION_TOKEN" "Notion Internal Integration token")"
  notion_database_id="$(prompt_var "NOTION_DATABASE_ID" "Notion database ID")"
  slack_webhook_url="$(prompt_var "SLACK_WEBHOOK_URL" "Slack Incoming Webhook URL")"
  ayumy_data_dir="$(prompt_var "AYUMY_DATA_DIR" "NAS data directory mount path")"

  if [[ -n "$ayumy_data_dir" && ! -d "$ayumy_data_dir" ]]; then
    echo "WARNING: directory does not exist: $ayumy_data_dir" >&2
    echo "Ensure the NAS is mounted before running the container." >&2
  fi

  cat > "$ENV_FILE" <<EOF
GITHUB_PAT=${github_pat}
ANTHROPIC_API_KEY=${anthropic_api_key}
NOTION_TOKEN=${notion_token}
NOTION_DATABASE_ID=${notion_database_id}
SLACK_WEBHOOK_URL=${slack_webhook_url}
AYUMY_DATA_DIR=${ayumy_data_dir}
EOF

  chmod 600 "$ENV_FILE"
  echo "Created $ENV_FILE"
}

# Build container and run a basic smoke test.
setup_container() {
  echo "=== Container build & test ==="

  if [[ ! -f "$ENV_FILE" ]]; then
    echo "ayumy setup-host: env file not found: $ENV_FILE" >&2
    echo "Run without --skip-env first, or create $ENV_FILE manually." >&2
    return 1
  fi

  cd "$AYUMY_ROOT"

  if ! docker compose build; then
    echo "ayumy setup-host: container build failed" >&2
    return 1
  fi

  # Verify env file is loaded and data volume is accessible.
  echo "Running container smoke test..."
  if ! docker compose run --rm ayumy sh -c '
    ok=true
    for var in GITHUB_PAT ANTHROPIC_API_KEY NOTION_TOKEN NOTION_DATABASE_ID AYUMY_DATA_DIR; do
      eval val=\$$var
      if [ -z "$val" ]; then
        echo "FAIL: $var is not set"
        ok=false
      fi
    done
    if [ ! -d "$AYUMY_DATA_DIR" ]; then
      echo "FAIL: $AYUMY_DATA_DIR does not exist (is the NAS mounted?)"
      ok=false
    fi
    if [ "$ok" = true ]; then
      echo "OK: all environment variables set and data directory accessible"
    else
      exit 1
    fi
  '; then
    echo "ayumy setup-host: container smoke test failed" >&2
    return 1
  fi
}

# Add cron entry for daily execution.
setup_cron() {
  echo "=== Cron setup ==="

  local existing
  existing="$(crontab -l 2>/dev/null || true)"
  if echo "$existing" | grep -qF "docker compose run --rm ayumy"; then
    echo "Cron entry already exists. Skipping."
    return 0
  fi

  printf "Add daily cron job (JST 00:00)? [Y/n]: "
  read -r answer
  if [[ "$answer" == [nN] ]]; then
    echo "Skipping cron setup."
    return 0
  fi

  (echo "$existing"; echo "$CRON_ENTRY") | crontab -
  echo "Cron entry added."
}

skip_env=false
skip_cron=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-env)
      skip_env=true
      shift
      ;;
    --skip-cron)
      skip_cron=true
      shift
      ;;
    --help)
      usage 0
      ;;
    *)
      echo "ayumy setup-host: unknown option '$1'" >&2
      usage
      ;;
  esac
done

[[ "$skip_env" == "false" ]] && setup_env
setup_container
[[ "$skip_cron" == "false" ]] && setup_cron

echo "=== Host setup complete ==="
