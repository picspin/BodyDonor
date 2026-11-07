#!/usr/bin/env bash
# PostToolUse hook: after an Edit/Write, run ruff check on the changed file
# (the "随手检查" step). Advisory only: always exits 0, never blocks the edit.
# NOTE: ruff format is intentionally NOT checked here — CI only enforces
# `ruff check`, and 49 files in the repo are currently not ruff-format clean
# (pre-existing). Adding format --check would blame every edit for old debt.
# Reads the hook JSON from stdin.
set -u

input=$(cat)
file_path=$(printf '%s' "$input" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('tool_input',{}).get('file_path',''))" 2>/dev/null)
[ -z "$file_path" ] && exit 0
case "$file_path" in *.py) ;; *) exit 0 ;; esac
[ -f "$file_path" ] || exit 0

if command -v ruff >/dev/null 2>&1; then
  RUFF=(ruff)
elif command -v uv >/dev/null 2>&1; then
  RUFF=(uv run ruff)
else
  echo "format-lint: ruff not found, skipped ($file_path)"
  exit 0
fi

if out=$("${RUFF[@]}" check "$file_path" 2>&1); then
  echo "format-lint: clean ($file_path)"
else
  echo "format-lint: issues in $file_path"
  printf '%s\n' "$out"
fi
exit 0
