# S3P2-R3a 轮次报告：④ 删商业合规设施（R3 拆分轮）

- 日期：2026-11-18；分支 `feat/m7-s3-postmerge-2`；基线 R2 HEAD `d0ae8a5`。
- 范围：只做 ④（删商业合规设施）。⑤（36 条目逐项分析）另派，不在本轮。

## 0. LICENSE-CHECK.md 未 tracked 的发现（放最前面，请用户确认归属）

**`LICENSE-CHECK.md` 从未进过 git 仓库。** 实测：

```
git log --all -- "*LICENSE-CHECK*"   # 空输出
```

- tracked 的许可文档一直是 `LICENSES-THIRD-PARTY.md`（M0 验收物）。
- "LICENSE-CHECK" 内容实际存在于两处：
  1. 仓库外 `~/work/bodydonor-plans/M7-LICENSE-CHECK.md`（366 行，"一、总表"为
     7 教师商用资格总表）；
  2. `LICENSES-THIRD-PARTY.md` 内部（§1.3/§1.4 商业授权清单与持有主体、
     §2.2 nnInteractive 商用约束、§7 汇总决策表商用列、§8 蒸馏约束）。
- **按意图的执行**（不凭空造 tracked 文件）：把商业审计内容抽成
  `COMMERCIAL-CHECK-internal.md`（worktree 根，内部合规附件），加入
  `.gitignore`（文件留磁盘信息不丢），tracked 的 LICENSES-THIRD-PARTY.md
  头部留一行指向说明（cab477e）。
- **请用户确认**：原 M7-PLAN 提到的"LICENSE-CHECK.md 36 条商业审计表"归属是
  (a) 保持现状（内部附件不入仓）(b) 计划文档另存（plans 仓自管）还是
  (c) 需要 tracked 入本仓。本报告按 (a)+(b) 执行。

## 1. 提交列表（8 个，基线 d0ae8a5..HEAD，含本报告提交）

| 提交 | 说明 |
|---|---|
| cea6a4a | refactor: ④删商用蒸馏白名单常量与 --profile 双口径——门禁只留 research_distill 单口径 |
| a1926b8 | refactor: ④删 brain_aneurysm 商用阻断镜像——注册表条目/config 字段/CLI 展示/runner 纵深 |
| 6910d6d | refactor: ④删 config/settings.yaml 商用阻断——commercial_distill_tasks 与 blocked 列表 |
| 2a9be01 | test: ④测试同步——brain_aneurysm 商用阻断测试改写，白名单机制测试改内存条目 |
| cab477e | docs: ④商业审计内容抽成 gitignored 内部附件——LICENSE-CHECK.md 未 tracked 的归属处理 |
| 64d9562 | test: ④补 CLI --profile 退役断言——脚本源码文本断言 |
| 7651e45 | fix: ④prepare_dataset raw_root str 传参转 Path——CLI --out-root 直传 str 时 TypeError |
| 01ff7ef | docs: R3a 轮次报告（本文件） |

## 2. diffstat（实测 `git diff d0ae8a5..HEAD --stat`）

19 files changed, 141 insertions(+), 279 deletions(-)

```
 .gitignore                                  |   4 +
 LICENSES-THIRD-PARTY.md                     |   2 +
 config/settings.yaml                        |   8 +-
 scripts/prepare_distill_dataset.py          |   6 +-
 src/bodydonor/cli.py                        |   2 -
 src/bodydonor/config.py                     |   5 -
 src/bodydonor/distill_data.py               |  78 +++----------
 src/bodydonor/license_registry.py           |   5 -
 src/bodydonor/ontology.py                   |   3 +-
 src/bodydonor/ts_runner.py                  |  21 +---
 tests/test_api.py                           |   2 +-
 tests/test_distill_license_gate.py          | 170 ++++++++++------------------
 tests/test_license_whitelist.py             |  42 +++----
 tests/test_ontology.py                      |   3 +-
 tests/test_research_field_decision_table.py |   2 +-
 tests/test_segmentation.py                  |  33 +++---
 tests/test_storage_config.py                |   2 +-
 tests/test_teacher_registry.py              |  10 +-
 tests/test_ts_runner.py                     |  22 +---
```

