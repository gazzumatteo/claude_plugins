#!/usr/bin/env bash
# PreToolUse hook: block Edit/Write on the source checklist file during a run.
#
# Claude Code invokes this hook with the tool call payload on stdin as JSON.
# We read the target file path from tool_input and compare against the marker
# written by init_report.py. Exit 2 blocks the tool call and surfaces the
# stderr message to the model.

set -euo pipefail

PAYLOAD="$(cat)"

# Find all .current-source markers in the current working tree.
# The marker is at <source_dir>/.e2e-runs/.current-source and contains the
# absolute path of the source checklist file.
find_markers() {
    local dir="${CLAUDE_PROJECT_DIR:-$PWD}"
    find "$dir" -type f -name ".current-source" -path "*/.e2e-runs/*" 2>/dev/null || true
}

# Extract the target file path from the tool_input JSON.
# Edit/Write use "file_path", NotebookEdit uses "notebook_path".
extract_target() {
    python3 -c '
import json, sys
payload = json.loads(sys.argv[1])
ti = payload.get("tool_input", {})
for key in ("file_path", "notebook_path", "path"):
    if key in ti:
        print(ti[key])
        break
' "$PAYLOAD" 2>/dev/null || true
}

TARGET="$(extract_target)"
if [[ -z "$TARGET" ]]; then
    exit 0
fi

# Resolve to absolute path (handle ~ and relative).
TARGET_ABS="$(python3 -c "import os,sys; print(os.path.realpath(os.path.expanduser(sys.argv[1])))" "$TARGET" 2>/dev/null || echo "$TARGET")"

BLOCKED=0
while IFS= read -r marker; do
    [[ -z "$marker" ]] && continue
    [[ ! -r "$marker" ]] && continue
    SOURCE="$(cat "$marker" 2>/dev/null | head -1)"
    [[ -z "$SOURCE" ]] && continue
    SOURCE_ABS="$(python3 -c "import os,sys; print(os.path.realpath(os.path.expanduser(sys.argv[1])))" "$SOURCE" 2>/dev/null || echo "$SOURCE")"
    if [[ "$TARGET_ABS" == "$SOURCE_ABS" ]]; then
        BLOCKED=1
        echo "BLOCKED: cannot modify source checklist '$SOURCE' during an active e2e test run." >&2
        echo "The source file is the test specification — it must not be edited by the test executor." >&2
        echo "If you intended to update the checklist itself, exit the run first." >&2
        break
    fi
done < <(find_markers)

if [[ "$BLOCKED" == "1" ]]; then
    exit 2
fi
exit 0
