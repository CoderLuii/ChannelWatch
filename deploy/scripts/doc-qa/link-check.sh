#!/usr/bin/env bash
set -euo pipefail

CONFIG_FILE="deploy/config/markdown-link-check.json"

if [ "$#" -eq 0 ]; then
    echo "[Doc QA] Error: Provide at least one Markdown file or glob to check."
    exit 1
fi

if [ ! -f "$CONFIG_FILE" ]; then
    echo "[Doc QA] Error: Missing markdown-link-check config at $CONFIG_FILE"
    exit 1
fi

npx --yes markdown-link-check@3 --config "$CONFIG_FILE" "$@"
