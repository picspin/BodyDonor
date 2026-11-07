#!/usr/bin/env bash
# Stop hook: session-end check for unhandled warnings, last-run test failures
# and temp files. Informational only: always exits 0, never blocks session end.
# NOTE: the full pytest suite is NOT re-run here (too slow for every session
# end); test failures are read from the last run's .pytest_cache lastfailed.
set -u

cd "${CLAUDE_PROJECT_DIR:-.}" 2>/dev/null || true
echo "--- stop-check ---"

if command -v ruff >/dev/null 2>&1; then
  RUFF=(ruff)
elif command -v uv >/dev/null 2>&1; then
  RUFF=(uv run ruff)
else
  RUFF=()
fi
if [ ${#RUFF[@]} -gt 0 ]; then
  if "${RUFF[@]}" check . > /tmp/stop-check-ruff.log 2>&1; then
    echo "ruff: clean"
  else
    echo "ruff: issues remain"
    head -20 /tmp/stop-check-ruff.log
  fi
else
  echo "ruff: not found, skipped"
fi

lastfailed=".pytest_cache/v/cache/lastfailed"
if [ -f "$lastfailed" ]; then
  n=$(python3 -c "import json; print(len(json.load(open('$lastfailed'))))" 2>/dev/null || echo "?")
  echo "pytest last run: $n failed test(s) recorded in $lastfailed"
  python3 -c "import json; [print('  FAILED:', k) for k in json.load(open('$lastfailed'))]" 2>/dev/null | head -20
else
  echo "pytest last run: no lastfailed cache (no recorded failures)"
fi

if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  tmp=$(git status --porcelain | awk '$1=="??" {print $2}' | grep -E '\.(tmp|bak|orig|rej|swp)$|~$' || true)
  if [ -n "$tmp" ]; then
    echo "temp files untracked:"
    printf '%s\n' "$tmp"
  else
    echo "temp files: none"
  fi
  echo "uncommitted changes: $(git status --porcelain | wc -l) file(s)"
fi
echo "--- stop-check done ---"
exit 0
