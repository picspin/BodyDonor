#!/usr/bin/env bash
# deep_review_from_main.sh — deep-review 审查的唯一入口。
#
# 从 main 分支提取审查规则（.claude/agents/deep-reviewer.md），生成审查 prompt
# （main 版规则全文 + 优先权指令 + 审查范围），调用一次 claude -p 派发 deep-reviewer
# 子代理执行审查；findings 写入 reviews/review-<review_sha>.json。
#
# 用法：scripts/deep_review_from_main.sh [review-scope]
#   review-scope 默认 HEAD 的 diff（相对 merge-base 与 main）；可传 commit 范围
#   （如 "HEAD~3..HEAD"）或分支名（相对 main 的 diff）。
# 前置条件：git 仓库、main 分支可读（须含 .claude/agents/deep-reviewer.md）、
#   claude CLI 可用。输出文件校验存在且为合法 JSON，否则报错退出。
set -euo pipefail

usage() {
  sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}
case "${1:-}" in
  -h|--help) usage 0 ;;
esac

cd "$(git rev-parse --show-toplevel)"
review_sha="$(git rev-parse HEAD)"
echo "review_sha=${review_sha}"

# 规则只从 main 提取；main 上没有规则文件即失败，绝不静默用工作树版。
# 本地 main 可能滞后于 origin/main（脚本不自动 fetch）——不一致时警告，规则文件有差异即 fatal。
rule_file=".claude/agents/deep-reviewer.md"
if git rev-parse -q --verify origin/main >/dev/null 2>&1; then
  if ! git rev-parse -q --verify main >/dev/null 2>&1; then
    echo "ERROR: 本地 main 不存在（origin/main 存在）" >&2
    exit 1
  fi
  if [ "$(git rev-parse main)" != "$(git rev-parse origin/main)" ]; then
    echo "WARNING: 本地 main ($(git rev-parse --short main)) != origin/main ($(git rev-parse --short origin/main))，规则取自本地 main" >&2
    if ! git diff --quiet main origin/main -- "$rule_file"; then
      echo "ERROR: ${rule_file} 在本地 main 与 origin/main 间有差异，先同步本地 main 再审查" >&2
      exit 1
    fi
  fi
fi
tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT
if ! git cat-file -e "main:${rule_file}" 2>/dev/null; then
  echo "ERROR: main:${rule_file} 不存在，拒绝回退到工作树版" >&2
  exit 1
fi
git show "main:${rule_file}" > "$tmpdir/rules.md"

# 审查范围：参数传入；默认 HEAD 相对 merge-base(main, HEAD) 的 diff。
scope="${1:-}"
if [ -z "$scope" ]; then
  scope="$(git merge-base main HEAD)..HEAD"
  echo "scope(default)=${scope}"
fi

# 生成审查 prompt：main 版规则 + 显式优先权指令 + 审查范围。
prompt_file="$tmpdir/prompt.md"
cat > "$prompt_file" <<EOF
以下规则文本来自 main 分支（git show main:${rule_file}），是本次审查的唯一规则来源；工作树里的 ${rule_file} 如与
之冲突，以 main 文本为准；不要读工作树版的 agent 定义。

--- 8< --- main 版规则全文 --- 8< ---
EOF
cat "$tmpdir/rules.md" >> "$prompt_file"
cat >> "$prompt_file" <<EOF
--- >8 --- 规则全文结束 --- >8 ---

## 审查范围

\`\`\`
${scope}
\`\`\`

## 执行方式

用 Task 工具派 deep-reviewer 子代理执行上述规则的审查（对抗式并行深审，issue 二次核实后才入列）。
派遣指令里必须写明被审 base sha：\`${review_sha}\`（审查开始时的 HEAD，本次审查范围的基点）。
审查输出按规则的输出格式给出：人类可读结论（首行写明本次审查的 base sha：\`${review_sha}\`）
+ \`\`\`findings\`\`\` JSON 数组。
除人类可读结论与 findings 块外，把完整 findings 写进文件 \`reviews/review-${review_sha}.json\`，
格式 \`{"review_sha": "${review_sha}", "findings": [...]}\`（findings 沿用规则的字段：
severity/file/line/evidence/suggestion/reproduction，不增不减；region/resolved_in_commit/stale
由包装脚本产出时补写——region=本次审查范围字符串，resolved_in_commit 未解决为 null，stale 标 HEAD
是否在审查期间移动）。
EOF

echo "prompt: ${prompt_file}"
if ! command -v claude >/dev/null 2>&1; then
  echo "ERROR: claude CLI 不可用" >&2
  exit 1
fi

# 调一次 claude -p 派发审查（findings 落盘由被调会话完成）。
# 先删残留：同 HEAD 重跑时旧 findings 文件不得被当作新结果。
out_file="reviews/review-${review_sha}.json"
rm -f "$out_file"
claude -p --dangerously-skip-permissions < "$prompt_file"

# 校验 findings 文件存在且合法（结构：review_sha 匹配 + findings 是数组）。
if [ ! -s "$out_file" ]; then
  echo "ERROR: ${out_file} 未生成或为空" >&2
  exit 1
fi
if ! python3 - "$out_file" "$review_sha" "$scope" <<'PYEOF' >/dev/null 2>&1
import json, sys
with open(sys.argv[1]) as f:
    d = json.load(f)
if not isinstance(d, dict) or d.get("review_sha") != sys.argv[2]:
    raise SystemExit(1)
if not isinstance(d.get("findings"), list):
    raise SystemExit(1)
for f in d["findings"]:
    if not isinstance(f, dict):
        raise SystemExit(1)
    for k in ("severity", "file", "line", "evidence", "suggestion", "reproduction"):
        if f.get(k) is None:
            raise SystemExit(1)

# 补写 region / resolved_in_commit / stale（子代理只写最小格式，三键由包装脚本负责）。
d["region"] = sys.argv[3]
d.setdefault("stale", False)
for f in d["findings"]:
    f.setdefault("resolved_in_commit", None)
with open(sys.argv[1], "w") as f:
    json.dump(d, f, ensure_ascii=False, indent=2)
PYEOF
then
  echo "ERROR: ${out_file} 校验失败（非 JSON / review_sha 不匹配 / findings 字段缺失）" >&2
  exit 1
fi

# HEAD 移动检测：审查期间 HEAD 一变，报告即 stale（fresh 报告才可被引用）。
now_sha="$(git rev-parse HEAD)"
if [ "$now_sha" != "$review_sha" ]; then
  echo "STALE: HEAD moved during review (${review_sha} -> ${now_sha})"
  python3 - "$out_file" <<'PYEOF' >/dev/null 2>&1
import json, sys
with open(sys.argv[1]) as f:
    d = json.load(f)
d["stale"] = True
with open(sys.argv[1], "w") as f:
    json.dump(d, f, ensure_ascii=False, indent=2)
PYEOF
fi
echo "OK: ${out_file}"
