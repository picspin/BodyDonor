# M7-GATE R1 轮次报告——① pre-commit hook 硬隔离 config/

日期：2026-11-19。基线：R2 HEAD `18f0724`（② 已落地）。分支 `feat/m7-gate-hardening`。

## 提交列表（实测 `git log --oneline 18f0724..HEAD`）

| SHA | 类型 | 内容 |
|-----|------|------|
| `ff61f68` | feat(m7-gate) | ① commit-msg hook 硬隔离 config/（脚本 + README 安装文档） |
| `7667f0d` | test(m7-gate) | ① scratch 仓实测守卫七例 |
| `8e62d18` | fix(m7-gate) | ① 审查修复——暂存列表 `--no-renames/-z` 临时文件读法 + 4 新测试 |
| `5f53a6b` | docs(m7-gate) | ① 审查修复——README 补实现细节与两项已接受限制 |

## Diffstat（实测 `git diff --stat 18f0724..HEAD`）

```
 scripts/git-hooks/README.md          |  30 ++++++++
 scripts/git-hooks/commit-msg         |  32 +++++++++
 tests/test_precommit_config_guard.py | 135 +++++++++++++++++++++++++++++++++++
 3 files changed, 197 insertions(+)
```

未 tracked（按惯例不进 git）：本报告 `M7-GATE-R1-REPORT.md`、审查 findings `reviews/review-7667f0d….json`。

## ① 逐项证据

### Hook 脚本进仓库（任务 §1）

- 脚本：`scripts/git-hooks/commit-msg`（26 行，可执行 mode 100755，进 git）。
- 安装文档：`scripts/git-hooks/README.md`——安装命令 `git config core.hooksPath scripts/git-hooks`（版本化、每个 clone 设一次，二选一方案采用 core.hooksPath，无第二套并存）；验证方法（无标记改 config 被拒即生效）写明。
- 守卫逻辑（commit-msg:6–28，亲读）：
  1. merge 提交（`MERGE_HEAD` 存在）→ 直接放行（exit 0）；
  2. `git diff --cached --name-only` 读暂存列表——**不加 `--diff-filter`**，删除 config 文件同样要过标记（决定：删 config 也是 config 变更）；
  3. 暂存含 `^config/` 路径 → 从 `$1`（消息文件）读非 `#` 注释行，`grep -qF '[config-change]'` 精确子串、大小写敏感匹配；
  4. 无标记 → exit 1，stderr 打印原因 + 放行方式（加标记重提）+ "非免审"说明；
  5. 有标记或无 config/ 改动 → 放行。
- 注释行排除的理由：git 默认剥离 `#` 注释行，落在注释里的标记不会进入最终 message，守卫不认（有测试 `test_comment_line_marker_not_recognized` 固化）。

### Hook 落点选型理由（任务关键机制）

方案 (a)：**单个 commit-msg hook 同时查暂存文件 + 消息**。理由：

- pre-commit 阶段 `COMMIT_EDITMSG` 尚未写入（编辑器未跑），即使 `git commit -m` 消息检查也无法在 pre-commit 可靠完成；分两个脚本则共享逻辑的复杂度高于单脚本双检查。
- commit-msg 的 `$1` 参数天然给出消息文件路径，暂存列表在 commit-msg 阶段依然可读（`git diff --cached` 在 commit-msg 时仍反映 staged 内容，测试七例实证）。
- 不采用被禁的"pre-commit 读 COMMIT_EDITMSG"写法。

### 边界决定（任务 §2，均写进 README 与测试）

| 边界 | 决定 | 落点 |
|------|------|------|
| 删除 config 文件 | 算 config 变更，须标记 | README"行为"节；`test_config_delete_without_marker_rejected` |
| merge 提交 | 放行（拒绝会卡住 merge 状态无法安全回退） | README；commit-msg:7–9（MERGE_HEAD 检查） |
| `--amend` | 无特殊处理，同规则（含 config/ 仍须标记） | README；`test_amend_with_config_and_marker_allowed` |
| 标记匹配 | `[config-change]` 精确子串（`grep -qF`）、大小写敏感 | README；commit-msg:17 |
| 注释行标记 | 不认（git 剥离注释行） | README；`test_comment_line_marker_not_recognized` |

### 测试（任务 §3，失败测试先行）

新文件 `tests/test_precommit_config_guard.py`（102 行，8 例）：

- scratch 仓 fixture：临时 git 仓 + `user.email`/`user.name` 配好 + **安装走 README 同一路径**（`git config core.hooksPath <主仓 scripts/git-hooks>`），hook 实际在 `git commit` 里跑，非字符串匹配。
- 七例实测断言（含提交产生与否的 HEAD 核验）：
  1. `test_config_change_without_marker_rejected`——无标记被拒（exit 非 0 + stderr 含 `[config-change]` 提示 + HEAD 未动）；
  2. `test_config_change_with_marker_allowed`——有标记放行；
  3. `test_normal_commit_without_config_allowed`——不改 config 正常提交；
  4. `test_config_delete_without_marker_rejected`——删除也是 config 变更；
  5. `test_amend_with_config_and_marker_allowed`——amend 边界；
  6. `test_comment_line_marker_not_recognized`——注释行标记不认（`-F` 消息文件路径实测）；
  7. `test_hook_install_doc_matches_fixture`——防文档漂移（README 安装命令与 fixture 一致性）。