## 3. ④ 逐项证据（文件:行号均为亲读）

### 3.1 COMMERCIAL_DISTILL_TASKS 删除

- 定义已删（原 `src/bodydonor/distill_data.py:87`）；全部读值处同步：
  - check_distill_source 商用分支（原 :143/:146）已删；
  - check_teacher_task_commercial（原 :176-182）整个函数删除——grep 全仓确认
    无 src/scripts/eval 调用方，只有测试 import（已同步删）；
  - prepare_dataset manifest `label_policy.teacher_tasks`（原 :629）已删；
  - `__all__`（原 :693）删 `COMMERCIAL_DISTILL_TASKS` 与
    `check_teacher_task_commercial`。
- 现 `src/bodydonor/distill_data.py:84-121`：`check_distill_source(source,
  teacher_task, experimental)` 三参——REJECTED_SOURCES → 白名单 → experimental
  → total 缺 task fail-closed（:106-111）→ **total 资格委托
  ontology.mask_allowed_in_distill_training（:112-119，注册表 research_distill
  单口径）**。
- 测试 import 同步：tests/test_distill_license_gate.py:15-26 删两符号。
- 残留：`grep -rn "COMMERCIAL_DISTILL_TASKS" src/ scripts/ eval/ config/` 零命中。

### 3.2 --profile commercial 分支删除

- `scripts/prepare_distill_dataset.py:44-46` 的 `--profile` 参数已删（默认
  `"commercial"` 的行为变化：**CLI 现在只走 research_distill 单口径**——
  source=total 资格读注册表 research_distill，`total` 条目
  research_distill=allowed，对既有 total 来源数据集行为等价；non-total 教师
  走 blocked/unverified/无条目三档阻断）。
- `check_distill_source` 与 `prepare_dataset` 的 profile 参数一并去掉；
  `mask_allowed_in_distill_training_strict` 同步去参
  （src/bodydonor/distill_data.py:123-133）。
- 全仓 `--profile` 零残留（tests/test_distill_license_gate.py:882-894 以脚本
  源码文本断言钉住：`"--profile" not in src`、`'default="commercial"' not in src`、
  main 直调不带 profile）。

### 3.3 blocked_tasks brain_aneurysm 商用理由删除（先核实后执行）

- **核实**：注册表 usage 只有商业一句（LICENSE_FACTS
  `totalsegmentator.brain_aneurysm` usage = "BLOCKED — no commercial license
  available"，原 src/bodydonor/license_registry.py:494-497），无非商业理由
  → 删除。上游 README 原文（LICENSES-THIRD-PARTY.md:135-138 逐字）也只有
  许可一句。
- 删除三处镜像：
  1. LICENSE_FACTS 条目（license_registry.py，现 :476-522 无 brain_aneurysm）；
  2. config/settings.yaml 原禁用注释 + blocked 列表（teacher 节现只有
     allowed_tasks，:40 起）；
  3. ts_runner.py `RUNNER_BLOCKED_TASKS`（原 :42）与 run_task 内镜像检查
     （原 :98-108）——头部契约注释同步（白名单主进程 check_worker_task 即终审）。
- **机制保留**：`TeacherConfig.blocked_tasks` 字段（config.py:45，default 空列表）、
  check_task 执行层（license_registry.py:546 一票否决）、CLI/API 展示
  （cli.py:233、summary() :530）——blocked 列表现在为空，机制可由内存条目验证。
- 测试覆盖三处不再阻断 + 机制不倒退：
  - tests/test_license_whitelist.py:25-33（内存条目一票否决）、:37-41
    （项目 config blocked==[]）、:107-114（yaml 一致性断言 blocked==[]）；
  - tests/test_segmentation.py:40-48（worker 层内存条目 BLOCKED）；
  - tests/test_teacher_registry.py:294-298（fixture 占位任务
    registry_blocked_task 一票否决）。
- brain_aneurysm 本身仍 fail-closed：注册表无条目 → `task 'brain_aneurysm'
  not in teacher registry`（SegmentWorkerError，实跑复核，见 §5）。

