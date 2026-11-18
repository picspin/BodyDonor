# M7-S2 Rebase 验证轮报告

- 轮次：S2 rebase 后验证轮（rebase 已由协调方完成，本报告为验证与修复轮记录）。
- 日期：2026-11-19。
- Worktree：`/home/gogvf/work/BodyDonor-S2`，分支 `feat/m7-s2-three-set-split`，rebase 基线 origin/main `421ef0c`（PR #4 合并后）。
- 本轮提交：`371f487`（deep-reviewer 审查文件）+ 本报告 docs 提交。**不 push、不 merge、不开 PR。**

## 1. Rebase 前后与冲突清单

| 项 | 值 |
|---|---|
| 旧 HEAD（rebase 前） | `aa591c1` |
| 新 HEAD（rebase 后） | `a99e7a1`（本轮起始；本轮追加 `371f487`、报告提交） |
| rebase 基线 | origin/main `421ef0c` |
| 冲突 | **0 冲突**，9 提交完整重放（不 squash、不改写信息） |

**为什么是 0 冲突**：唯一与 main 重叠的文件是 `src/bodydonor/distill_data.py`——S2 只改了 1 行 import（`from .holdout import check_leak, load_holdout` → 加 `case_ids_hash`）+ 6 行 manifest 双哈希（`leak_check` 字典内新增 2 个键），main 自 7d644af 以来在该文件的改动（R3a 删 `--profile`、S3P2 改 `read_teacher_provenance` 等）都不落在这些行上；其余 10 个文件 main 自 7d644af 以来没碰过。七项意图锚点行（`distill_data.py:38` import、`:606-610` 双哈希、`holdout.py:64` check_derived_source、`qc_thresholds.yaml:81` holdout_30_dev、`eval/holdout.txt` 30 例、实测哈希 `218a663e218d4ce7`）全部未动。

### 重放后 diffstat（实测）

`git diff --stat origin/main..HEAD`（本轮起始时）：**11 files changed, 601 insertions(+), 18 deletions(-)**，与旧 S2 的 `7d644af..aa591c1` 逐文件一致：

| 文件 | 变动 |
|---|---|
| M6-C-REPORT.md / M6-D-REPORT.md | +2 / +2 |
| M6-F-REPORT.md | +7 |
| M7-S2-REPORT.md | +157 |
| config/qc_thresholds.yaml | 30（+/-） |
| src/bodydonor/distill_data.py | 8 |
| src/bodydonor/holdout.py | +49 |
| src/bodydonor/interaction_metrics.py | +51 |
| src/bodydonor/qc_priority.py | +7 |
| tests/test_m7s2_split.py | +293 |
| tests/test_qc.py | 13（+/-） |

`git status` 干净（仅未跟踪 `.venv-nninteractive`、`.venv-totalseg`）。

## 2. 七项意图逐项证据（rebase 后实测，文件:行号均为亲读）

| # | 意图 | 证据（rebase 后实测值） |
|---|---|---|
| 1 | gate `eval/holdout.txt` 30 例 | 实测 `load_holdout(Path('eval/holdout.txt'))` → n=30，`case_ids_hash` = `218a663e218d4ce7`（与协调方一致） |
| 2 | `case_ids_hash` import | `distill_data.py:38`：`from .holdout import case_ids_hash, check_leak, load_holdout` |
| 3 | manifest 双哈希 | `distill_data.py:606-610`：`train_case_ids_hash = case_ids_hash([c.case_id for c in pool])` + `gate_case_ids_hash = case_ids_hash([e.case_id for e in load_holdout(holdout_path)])` |
| 4 | `check_derived_source` | `holdout.py:64`（签名 `derived_case_ids, gate_case_ids=None, path=None`，相交 LeakError）；导出表 `:440` |
| 5 | `qc_thresholds.yaml` 已调规则标 `holdout_30_dev` | yaml `:81`（`rules.multi_teacher_consistency` source）；全文件 5 处 `holdout_30_dev`；`qc_priority.py` w3/w4 权重注释引用（yaml `:179` note） |
| 6 | 可靠性表 `(teacher,task,modality,class)`+n | `interaction_metrics.py:48` `HIGH_RISK_TEACHER_CLASSES`、`:94/:125/:138` teacher_reliability_entry 链（prostate 显式 low）；tests `TestReliabilityEntry` 5 条 |
| 7 | 聚合方式决策（w3 最差类 vs 一致性 mean 各自保留+理由） | `qc_priority.py:189` `component_disagreement` docstring「有意不同（非静默不一致，M7-S2 决策）」节（rebase 后在 :196-206 附近）；yaml `:84`/`:179` 两处 note 写明目的差异 + `derived_from_case_ids_hash = 218a663e218d4ce7` |

