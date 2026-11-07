#!/usr/bin/env bash
# SubagentStop hook (matcher: deep-reviewer): post the reviewer's structured
# findings to the GitHub PR as inline comments + a summary comment, and set
# the deep-reviewer commit status (failure on blocker, success otherwise).
#
# Deterministic engineering: no LLM calls here, only parsing + gh api.
# Reads the hook JSON from stdin. Exits 0 in all cases (never fails the hook);
# prints exactly what happened (posted / skipped + reason).
#
# Prerequisites on the machine running Claude Code:
#   - `gh` CLI installed and authenticated (gh auth login)
#   - an open PR for the current branch in the repo
# Without them it prints SKIP and exits 0; findings stay in the transcript.
set -u

input=$(cat)

# --- 1. transcript_path from hook input ------------------------------------
transcript=$(printf '%s' "$input" | python3 -c "import json,sys; print(json.load(sys.stdin).get('transcript_path',''))" 2>/dev/null)
if [ -z "$transcript" ] || [ ! -f "$transcript" ]; then
  echo "post-review-to-pr: SKIP — no transcript_path in hook input"
  exit 0
fi

# --- 2. last ```findings fenced JSON block from the transcript --------------
findings_json=$(python3 - "$transcript" <<'PYEOF'
import json, re, sys
path = sys.argv[1]
text = ""
try:
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                obj = json.loads(line)
            except Exception:
                continue
            msg = obj.get("message") or {}
            if msg.get("role") != "assistant":
                continue
            for block in msg.get("content") or []:
                if isinstance(block, dict) and block.get("type") == "text":
                    text += block.get("text", "") + "\n"
except FileNotFoundError:
    sys.exit(1)
blocks = re.findall(r"```findings\s*\n(.*?)```", text, re.DOTALL)
if not blocks:
    sys.exit(2)
print(blocks[-1].strip())
PYEOF
)
rc=$?
if [ $rc -eq 1 ]; then
  echo "post-review-to-pr: SKIP — transcript not readable: $transcript"
  exit 0
elif [ $rc -eq 2 ] || [ -z "$findings_json" ]; then
  echo "post-review-to-pr: SKIP — no \`\`\`findings block in reviewer output"
  exit 0
fi