### 3.4 商业审计内容附件化（LICENSE-CHECK 归属处理）

- `COMMERCIAL-CHECK-internal.md`：8 教师商用资格总表（plans 总表商用列）+
  6 条商用约束（LICENSES §1.3/§1.4/§2.2/§7/§8 商用句的汇总），含 R3 ④
  行为变化注记。
- `.gitignore:47-49` 加入；`git check-ignore` 实测 ignored-ok。
- LICENSES-THIRD-PARTY.md:7-8 留指向说明（ tracked 文档内的正文不动——
  :137-140/:333/:350 的 brain_aneurysm 原文引用是核查记录，非白名单设施，
  保留如实记录历史）。

### 3.5 行为变化（报告写明）

1. **CLI 默认口径**：`prepare_distill_dataset.py` 不再接受 `--profile`；
   许可门禁单口径 = check_distill_source 来源白名单 + 注册表
   research_distill 资格。对既有数据（来源全是 total/task=total）行为等价。
2. **Python API**：`check_distill_source` / `mask_allowed_in_distill_training_strict` /
   `prepare_dataset` 去 profile 参数；非法 profile 的 ValueError 分支随之消失
   （无参可传，不再是防线）。
3. **/license 端点与 CLI status**：不再含 `commercial_distill_tasks` 键；
   `blocked_tasks` 为空列表。
4. **manifest**：`label_policy` 少 `teacher_tasks` 键（读方
   register_student_model.py 只 dict 透传，无 KeyError 风险——审查核实）。

## 4. 测试与数字（实测）

| 文件 | 数字 |
|---|---|
| tests/test_distill_license_gate.py | 50 passed（含新增 CLI 退役断言） |
| tests/test_license_whitelist.py | 8 passed |
| tests/test_teacher_registry.py | 25 passed |
| tests/test_research_field_decision_table.py | 6 passed |
| tests/test_segmentation.py | 21 passed |
| tests/test_api.py + test_storage_config.py + test_ts_runner.py | 25 passed |
| tests/test_m5e.py + test_nninteractive_backend.py | 35 passed（后台实测 153.00s） |
| **受影响文件合计** | **170 passed, 0 failed, 0 errors** |
| ruff（全仓 `ruff check .`） | All checks passed |

- 零残留 grep（§3.1/§3.2）：src/scripts/eval/config 零命中；tests 中历史
  字符串仅 R3 ④ 说明注释与 hasattr 反向断言（已列明）。
- 实跑复核：registry 对 brain_aneurysm 返回 `not in teacher registry`、
  check_worker_task raise SegmentWorkerError；`prepare_distill_dataset.py
  --dry-run` exit 0（单口径门禁 + 预检通过）。
- 实测注记：prepare_dataset dry_run 全 35 例 merge 实测前 5 例 79s（线性外推
  ~9 分钟量级），与 M6 记录一致——**慢非挂**（逐 case nib.load 解压 117 文件）。

## 5. 审查 findings 清单与处理

- deep-reviewer 子代理 1 个（对抗性审查，只读），findings 写
  `M7-S3P2-R3a-REVIEW.md`。结论：**Blocker 0，W 0**。
- 审查方自认未覆盖两处，实现方补查：
  1. tui.py / static/ 的 grep → 补查零命中（无 finding）；
  2. pytest 实跑 → 实现方自行实跑 §4 全部数字（二次核实）。
- 无属实 bug，无需失败测试先行修复。

## 6. 未决清单

1. **LICENSE-CHECK.md 归属待用户确认**（§0）：内部附件方案已执行，是否需要
   tracked 入仓由用户定。
2. **⑤ 另派未做**：36 条目逐项商业分析（输出大户）不在本轮。
3. **prepare_dataset dry_run 性能**：~9 分钟量级（M6 已记录"慢非挂"），
   未做优化（非本项范围）。
4. **LICENSES-THIRD-PARTY.md 历史原文引用保留**：:137-140/:333/:338/:350 的
   brain_aneurysm 禁用句是核查记录（README 逐字摘录），非白名单设施——保留
   如实记录；若用户希望同步改写为"已随 R3 ④ 移除阻断"口径，需另行小改。