测试侧证据：`tests/test_m7s2_split.py` 19 条（TestCaseIdsHash 3 / CheckDerivedSource 3 / ThreeSetSplit 3 / MetadataHonesty 5 / ReliabilityEntry 5 / CompatEntry 1）+ `test_qc.py` 2 条旧元数据断言更新（`holdout_30_dev`/30）。

## 3. 受影响文件测试（本轮实测）

运行方式：`PYTHONPATH=~/work/BodyDonor-S2/src ~/work/BodyDonor/.venv/bin/python -m pytest ... -p no:warnings`（import 核查 `bodydonor.__file__` → `/home/gogvf/work/BodyDonor-S2/src/bodydonor/__init__.py` ✓）。

| 范围 | 实测结果 |
|---|---|
| `tests/test_m7s2_split.py` + `tests/test_qc.py`（S2 直接改） | **53 passed**（0.97s），0 failed 0 error |
| ruff（6 个改动 py 文件） | All checks passed |
| 四 API 调用方文件（grep 定向：`tests/test_distill_license_gate.py`、`tests/test_holdout.py`、`tests/test_m7_s3p2_backfill.py`、`tests/test_m7s2_split.py`） | **94 passed**（1.32s），0 failed 0 error |

**结论：无 S2 意图与 main API 的正面冲突，无旧 API 调用**——main R3a 删 `--profile`、S3P2 改 `read_teacher_provenance` 三元状态与 `check_task` 准入，S2 的测试都没有调用这些被改的 API 面。无需二选一裁决，未决清单无 API 兼容项。

## 4. 三点专项验证（实测值）

| 项 | 实测值 |
|---|---|
| gate 30 例哈希 | `218a663e218d4ce7`（`eval/holdout.txt` 30 例实算，与 manifest 旧值/协调方一致） |
| train 35 例哈希 | `b6f90c1fe6ccc16c`（本机 `~/data/BodyDonor/nnUNet_raw/Dataset501_BodyDonorDistill/manifest.json` 35 例实算，与报告 §1 表一致） |
| 两集不相交 | `tests/test_m7s2_split.py::TestThreeSetSplit::test_real_manifest_train_gate_disjoint` **1 passed**（本机 manifest 在位，真实跑非 skip）；`check_leak` 非空 raise 逻辑在 `holdout.py:159-175`（亲读），`tests/test_m7s2_split.py:105-109` check_leak 真跑断言在位 |
| calib 如实 pending/空 | `holdout.py:40-52` 三集分离 docstring 亲读：「calib = **现状 pending/空**（无独立校准集）……须标『样本内开发指标』（in-sample dev），不得冒充独立校准」；yaml `holdout_30_dev` 5 处标注 = 样本内开发指标声明；无任何 calib 数据伪造（`M7-S2-REPORT.md` §10 第 1 项未决如实记录 calib 缺失） |
| prostate 显式 low | `interaction_metrics.py:48-51`（「显式高风险类（M4 实测：教师 prostate Dice 0.5914，学生略高于教师……显式点名）」）+ `:138`（`reliability = "low"  # 显式依据：M4 实测教师 prostate Dice 0.5914`）亲读；`qc_priority.py:82` `_HIGH_RISK_TEACHER_CLASSES = frozenset({"prostate"})` 同口径；`M7-S2-REPORT.md:59` 保留依据段亲读 |

## 5. 全量重跑

- 命令：后台 detached `PYTHONPATH=~/work/BodyDonor-S2/src ~/work/BodyDonor/.venv/bin/python -m pytest tests/ --junitxml=/home/gogvf/work/BodyDonor-S2/m7-s2-rebase-impl-fulltest-junit.xml -p no:warnings > /home/gogvf/work/bodydonor-plans/s2-rebase-impl-fullrun.log 2>&1`，PID **617815**（记录，不管道化输出，无并行第二路 pytest）。
- 结果：**全量跑完，见 §5a（768 passed / 0 failed / 0 error / 0 skipped，exit 0）。**

## 5a. 全量核对（实测）

- **JUnit testsuite：`errors="0" failures="0" skipped="0" tests="768"`**，time 2694.926s（0:44:54，2026-11-19T04:20 起）。
- 日志 0 行 FAILED/ERROR（grep 实测），exit 0。
- **768 passed / 0 failed / 0 error / 0 skipped**（实测 summary `768 passed in 2694.94s (0:44:54)`）。
- 与收尾轮（2026-11-14）对比：当时 620 项收集、612 passed / 3 failed（GPU 锁相撞）/ 4 skipped（worktree db 空）；本轮 main 合并 PR #4 后测试集扩大到 768 项，且 4 条真实数据验收测试**全部真跑通过（0 skipped）**——上轮的 3 failed（锁相撞）与 4 skipped（db 空）均为环境性，本轮同环境复测消失，进一步证实非代码问题。
- GPU 锁纪律：全程无并行第二路 pytest。

