#!/usr/bin/env bash
set -euo pipefail

DOC_ROOT="docs"

if [ ! -d "$DOC_ROOT" ]; then
    echo "[Doc QA] Error: Missing documentation directory at $DOC_ROOT"
    exit 1
fi

python3 - "$DOC_ROOT" <<'PYEOF'
import pathlib
import re
import sys

doc_root = pathlib.Path(sys.argv[1])
quadrants = {
    "tutorials": "tutorial",
    "how-to": "how-to",
    "reference": "reference",
    "explanation": "explanation",
}

errors = []

def markdown_files(directory):
    if not directory.exists():
        return []
    return sorted(path for path in directory.rglob("*.md") if path.is_file())

for dirname, quadrant in quadrants.items():
    for md_file in markdown_files(doc_root / dirname):
        text = md_file.read_text(encoding="utf-8")
        lines = text.splitlines()

        is_index = md_file.name == "README.md"
        has_numbered_steps = any(re.match(r"^\s*\d+\.\s+", line) for line in lines)
        has_prose_intro = any(
            len(re.findall(r"[.!?]", paragraph)) >= 1
            and not paragraph.lstrip().startswith(("#", "-", "*", "|"))
            for paragraph in re.split(r"\n\s*\n", text)
        )

        # README files in quadrant directories are navigation/index pages. They
        # should explain the quadrant, but they should not be forced to contain
        # tutorial steps, how-to procedures, reference tables, or long essays.
        if is_index:
            if not has_prose_intro:
                errors.append(f"{md_file}: index pages need a short prose description")
            continue

        if quadrant == "tutorial":
            if not has_numbered_steps:
                errors.append(f"{md_file}: tutorials must contain numbered-list steps")

        elif quadrant == "reference":
            table_separator = any(re.match(r"^\s*\|(?:\s*:?-{3,}:?\s*\|)+\s*$", line) for line in lines)
            reference_markers = len(re.findall(r"`[^`]+`", text)) >= 5 or "## Source paths" in text
            if not (table_separator or reference_markers):
                errors.append(f"{md_file}: reference docs must contain a Markdown table or structured reference identifiers")

        elif quadrant == "how-to":
            title = next((line.strip().lstrip("# ").strip() for line in lines if line.startswith("#")), "")
            has_how_title = bool(re.match(r"^(To |How to )", title))
            has_step_heading = any(re.match(r"^#+\s+Step\s+\d+", line.strip(), re.IGNORECASE) for line in lines)
            # How-to docs can be named as imperative tasks (Deploy, Configure,
            # Upgrade, Troubleshoot) when they include concrete numbered steps.
            if not (has_how_title or has_step_heading or "Steps:" in text or has_numbered_steps):
                errors.append(f"{md_file}: how-to docs must have a task title, step headings, or numbered steps")

        elif quadrant == "explanation":
            prose_paragraphs = [
                paragraph.strip()
                for paragraph in re.split(r"\n\s*\n", text)
                if len(re.findall(r"[.!?]", paragraph)) > 2
                and not paragraph.lstrip().startswith(("#", "-", "*", "|"))
            ]
            step_lines = sum(1 for line in lines if re.match(r"^\s*(\d+\.|[-*])\s+", line))
            nonblank_lines = sum(1 for line in lines if line.strip())
            step_dominated = nonblank_lines > 0 and step_lines / nonblank_lines > 0.4
            if not prose_paragraphs or step_dominated:
                errors.append(f"{md_file}: explanation docs need prose paragraphs over two sentences and must not be dominated by step lists")

if errors:
    for error in errors:
        print(f"[Doc QA] Error: {error}", file=sys.stderr)
    sys.exit(1)

print("[Doc QA] Diátaxis heuristic checks passed.")
PYEOF
