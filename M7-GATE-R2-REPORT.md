# M7-GATE-R2 轮次报告 — ② deep-reviewer 规则从 main 读

日期：2026-11-19。基线 origin/main `421ef0c`，分支 `feat/m7-gate-hardening`。

## 提交列表（4 提交，实测 diffstat）

| 提交 | 内容 |
|---|---|
| `58471bd` | feat(m7-gate): ② deep-review 唯一入口脚本 + 3 例测试（217 行） |
| `1e2be2a` | docs(m7-gate): deep-reviewer 加"调用约定"小节（+4 行） |
| `a2d102d` | fix(m7-gate): 本地 main 滞后 origin/main 警告 + 规则文件差异 fatal（+9 行） |
| `cac79bb` | test(m7-gate): 审查修复 5W+3N 全修（+140/-15） |

累计 diffstat（`git diff 421ef0c..HEAD --stat` 尾行）：`3 files changed, 355 insertions(+), 15 deletions(-)`（仅 scripts/deep_review_from_main.sh、tests/test_deep_review_from_main.py、.claude/agents/deep-reviewer.md 三文件；分支上另有 S3P2 轮的历史提交不属本轮）。

## ② 逐项证据

### 1. 审查包装脚本
- `scripts/deep_review_from_main.sh:16-19` — `usage()` 从头部注释生成 `--help`；`-h/--help` 分支 20-24 行。
- `scripts/deep_review_from_main.sh:26-27` — `review_sha=$(git rev-parse HEAD)` 并打印。
- `scripts/deep_review_from_main.sh:29-52` — origin/main 分歧护栏（a2d102d）：origin/main 存在时先验本地 main 存在（缺失即 ERROR 退出），不一致打 WARNING，规则文件 `git diff --quiet main origin/main -- <rule_file>` 有差异即 ERROR 退出。
- `scripts/deep_review_from_main.sh:54-62` — `git cat-file -e main:<rule_file>` 检查规则文件在 main 上存在，缺失即报错退出（fail-closed，不回退工作树版）；`git show main:...` 提取进临时文件（mktemp + trap 清理）。
- `scripts/deep_review_from_main.sh:64-71` — 审查范围：参数传入；默认 `git merge-base main HEAD)..HEAD`。
- `scripts/deep_review_from_main.sh:73-105` — prompt 生成：优先权指令一句 + main 版规则全文（`cat rules.md`）+ 审查范围 + 执行方式（派 deep-reviewer 子代理、findings 落盘 `reviews/review-<sha>.json` 最小格式）。
- `scripts/deep_review_from_main.sh:107-117` — `command -v claude` 检查后 `claude -p --dangerously-skip-permissions < prompt_file`；调用前 `rm -f "$out_file"`（W1 修复，防同 HEAD 残留复用）。
- `scripts/deep_review_from_main.sh:119-136` — 校验：文件存在且非空；python 结构校验（dict + `review_sha` 匹配 + `findings` 是数组；W2 修复，超 `json.load` 语法层）。

### 2. 调用约定
- `.claude/agents/deep-reviewer.md:66-68` — "调用约定"小节（只加不改现有清单/输出格式）：审查一律经脚本发起；注入规则以 main 版为准（W5 修复后如实表述：子代理 name/tools/hook 仍由工作树版解析）；不要绕过脚本直接派子代理。
- 用法文档在脚本头部注释 + `--help`（二选一取脚本内，无重复文档）。

### 3. 测试（失败测试先行）
- `tests/test_deep_review_from_main.py`：scratch git 仓（`git init -b main` + 提交）+ `fake_claude` PATH 替身（`FAKE_MODE` 控制落盘行为：write/nowrite/badjson/badstruct）。
- 8 例全绿（实测 `[100%] 8 passed`）：
  1. `test_extracts_main_version_not_worktree` — main 版全文注入 prompt、WORK-MARKER 不出现；
  2. `test_missing_main_rules_fails` — main 无规则文件报错退出、不调 claude（不静默回退）；
  3. `test_review_sha_and_json_output` — review_sha 与 scratch 仓 HEAD 一致、findings JSON 落盘且结构正确；
  4. `test_no_stale_findings_reuse` — 同 HEAD 重跑时旧 findings 先删、claude 未写文件即报"未生成或为空"；
  5. `test_bad_json_fails` — 非 JSON 内容报"校验失败"退出非 0；
  6. `test_bad_structure_fails` — review_sha 不匹配/findings 非数组报错退出非 0；
  7. `test_origin_rule_divergence_fails` — origin/main 规则文件与本地 main 有差异即拒绝（`_add_origin_remote` 逐级 mktree 造 origin 指针）；
  8. `test_origin_diverged_same_rules_warns_only` — 提交不同但规则相同时仅 WARNING、正常产出。
- ruff：`tests/test_deep_review_from_main.py` All checks passed。
- 端到端真审查（真 `claude -p`）不进 pytest，见下节验证记录。

## 第 2 项验证记录（改松规则 → 真审查 → 恢复）