## 6. deep-reviewer 审查（findings 清单与处理）

派 1 个 deep-reviewer 子代理审重放后 11 文件（重点：distill_data.py 8 行与 main 新代码结合处、holdout.py/interaction_metrics.py 对 main 现行 API 的调用）。findings 全文在 `M7-S2-REBASE-REVIEW.md`（已提交 `371f487`）。

**结果：0 Blocker，0 CONFIRMED Warning，2 nit**。逐条 double-check（带复现）：

| # | 级别 | 位置 | 缺陷 | 复现/归属核查（调用方实测） | 处理 |
|---|---|---|---|---|---|
| 1 | nit（PLAUSIBLE→实测属实） | `distill_data.py:652-653` | `__all__` 中 `"ALLOWED_TRAIN_SOURCES"` 重复两次 | `grep` 实测两行；`git show origin/main:src/bodydonor/distill_data.py` 同样两行（main 侧 646-647）→ **main 自带，非 rebase/S2 引入** | 不修（CLAUDE.md §3 不删既有问题；记未决 §8.1） |
| 2 | nit | `qc_priority.py:239` | 低频判断硬编码 `< 10` 未引用 `MIN_PER_CLASS` | 亲读确认；该行不在 S2 的 7 行 diff → **main 自带**；当前与 `interaction_metrics.MIN_PER_CLASS=10`（:46）、`holdout.MIN_PER_CLASS=10`（:32）三处同值，仓库既有模式即「常量+同口径注释」（如 `qc_priority.py:81`） | 不修（改善相邻代码非本轮；记未决 §8.2） |

无 CONFIRMED bug → 无需失败测试/修复流程。审查未覆盖项已由调用方补齐（亲读核实，见审查文件「已查无问题」表）：`component_convergence`（`qc_priority.py:136`）与 `record_review_decision`（`qc_reflux.py:25`）签名与调用点一致；holdout→models 无 import 环；w3/w4 调用链一致。

## 7. 与既有 S2 报告的关系

- `M7-S2-REPORT.md`（rebase 随带，157 行）记录的是收尾轮（2026-11-14）的结果，本轮不改动它；其 §11 提交列表为旧 hash（`ab8da49` 等），rebase 后新 hash 见本报告 §7。

### 提交新旧 hash 对照（9 提交，逐条 1:1，主题一致）

| 旧 hash（`7d644af..aa591c1`） | 新 hash（`421ef0c..a99e7a1`） | 主题 |
|---|---|---|
| `ab8da49` | `f1103e3` | feat: 三集分离——case_ids_hash + check_derived_source + manifest 双哈希 |
| `0473620` | `062eb11` | feat: 可靠性表 (teacher,task,modality,class)+n，低频独立特征；w3 聚合理由显式 |
| `51bb9e1` | `708fc93` | feat: 元数据诚实——已调规则不再标 cold_start，写实 holdout_30_dev |
| `3663c6e` | `d0251af` | test: 三集分离 19 条 + 更新旧元数据防回归断言 |
| `ea7afb4` | `cb9012d` | docs: M6-C/D/F 报告改标「样本内开发指标」+ M6-F 附录补记学生 0.8014 口径 |
| `0fd341d` | `1c95307` | docs: M7-S2-REPORT 草稿 |
| `d2a7209` | `0624587` | fix: derived_from_case_ids_hash 写实值 + 防回归测试 |
| `37e110c` | `d752f43` | docs: §7 测试表修正 |
| `aa591c1` | `a99e7a1` | docs: M7-S2-REPORT 定稿 |

## 8. 未决清单

**无 API 兼容未决**（受影响文件测试 94+53 passed，S2 意图与 main API 无正面冲突，无旧 API 调用需要裁决）。

本轮新增未决（均为 main 自带，不属 rebase 回归，提请用户定夺）：

1. `distill_data.py:652-653` `__all__` 重复条目 `"ALLOWED_TRAIN_SOURCES"`（main 自带 646-647，rebase 原样重放）——是否随下轮清理。
2. `qc_priority.py:239` 硬编码 `< 10` 未引用 `MIN_PER_CLASS`（main 自带，当前三处同值）——是否统一引用常量。

沿袭既有 S2 报告未决（`M7-S2-REPORT.md` §10，本轮不重复处理）：calib 集缺失、teacher_reliability_entry 消费方接线、local_artifacts marker、旧 manifest 双哈希字段、case_ids_hash 理论边界、worktree 无生产 db 数据。
