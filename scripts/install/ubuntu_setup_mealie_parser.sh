#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${REPO_DIR:-$HOME/mealie-parser}"
SETUP_CRON=false
CRON_SCHEDULE="0 */6 * * *"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo-dir)
      REPO_DIR="$2"
      shift 2
      ;;
    --setup-cron)
      SETUP_CRON=true
      shift
      ;;
    --cron-schedule)
      CRON_SCHEDULE="$2"
      shift 2
      ;;
    *)
      echo "Unknown option: $1"
      exit 1
      ;;
  esac
done

if [[ ! -d "$REPO_DIR" ]]; then
  echo "Repository directory not found: $REPO_DIR"
  exit 1
fi

sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-pip git curl ca-certificates

cd "$REPO_DIR"

python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate

pip install --upgrade pip
pip install -r requirements.txt
pip install -e .

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Created .env from .env.example. Edit it before running."
fi

mkdir -p reports

echo "Install complete."
echo "Run once with: $REPO_DIR/.venv/bin/python -m mealie_parser"

if [[ "$SETUP_CRON" == true ]]; then
  CRON_CMD="$CRON_SCHEDULE cd $REPO_DIR && $REPO_DIR/.venv/bin/python -m mealie_parser >> $REPO_DIR/reports/cron.log 2>&1"
  (crontab -l 2>/dev/null | grep -v "mealie_parser"; echo "$CRON_CMD # mealie_parser") | crontab -
  echo "Cron installed: $CRON_SCHEDULE"
fi
