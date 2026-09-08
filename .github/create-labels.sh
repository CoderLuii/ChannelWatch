#!/usr/bin/env bash
set -euo pipefail

REPO="${1:-}"
if [[ -z "$REPO" ]]; then
  echo "Usage: $0 <owner/repo>"
  echo "Example: $0 CoderLuii/ChannelWatch"
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LABELS_FILE="$SCRIPT_DIR/labels.yml"

if ! command -v gh &>/dev/null; then
  echo "Error: gh CLI is not installed. Install from https://cli.github.com/"
  exit 1
fi

if ! command -v python3 &>/dev/null; then
  echo "Error: python3 is required to parse labels.yml"
  exit 1
fi

# Requires PyYAML: python3 -m pip install PyYAML
python3 - "$REPO" "$LABELS_FILE" <<'PYEOF'
import re
import subprocess
import sys

try:
    import yaml
except ImportError:
    print("Error: PyYAML is required. Install with: python3 -m pip install PyYAML", file=sys.stderr)
    sys.exit(1)

repo, labels_file = sys.argv[1:]
try:
    with open(labels_file, encoding="utf-8") as source:
        labels = yaml.safe_load(source)
    if not isinstance(labels, list) or not labels:
        raise ValueError("labels.yml must contain a non-empty list")
    names = set()
    for label in labels:
        if not isinstance(label, dict) or not isinstance(label.get("name"), str) or not label["name"].strip():
            raise ValueError("every label needs a non-empty name")
        if label["name"] in names:
            raise ValueError("label names must be unique")
        names.add(label["name"])
        if not re.fullmatch(r"[0-9a-fA-F]{6}", str(label.get("color", "ededed"))):
            raise ValueError("label colors must be six hexadecimal digits")
        if not isinstance(label.get("description", ""), str):
            raise ValueError("label descriptions must be text")
except (OSError, ValueError, yaml.YAMLError) as exc:
    print(f"Error: {exc}", file=sys.stderr)
    sys.exit(1)

applied = errors = 0
for label in labels:
    result = subprocess.run(
        ["gh", "label", "create", label["name"], "--repo", repo,
         "--color", str(label.get("color", "ededed")),
         "--description", label.get("description", ""), "--force"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        print(f"  OK  {label['name']}")
        applied += 1
    else:
        print(f"  ERR {label['name']}: {result.stderr.strip()}", file=sys.stderr)
        errors += 1

print(f"\nDone: {applied} applied, {errors} errors")
sys.exit(1 if errors else 0)
PYEOF
