#!/usr/bin/env bash
set -euo pipefail

# Language tag handling: only fenced code blocks tagged exactly `bash` or `python`
# are extracted and executed. Untagged blocks, aliases such as `sh`/`py`, and
# fences with extra attributes are skipped so documentation examples opt in
# explicitly to executable QA.

if [ "$#" -eq 0 ]; then
    echo "[Doc QA] Error: Provide at least one Markdown file to scan for snippets."
    exit 1
fi

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

for md_file in "$@"; do
    if [ ! -f "$md_file" ]; then
        echo "[Doc QA] Error: Markdown file not found: $md_file"
        exit 1
    fi
done

python3 - "$TMP_DIR" "$@" <<'PYEOF'
import pathlib
import re
import subprocess
import sys

tmp_dir = pathlib.Path(sys.argv[1])
files = [pathlib.Path(item) for item in sys.argv[2:]]
fence_re = re.compile(r"^```(bash|python)\s*$")

snippet_count = 0
for md_file in files:
    language = None
    lines = []
    start_line = 0

    for line_number, line in enumerate(md_file.read_text(encoding="utf-8").splitlines(), 1):
        if language is None:
            match = fence_re.match(line)
            if match:
                language = match.group(1)
                lines = []
                start_line = line_number
            continue

        if line == "```":
            snippet_count += 1
            suffix = "sh" if language == "bash" else "py"
            snippet_path = tmp_dir / f"snippet-{snippet_count}.{suffix}"
            snippet_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            command = ["bash", str(snippet_path)] if language == "bash" else ["python3", str(snippet_path)]
            print(f"[Doc QA] Running {language} snippet from {md_file}:{start_line}")
            result = subprocess.run(command, cwd=tmp_dir, text=True)
            if result.returncode != 0:
                print(
                    f"[Doc QA] Error: {language} snippet from {md_file}:{start_line} "
                    f"exited with {result.returncode}",
                    file=sys.stderr,
                )
                sys.exit(result.returncode)
            language = None
            lines = []
            continue

        lines.append(line)

    if language is not None:
        print(f"[Doc QA] Error: Unterminated {language} fence in {md_file}:{start_line}", file=sys.stderr)
        sys.exit(1)

print(f"[Doc QA] Checked {snippet_count} executable snippets.")
PYEOF