1. **改松**：工作树 `.claude/agents/deep-reviewer.md` 删除审查清单第 4 项"测试：新增/修改的行为是否有测试覆盖；测试是否真的断言了所声称的行为"整条，**未提交**（`git diff` 实测 1 file, 1 deletion）。因改松文件挡切分支，先拷贝存档 `/tmp/relaxed-reviewer.md` 再还原。
2. **审查目标**：临时分支 `tmp/verify-target`（自 origin/main）提交 `7da78fe`，新增 `src/bodydonor/_gate_r2_gap.py`（2 行 `gap_add` 函数，故意无测试）。
3. **真审查**：`bash scripts/deep_review_from_main.sh "7da78fe~1..7da78fe"`，exit 0。关键输出行（`/tmp/r2-verify-evidence/r2-review-run.log`，已存档）：
   - `review_sha=1e2be2a835d0da3ccf87c2c54103870ed4e9d56c`（脚本抓取正确）
   - 第 17 行：`- **测试覆盖** ✓（不构成 finding）— diff 无测试、全树无调用点（git grep gap_add 7da78fe 仅命中定义行自身）。二次核实：pyproject addopts = "-q" 无覆盖率门槛、CI workflow 裸 pytest 无 --cov/fail_under，缺测试不会挂 CI；提交信息自述「验证目标」；git merge-base --is-ancestor 实测 NOT ancestor of main`。
   - **main 版证明**：改松版清单已无第 4 项，而审查输出中"测试覆盖"条目存在且执行了实质核查（diff 无测试 + CI 无覆盖率门槛 + 非 main 路径），证明审查规则用的是 main 版而非被改松的工作树版。
   - 过程记录：deep-reviewer 子代理无 Bash，被审提交不在当前工作树时无法自取 diff；首次派发失败后由调用方实测 `git show`/`git grep` 贴入 prompt 复审通过（与 M6 轮 deep-reviewer 无 Bash 的已知边界一致）。
4. **恢复**：`git checkout -- .claude/agents/deep-reviewer.md` 还原；临时分支 `tmp/verify-target`、`tmp/verify-relaxed-review` 已删除；`git status` 干净（实测 `grep -c "4\. 测试"` = 1，清单第 4 项在工作树与 HEAD 均在位）；审查 JSON 未留在分支（`reviews/` 清空，日志与 JSON 存档 `/tmp/r2-verify-evidence/`）。

## 本轮审查（用落地后的路径）

`bash scripts/deep_review_from_main.sh "421ef0c..HEAD"`（exit 0）派 1 个 deep-reviewer，findings 落盘 `reviews/review-a2d102d*.json`（校验通过，已删未留仓）。**8 findings（5W+3N），逐条 double-check 全部属实（读码可证 + 审查者提供复现记录），全部已修**：

| # | 严重度 | 内容 | 处理 |
|---|---|---|---|
| W1 | warning | 同 HEAD 残留 findings 文件可被复用为"新"结果 | 已修：claude 调用前 `rm -f`，新增 test_no_stale_findings_reuse |
| W2 | warning | JSON 校验只做语法不核结构 | 已修：python 校验 review_sha 匹配 + findings 数组，新增 test_bad_structure_fails |
| W3 | warning | origin/main 护栏零测试覆盖 | 已修：test_origin_rule_divergence_fails + test_origin_diverged_same_rules_warns_only |
| W4 | warning | 校验路径（未生成/非 JSON）无测试 | 已修：test_bad_json_fails + nowrite 用例 |
| W5 | warning | "main 版规则优先于工作树版"表述夸大（子代理定义仍工作树版） | 已修：调用约定改如实表述 |
| N1 | nit | 本地 main 缺失时错误链路误导 | 已修：先验 main 存在 |
| N2 | nit | `--help` 多打印一行 `set -euo pipefail` | 已修：sed '2,14p'→'2,12p' |
| N3 | nit | GIT_DIR 等环境变量可重定向 scratch 仓 git 操作 | 已修：_run_script 清 4 变量 |

修复复验：8/8 passed（实测 `[100%]`），ruff 全绿。

## 过程事故记录

- 审查者复现记录中 W1/W2 的机械复现尝试：PATH 替身未生效（claude 真机被调起）导致 120s 超时转后台，已 TaskStop 停止并清理；复现依据改为审查者提供的复现记录 + 读码核实（脚本确无 `rm -f`、仅 `json.load`）。

## 未决清单

1. **本地 main 滞后**（环境事实，非本轮引入）：本仓本地 `main`（`d7b5c44`）落后 `origin/main`（`421ef0c`），规则文件当前两处一致（`git diff main origin/main -- .claude/agents/deep-reviewer.md` 实测 0 行）故本轮验证不受影响；脚本已加 WARNING + 规则文件差异即 fatal。建议协调方在 main 所在 checkout 跑一次 `git fetch`/同步（本 worktree 不 push 不 merge，未动）。
2. **端到端 claude 调用链的 findings 落盘**：脚本调 `claude -p` 时 findings 由被调会话写盘——本轮两次真跑均成功落盘（验证记录 + 自审各一次），但该依赖属被调会侧行为，脚本只做事后校验，属设计内（fail-closed 已覆盖）。
3. **reviews/ 目录未进仓**：审查 JSON 与日志按惯例（与 M7-S2 收口一致）不 commit，存档 `/tmp/r2-verify-evidence/`；是否纳入版本管理留协调方决定。
