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
  sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}
case "${1:-}" in
  -h|--help) usage 0 ;;
esac

cd "$(git rev-parse --show-toplevel)"
review_sha="$(git rev-parse HEAD)"
echo "review_sha=${review_sha}"

# 规则只从 main 提取；main 上没有规则文件即失败，绝不静默用工作树版。
rule_file=".claude/agents/deep-reviewer.md"
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
审查输出按规则的输出格式给出：人类可读结论 + \`\`\`findings\`\`\` JSON 数组。
除人类可读结论与 findings 块外，把完整 findings 写进文件 \`reviews/review-${review_sha}.json\`，
最小格式 \`{"review_sha": "<sha>", "findings": [...]}\`（沿用规则的 findings 字段：
severity/file/line/evidence/suggestion/reproduction）。
EOF

echo "prompt: ${prompt_file}"
if ! command -v claude >/dev/null 2>&1; then
  echo "ERROR: claude CLI 不可用" >&2
  exit 1
fi

# 调一次 claude -p 派发审查（findings 落盘由被调会话完成）。
claude -p --dangerously-skip-permissions < "$prompt_file"

# 校验 findings 文件存在且合法。
out_file="reviews/review-${review_sha}.json"
if [ ! -s "$out_file" ]; then
  echo "ERROR: ${out_file} 未生成或为空" >&2
  exit 1
fi
if ! python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "$out_file" >/dev/null 2>&1; then
  echo "ERROR: ${out_file} 不是合法 JSON" >&2
  exit 1
fi
echo "OK: ${out_file}"