- 实测：`tests/test_precommit_config_guard.py` 单文件 **7 passed**（首轮跑）；合批见下。

### 自跑验证（数字铁律：实测为准）

- 守卫测试单文件：7 passed。
- 受影响 6 文件合批（守卫 + test_qc.py + test_qc_api.py + test_qc_interaction.py + test_qc_interaction_e2e.py + test_m6f_trace.py）：
  **104 passed, 1 skipped, 0 failed（59.57s，exit 0）**。
  注：`pyproject.toml:57` 的 `addopts = "-q"` 会吞最终摘要行，须 `-c /dev/null` 绕过才拿到实测计数。
- ruff：`tests/test_precommit_config_guard.py` All checks passed（唯一改动 py 文件）。
- `bash -n scripts/git-hooks/commit-msg`：SYNTAX-OK。
- 本 worktree 本轮未装 hook（`core.hooksPath` 保持为空）——装 hook 是每个 clone 的显式动作，本轮只交付脚本与文档。R3 轮如需本 worktree 生效再执行安装命令。

## 审查 findings 清单与处理

审查经 `bash scripts/deep_review_from_main.sh "18f0724..HEAD"` 执行（R2 落地的唯一入口，规则从 main 提取注入 prompt，`review_sha=7667f0d76832f1e2c7faaf387cb49670f8a21920`）。本地 main（d7b5c44）≠ origin/main（421ef0c）时脚本按设计取本地 main 并打印 WARNING。findings 文件：`reviews/review-7667f0d76832f1e2c7faaf387cb49670f8a21920.json`。1 blocker + 4 warning + 2 nit，逐条 double-check（本方在 scratch 仓实测复现）后处理：

| # | 级别 | 内容 | 复现结果 | 处理 |
|---|------|------|----------|------|
| 1 | blocker | `--name-only` 默认 renames 只列新路径，`git mv` 移出 config/ 逃检（commit-msg:12） | 属实：rename out 无标记提交 exit=0 放行 | **修复**：`--no-renames`；新测试 `test_rename_out_of_config_without_marker_rejected` 先红后绿 |
| 2 | warning | 默认 `core.quotePath=true` 非 ASCII 路径引号转义逃检（commit-msg:12） | 属实：`config/中文.yaml` 输出 `"config/\344..."` 无标记提交 exit=0 | **修复**：`-z` 输出；新测试 `test_non_ascii_config_name_without_marker_rejected` 先红后绿 |
| 3 | warning | `pipefail` 下 `printf | grep -q` 大暂存列表 SIGPIPE 误放行（commit-msg:15） | 属实：628908 字节列表 4 次无标记提交 exit=0 且 config/c.yaml 进提交 | **修复**：改 here-string/临时文件读法；中间态发现 `$(...)` 吃 NUL，最终改为 mktemp 临时文件 + `grep -aq '^config/' -z`；审查同场景复测 628908 字节 exit=1 拒绝 |
| 4 | warning | `--amend --no-edit` 复用旧标记放行新改动，README:28 声称名存实亡（README.md:28） | 属实（第一次复现因旧标记不在消息里被正确拒，补基线后复现 exit=0） | **接受为已文档化限制**：README 明示 `--amend --no-edit` 复用旧标记是 git 固有行为 + 新测试 `test_amend_no_edit_reuses_old_marker_pinned_limitation` 固化 |
| 5 | warning | merge 提交夹带 config/ 无标记要求，与"硬隔离"张力（commit-msg:7） | 属实：merge 携带 config/e.yaml exit=0 | **接受为已文档化风险**：README 补显式风险说明（分叉提交时已被守卫覆盖，merge 夹带属残余） |
| 6 | nit | `test_comment_line_marker_not_recognized` 只断言 returncode（tests:96） | 属实（代码亲读） | **修复**：补 `assert "[config-change]" in r.stderr` 对齐先例 |
| 7 | nit | `test_hook_install_doc_matches_fixture` 字符串匹配不防路径漂移（tests:99） | 属实（审查实测相对路径在本 clone 可用；fixture 用绝对路径因 scratch 仓无该目录） | **不修**：README 相对路径 `scripts/git-hooks` 从仓库根与子目录实测可用，属文档示例正确性而非守卫行为；防漂移加固收益低，留作未决 |

修复提交：`8e62d18`（hook + 4 测试）、`5f53a6b`（README）。修复后受影响 6 文件终跑：**107 passed, 1 skipped, 0 failed（60.43s）**；守卫测试 10 passed；ruff All checks passed；`bash -n` 过。

## 未决清单

1. **审查 nit 7**：文档漂移加固（fixture 在 scratch 仓里按相对路径装一次验证 hook 真的运行）——收益低未做，后续轮可加。
2. **本 worktree 未安装 hook**（`core.hooksPath` 为空）：装 hook 是每个 clone 的显式动作，本轮只交付脚本 + README；实际启用须跑 `git config core.hooksPath scripts/git-hooks`。
3. **本地 main 滞后 origin/main**（d7b5c44 vs 421ef0c）：审查规则取自本地 main，与 R2 轮已知环境事实一致，合 PR 前需同步。