# --- 3. validate + dedupe (fingerprint = sha256 of severity/file/line/evidence)
deduped=$(printf '%s' "$findings_json" | python3 -c "
import json, sys, hashlib
try:
    items = json.load(sys.stdin)
except Exception as e:
    print('BAD_JSON:' + str(e), file=sys.stderr); sys.exit(3)
if not isinstance(items, list):
    print('BAD_JSON: findings block is not an array', file=sys.stderr); sys.exit(3)
seen = set(); out = []
for it in items:
    if not isinstance(it, dict):
        continue
    sev = str(it.get('severity', '')).lower()
    if sev not in ('blocker', 'warning', 'nit'):
        sev = 'nit'
    f = str(it.get('file', ''))
    try:
        line = int(it.get('line') or 0)
    except Exception:
        line = 0
    ev = str(it.get('evidence', ''))
    sug = str(it.get('suggestion', ''))
    fp = hashlib.sha256((sev + '\0' + f + '\0' + str(line) + '\0' + ev).encode()).hexdigest()[:16]
    if fp in seen:
        continue
    seen.add(fp)
    out.append({'severity': sev, 'file': f, 'line': line, 'evidence': ev, 'suggestion': sug, 'fingerprint': fp})
print(json.dumps(out))
" 2>/tmp/post-review-parse.err)
rc=$?
if [ $rc -ne 0 ]; then
  echo "post-review-to-pr: SKIP — findings block is not valid JSON: $(cat /tmp/post-review-parse.err)"
  exit 0
fi
count=$(printf '%s' "$deduped" | python3 -c "import json,sys; print(len(json.load(sys.stdin)))")
if [ "$count" -eq 0 ]; then
  echo "post-review-to-pr: no findings (empty array) — nothing to post"
  exit 0
fi

# --- 4. prerequisites: gh + repo + open PR for current branch --------------
if ! command -v gh >/dev/null 2>&1; then
  echo "post-review-to-pr: SKIP — gh CLI not found. Install + auth gh (gh auth login) on this machine to enable PR posting. Findings ($count) are in the reviewer transcript: $transcript"
  exit 0
fi
remote_url=$(git remote get-url origin 2>/dev/null || true)
repo=$(printf '%s' "$remote_url" | sed -E 's#.*github\.com[/:]([^/]+/[^/]+?)(\.git)?$#\1#')
if [ -z "$repo" ]; then
  echo "post-review-to-pr: SKIP — cannot derive owner/repo from origin ($remote_url)"
  exit 0
fi
branch=$(git branch --show-current 2>/dev/null || true)
pr_json=$(gh pr list --repo "$repo" --head "$branch" --json number,headRefOid --jq '.[0] // empty' 2>/dev/null || true)
pr_number=$(printf '%s' "$pr_json" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('number',''))" 2>/dev/null)
head_sha=$(printf '%s' "$pr_json" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('headRefOid',''))" 2>/dev/null)
if [ -z "$pr_number" ]; then
  echo "post-review-to-pr: SKIP — no open PR for branch $branch in $repo. Findings ($count) kept in transcript: $transcript"
  exit 0
fi

# --- 5. post: inline comments (line > 0) + summary + commit status ---------
printf '%s' "$deduped" | REPO="$repo" PR="$pr_number" SHA="$head_sha" python3 - <<'PYEOF'
import json, os, subprocess, sys

repo = os.environ["REPO"]
pr = os.environ["PR"]
sha = os.environ["SHA"]
findings = json.load(sys.stdin)

def gh_api(route, payload):
    cmd = ["gh", "api", "--method", "POST", route, "--input", "-"]
    r = subprocess.run(cmd, input=json.dumps(payload).encode(),
                       capture_output=True)
    return r.returncode, r.stdout.decode(errors="replace"), r.stderr.decode(errors="replace")

counts = {"blocker": 0, "warning": 0, "nit": 0}
for f in findings:
    counts[f["severity"]] += 1

posted = 0
summary_only = []
for f in findings:
    if f["line"] > 0 and f["file"]:
        body = ("**[{sev}]** {ev}\n\nSuggestion: {sug}\n\n"
                "<!-- deep-reviewer fingerprint: {fp} -->").format(
                    sev=f["severity"], ev=f["evidence"],
                    sug=f["suggestion"], fp=f["fingerprint"])
        rc, _out, err = gh_api(
            f"repos/{repo}/pulls/{pr}/comments",
            {"body": body, "commit_id": sha, "path": f["file"], "line": f["line"]})
        if rc == 0:
            posted += 1
        else:
            # e.g. 422: line not in the PR diff — fall back to the summary.
            summary_only.append((f, err.strip()[:200]))
    else:
        summary_only.append((f, "no file/line"))

lines = ["## Deep review (deep-reviewer)", "",
         f"Findings: {len(findings)} "
         f"(blocker: {counts['blocker']}, warning: {counts['warning']}, nit: {counts['nit']})",
         f"Inline comments posted: {posted}", ""]
for f, why in summary_only:
    loc = f"{f['file']}:{f['line']}" if f["file"] else "(no location)"
    lines.append(f"- **[{f['severity']}]** {loc} — {f['evidence']}")
    if f["suggestion"]:
        lines.append(f"  Suggestion: {f['suggestion']}")
    lines.append(f"  _not inlined: {why}_")
rc, _out, err = gh_api(f"repos/{repo}/issues/{pr}/comments",
                       {"body": "\n".join(lines)})
if rc != 0:
    print(f"post-review-to-pr: summary comment FAILED: {err.strip()[:200]}", file=sys.stderr)

state = "failure" if counts["blocker"] > 0 else "success"
desc = f"{counts['blocker']} blocker(s), {len(findings)} finding(s) total"
rc, _out, err = gh_api(f"repos/{repo}/statuses/{sha}",
                       {"state": state, "context": "deep-reviewer",
                        "description": desc})
if rc != 0:
    print(f"post-review-to-pr: commit status FAILED: {err.strip()[:200]}", file=sys.stderr)

print(f"post-review-to-pr: posted {posted} inline comment(s) to "
      f"https://github.com/{repo}/pull/{pr} "
      f"({counts['blocker']} blocker, {counts['warning']} warning, {counts['nit']} nit); "
      f"commit status: {state}")
PYEOF
exit 0
