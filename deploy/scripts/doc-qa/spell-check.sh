#!/usr/bin/env bash
set -euo pipefail

CONFIG_FILE="deploy/config/cspell.json"

if [ ! -f "$CONFIG_FILE" ]; then
    echo "[Doc QA] Error: Missing cspell config at $CONFIG_FILE"
    exit 1
fi

npx --yes cspell@8 --config "$CONFIG_FILE" "**/*.md" "!app/ui/node_modules/**"
