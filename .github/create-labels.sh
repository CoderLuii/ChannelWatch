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

python3 - "$REPO" "$LABELS_FILE" <<'PYEOF'
import sys, subprocess, json

repo = sys.argv[1]
labels_file = sys.argv[2]

try:
    import yaml
    with open(labels_file) as f:
        labels = yaml.safe_load(f)
except ImportError:
    import re, ast
    print("Warning: PyYAML not available; using basic parser")
    with open(labels_file) as f:
        content = f.read()
    labels = []
    for block in re.split(r'\n- name:', content):
        if not block.strip():
            continue
        if not block.startswith('"') and not block.startswith("'"):
            block = "- name:" + block
        try:
            parsed = yaml.safe_load(block)
            if isinstance(parsed, list):
                labels.extend(parsed)
        except Exception:
            pass

created = 0
updated = 0
errors = 0

for label in labels:
    name = label['name']
    color = label.get('color', 'ededed')
    description = label.get('description', '')

    result = subprocess.run(
        ['gh', 'label', 'create', name,
         '--repo', repo,
         '--color', color,
         '--description', description,
         '--force'],
        capture_output=True, text=True
    )

    if result.returncode == 0:
        print(f"  OK  {name}")
        created += 1
    else:
        stderr = result.stderr.strip()
        if 'already exists' in stderr.lower():
            subprocess.run(
                ['gh', 'label', 'edit', name,
                 '--repo', repo,
                 '--color', color,
                 '--description', description],
                capture_output=True
            )
            print(f"  UP  {name}")
            updated += 1
        else:
            print(f"  ERR {name}: {stderr}", file=sys.stderr)
            errors += 1

print(f"\nDone: {created} created, {updated} updated, {errors} errors")
if errors:
    sys.exit(1)
PYEOF
