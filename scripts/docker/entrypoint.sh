#!/usr/bin/env bash
set -euo pipefail

RUN_MODE="${RUN_MODE:-once}"
RUN_INTERVAL_SECONDS="${RUN_INTERVAL_SECONDS:-21600}"

run_once() {
  python -m mealie_parser
}

if [ "$RUN_MODE" = "loop" ]; then
  if ! [[ "$RUN_INTERVAL_SECONDS" =~ ^[0-9]+$ ]]; then
    echo "[error] RUN_INTERVAL_SECONDS must be an integer"
    exit 1
  fi

  echo "[start] Loop mode enabled (interval=${RUN_INTERVAL_SECONDS}s)"
  while true; do
    run_once
    echo "[sleep] Waiting ${RUN_INTERVAL_SECONDS}s"
    sleep "$RUN_INTERVAL_SECONDS"
  done
fi

if [ "$RUN_MODE" != "once" ]; then
  echo "[error] RUN_MODE must be either 'once' or 'loop'"
  exit 1
fi

run_once
