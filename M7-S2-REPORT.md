# M7-S2-REPORT — 评估三集分离与元数据诚实

日期：2026-11-14。分支：`feat/m7-s2-three-set-split`（独立 worktree `~/work/BodyDonor-S2`，基于 main `7d644af`）。依据：NEXT-ITERATION-PLAN v3.2 §2 S2、M7-PLAN v3 §4.2/§4.3。

## 1. 三集定义与冻结

| 集合 | 构成 | 用途 | 哈希（`case_ids_hash`，sha256 前 16 hex） |
|---|---|---|---|
| **gate** | `eval/holdout.txt` 30 例（ts_public 15 + amos 15） | 只用于最终验收与回归 | `218a663e218d4ce7` |
| **train** | 蒸馏实际名单 35 例（AMOS 25 + CT-Lite 10，`distill_data.build_train_pool`；本机 manifest `n_cases=35`） | 蒸馏 | `b6f90c1fe6ccc16c` |
| **calib** | **pending/空（现状如实定义，未造数据）** | — | — |

- train 与 gate 不相交：本机 manifest 逐 case 与 holdout.txt 交集为空（`check_leak` + manifest `leak_check.rechecked_with_real_train_list=true` 兜底）。
- calib 现状：**没有独立校准集**。凡在这 30 例上调过的派生统计，其 `derived_from_case_ids_hash` 就是 gate 30 例哈希，标「样本内开发指标」（in-sample dev），不得冒充独立校准（M7-PLAN §4.3.9：另建 calib 前不再用这 30 例调阈值/名单/聚合）。

### 实现（`src/bodydonor/holdout.py`）

- `case_ids_hash(case_ids)`：冻结 case 列表哈希（排序 + `'\n'` 连接 + sha256，16 hex）。
- `check_derived_source(derived_case_ids, gate_case_ids, path)`：派生统计来源与 gate 相交检查，非空即 `LeakError`（M7-PLAN §4.3.2）。
- 模块 docstring 新增三集分离节（gate/calib/train 定义与现状）。
- `distill_data.prepare_dataset` manifest `leak_check` 新增 `train_case_ids_hash` / `gate_case_ids_hash` 双哈希（旧 manifest 无此二字段，重新准备数据时写入）。

## 2. 派生统计记录来源 + check_leak 扩展

- 派生统计（可靠性低频名单、w4 名单、multi_teacher_consistency 聚合方式、sampling_priority w3/w4 定义）的来源哈希记在各处 config note（`derived_from_case_ids_hash` = gate 30 例哈希）。
- `check_derived_source` 与既有 `check_leak`（训练名单）并行：前者查派生统计来源，后者查训练集。相交即中止（LeakError），测试挡住（见 §5）。

## 3. 聚合方式决策：w3 vs multi_teacher_consistency（各自保留 + 理由）

**决定：不统一，两处各自保留聚合方式，理由实证写死。**（M7-PLAN §4.3.6 二选一中选「二者都写明理由」——统一成一种会破坏一边的验收/判别力。）

| 处 | 聚合 | 实证依据 |
|---|---|---|
| `qc_priority.component_disagreement`（w3 抽检排序） | 最差类 `1 - min(per_class_dice)` | s0495 prostate 整类漏检 min 0.0 而 mean 0.8771，mean 聚合下该进前 20% 的 case 排不进；抽检目的是找出最坏 case 交人审，宁过审不漏审 |
| `config/qc_thresholds.yaml rules.multi_teacher_consistency`（QC flag） | mean | case 级最差类聚合在 0.80 下 flag 24/30（无判别力，基本全审）；mean flag 5/30（有判别力，flag 的正是分歧最大的 5 例） |

- 统一为 mean 会破坏 M6-c 排序验收（s0495/s0556 排不进前 20%）；统一为最差类会破坏 M4 一致性判别力（24/30 全审）。两处目的不同（排序找最坏 vs flag 估一致率），非静默不一致。
- 显式说明落点：`component_disagreement` docstring（新增「有意不同（非静默不一致，M7-S2 决策）」节）、yaml 两处 note。

## 4. 元数据诚实修正（逐项核对，非批量改）

`config/qc_thresholds.yaml`：

| 项 | 改前 | 改后 | 依据 |
|---|---|---|---|
| header source 注记 | 仅 cold_start / calibrated_on_N | 新增 `holdout_30_dev` 定义（样本内开发指标说明） | M7-S2 元数据诚实 |
| `rules.multi_teacher_consistency` | `source: cold_start`、`calibrated_on: 0` | `source: holdout_30_dev`、`calibrated_on: 30`、`calibrated_at: "2026-11-11"` | 聚合方式已在 30 例上选定；note 原文「source 保持 cold_start、calibrated_on 保持 0」删除，改为写实来源 + 哈希引用 |
| `sampling_priority` 节 | `source: cold_start`、`calibrated_on: 0` | `source: holdout_30_dev`、`calibrated_on: 30`、`calibrated_at: "2026-11-12"` | w3 最差类聚合与 w4 低频占比定义在 30 例上实测选定 |
| w1/w2 权重注释 | 未标 | 加「未调，cold_start」 | 逐项核对：未在 30 例上调整，保持 cold_start 不动 |
| w3/w4 权重注释 | 未标 | 加「在 30 例上选定/验证（holdout_30_dev）」 | 逐项核对 |
| 其余规则（volume_prior、connected_component、holes、boundary_smoothness、hu_distribution、interaction_convergence、interaction_abandonment、left_right_symmetry）及 gating 节 | cold_start / 0 | **不动** | 未在 30 例上调过，逐项核对保持 cold_start |

阈值数值全部未动（threshold 0.80、等权 1.0 等保持原值）——本切片只改元数据诚实，不调参。

## 5. 可靠性表重构（`src/bodydonor/interaction_metrics.py`）

- 新增 `teacher_reliability_entry(cls, per_class_case_count, task, modality, teacher)`：可靠性按 **(teacher, task, modality, class)** 四元组组织并**附支持数 n**（现有教师评估产物只有 TS total CT 117 类，其余维度如实填缺省）。
- **低频是独立特征**：条目返回 `low_frequency` 布尔（< MIN_PER_CLASS cases）与 `reliability` 分离——低频类 `reliability="unknown"`（支持数不足，无法判定），不再折成 `"low"`；同一信号不再在可靠性先验与 w4 里被重复计入为不可靠（消费方按需加权）。
- prostate 显式 low 保留并写明依据（M4 实测教师 prostate Dice 0.5914，`HIGH_RISK_TEACHER_CLASSES` 注释 + 函数内注释），不移入 calib 重新估计（无独立 calib 集）。
- 缺计数数据：`n=None`、`reliability="unknown"`（不猜）。
- 兼容入口 `teacher_reliability()` 语义不变（`tracker.build_metrics` 打标、qc.py high_risk_classes、app.py 消费方行为一致）；新条目供 M7 消费。

## 6. M6 报告改标（加注/改标，不重写历史）

- `M6-C-REPORT.md` §6 硬性验收后加 M7-S2 改标段：排名与验收为「样本内开发指标」。
- `M6-D-REPORT.md` §2 三档实测数字后加 M7-S2 改标段：门控比例（6/18/6）为「样本内开发指标」；分档比例本身仍是 cold_start 未校准。
- `M6-F-REPORT.md` §6 trace-back 后加 M7-S2 改标段；新增 §12 附录：补记学生 0.8014 / `gate_status` passed 的来源与口径（in-scope 分口径评，naive 117 类对照保留，口径错误纠正过程，验收语料为 gate 30 例——0.8014 标样本内开发指标）。
- 原数字与原结论全部未动。

## 7. 测试与验证

新增 `tests/test_m7s2_split.py` 19 条（收尾轮 deep-reviewer 修复后 20 条，MetadataHonesty 新增 derived_hash_written_verbatim）：

| 组 | 条数 | 覆盖 |
|---|---|---|
| TestCaseIdsHash | 3 | 排序稳定性 + 增删即变 / gate 哈希与已知值一致（实算断言） |
| TestCheckDerivedSource | 3 | 派生统计来源与 gate 不相交通过 / 相交 LeakError / 默认读真 gate 文件（gate 自身派生 → LeakError） |
| TestThreeSetSplit | 3 | train 池与 gate 不相交（check_leak 真跑）/ train_hash != gate_hash / 本机真实 manifest 双哈希+不相交（local_artifacts，缺失 skip） |
| TestMetadataHonesty | 5 | multi_teacher source=holdout_30_dev+calibrated_on=30+数值未动 / derived_from_case_ids_hash 写实值且与 holdout.txt 实算一致 / sampling_priority w3/w4 标记+w1/w2 未调如实记 / 8 条未调规则保持 cold_start / gating 保持 cold_start |
| TestReliabilityEntry | 5 | (teacher,task,modality,class) 组织 / n+低频独立（unknown）/ prostate 显式 low+依据 / 缺数据 unknown / 维度覆盖 |
| TestCompatEntry | 1 | teacher_reliability 语义不变 |

既有测试更新：`tests/test_qc.py` 2 条断言旧元数据（`cold_start`/`calibrated_on: 0`）更新为写实值（`holdout_30_dev`/30）——正是本次要改的不诚实元数据的防回归断言。

### 收尾轮全量 pytest（2026-11-14）

`PYTHONPATH=~/work/BodyDonor-S2/src` + repo venv，620 项收集，**612 passed / 3 failed（重跑后全绿）/ 4 skipped**（首轮 316s + 重跑 90.75s）：

- 3 failed = `test_interaction_metrics.py::TestCorrectSubmitE2E` 3 条——**环境性 GPU 锁相撞**，非代码 bug：本 worktree 全量跑与另一会话（S1，主 checkout）的 pytest 并行，后者持 `~/data/BodyDonor/.gpu.lock` 批量推理锁，交互修正端点按设计 fast-fail 409（「等批量任务结束再试」）。**另一会话结束后重跑该类：3 passed（90.75s）——失败确认为锁相撞，代码无问题。**
- 4 skipped 逐项（`pytest -rs` 逐文件核对，skipif 均为 `_real_products_available()`）：
  - `test_m6d_gating.py::test_gating_three_tiers_30_holdout`（`:402`，三档验收）：**数据库为空**——`settings.database.url = sqlite:///./bodydonor.db` 相对 CWD，worktree 的 `bodydonor.db` 有表但 `model_versions` 0 行，`load_student_prediction('s0045')` 查不到 gate_status=passed 的学生版本即 raise → skip。主 checkout 的 db 有该行（环境性：worktree 无生产 db 数据，非本轮改动引入）。
  - `test_m6e_wiring.py::test_regression_419_pairs_flag_baseline`（`:267`，419 对真实回归）：同原因。
  - `test_m6c_priority.py::test_ranking_consistency_30_holdout_top20`（`:392`，排序检验）：同原因。
  - `test_m6f_trace.py::test_real_holdout_trace_back_30_cases`（`:835`，trace 验收）：同原因。

其余真实数据 skipif 文件全量跑中**未静默跳过**：`~/data/BodyDonor` 真实产物 + worktree `eval/` 复制件齐全，`test_qc_interaction_e2e.py`、`test_nninteractive_backend.py`、`test_interaction_metrics.py` 的真实推理测试在 316s 内真实跑过（TestCorrectSubmitE2E 3 条除外，见上）。

ruff：全量 `ruff check .` All checks passed（`ruff format --check` 不在本仓库约定内，65 文件未 format 属既有状态，未动）。

环境说明：repo `.venv` editable install 指向主 checkout（S1 工作区），本 worktree 用 `PYTHONPATH=~/work/BodyDonor-S2/src` 覆盖运行；eval 运行产物（holdout_results.json 等三个 gitignore 文件）从主 checkout 复制到本 worktree 供测试（只读复制，未提交）。

## 7b. deep-reviewer 收尾审查（三约束流程：对抗审查 → 逐条复核 → 红绿修复）

三个并行只读 subagent（split/hash、metrics/metadata、报告 docs）共报 12 条候选，逐条读码/实算复核：

**已验证属实并修复（2 blocker 级处理，先失败测试后修复再绿）：**

| # | severity | 位置 | 问题 | 处理 |
|---|---|---|---|---|
| 1 | blocker | `config/qc_thresholds.yaml:84`、`:179` | 两处 note 只写公式（「该 30 例排序 sha256 前 16 hex」）不写值，引用的 `derived_from_case_ids_hash` 无法核对 | 新增 `test_derived_hash_written_verbatim`（红：断言 note 含 holdout.txt 实算哈希）→ 两处写实 `218a663e218d4ce7` → 绿（20+55 passed）。提交 `d2a7209` |
| 2 | warning | `M7-S2-REPORT.md` §7 | 测试表与文件不符：TestMetadataHonesty 标 5 实为 4（新测试前）、TestCaseIdsHash 3 条未列入，表合计 17 vs 正文 19 | 修正表（补 TestCaseIdsHash 行 + MetadataHonesty 覆盖列写实测算断言）。提交 `37e110c` |

**复核后不修（未验证为缺陷，属设计决策/记录如实）：**

- `check_derived_source` / `teacher_reliability_entry` 未接入生产路径：**如实**——报告 §5/§10 已写「新条目供 M7 消费」「本轮目标保持调用方行为一致」，提交说明与代码一致，属计划内接线，记未决第 2 项。
- yaml `source: holdout_30_dev` 为静态手写字段无运行时校验：设计如此（config diff 即审计），防回归测试已挡误改。
- `case_ids_hash(['a\nb']) == case_ids_hash(['a','b'])` 换行碰撞、空列表返回空串哈希：真实边界，但 gate/train 列表来自受控文件（30 例无重复无换行，实算核对），属理论碰撞，记未决。
- `test_manifest_records_both_hashes` 未调 `prepare_dataset`：属实但测试 docstring 已如实写「最小覆盖：只验证 build_train_pool 层的哈希（prepare_dataset 预检需要 TS 真实 mask，属于 local_artifacts 范围）」，非名不符实。
- 旧 manifest 无双哈希字段时 `if "train_case_ids_hash" in leak` 静默跳过：与 §10 第 5 项记录一致，S1 合流后补 marker 时一并收紧。

**未决（不硬修）：** 上述后五条中前两条已如实记录，换行碰撞/空列表两条新增到 §10。

## 8. 与 S1 fixture 可能重复的记录

- 本轮 S2 未依赖 S1 的合成 fixture（S1 在 `fix/ci-m6e-eval-file` 分支带未提交改动，未跨分支取用，未被 S1 阻塞）。
- S2 测试自带小型合成 fixture（tmp_path 造 holdout.txt / 训练池目录），将来与 S1 的合成 fixture 可能重复（如 holdout 门禁、QC pipeline 的合成 case 构造）；两路 fixture 合并留待两分支合流时统一，不在本切片跨分支合。
- 依赖本地产物的测试未加 `@pytest.mark.local_artifacts` 标记（仓库现无此 marker 注册，pyproject 无 markers 配置；S1 正在引入，待其落地后统一补标）——本机 manifest 测试以 `pytest.skip` 兜底，CI 缺产物时静默跳过，行为与既有模式一致。

## 9. 红线遵守

- 未用这 30 例调阈值/名单/聚合方式（本切片只改元数据与加检查，数值零改动）。
- 未提交真实 hold-out JSON（三个 eval 运行产物只复制到 worktree 本地，未入 git）。
- 未做学习型门控器；未碰商用蒸馏审批/Edge/mTLS/Orthanc/MinIO/Redis；未对外宣称临床性能/学生门禁 passed/MR QC 可用（报告改标为「样本内开发指标」正是为此）。
- 未动 main、未推远端、未碰 S1 工作区（`~/work/BodyDonor` 工作树未提交改动零接触）。

## 10. 未决问题

1. **calib 集缺失**：派生统计来源哈希即 gate 哈希（样本内），另建 calib 集（开发划分或生产回流 ≥20 例）后须复核全部 `holdout_30_dev` 项（M7-PLAN §4.3.9）。
2. **teacher_reliability_entry 的消费方接线**：新条目本轮未接入 w1/w4 消费路径（w1 仍读 provenance 条目的 `teacherReliability` 标记、w4 仍用低频名单）——保持调用方行为一致是本轮目标；M7 加权训练时切换到四元组条目 + low_frequency 独立特征。
3. **local_artifacts marker**：待 S1 的 marker 注册合流后，为本机 manifest 测试补标。
4. **全量 pytest**：本收尾轮已跑（见 §7），慢速真实数据 skipif 项仍未跑（依赖 `~/data/BodyDonor` 真实 mask + TS 推理，~30 分钟）。
5. **旧 manifest 无双哈希字段**：本机 Dataset501 manifest 是旧格式，重新准备数据时写入 `train_case_ids_hash`/`gate_case_ids_hash`（测试对旧格式兼容跳过）。
6. **case_ids_hash 理论边界**（deep-reviewer 提出，复核属实但不修）：id 含 `\n` 时与拆分列表碰撞（`['a\nb']` ≡ `['a','b']`）、空列表返回空串哈希 `e3b0c442…`——gate/train 列表来自受控文件（30 例无重复无换行，实算核对），生产无此输入；若将来接受外部 id 列表，改为长度前缀或拒绝含 `\n`。
7. **worktree 无生产 db 数据**：`model_versions` 在本 worktree `bodydonor.db` 为 0 行（db url 相对 CWD），4 条真实数据验收测试因此 skip——S1 合流或 CI 环境用固定 db url / fixture 注入后可跑。

## 11. 提交列表

| hash | 内容 |
|---|---|
| `ab8da49` | feat: 三集分离——case_ids_hash + check_derived_source + manifest 双哈希 |
| `0473620` | feat: 可靠性表 (teacher,task,modality,class)+n，低频独立特征；w3 聚合理由显式 |
| `51bb9e1` | feat: 元数据诚实——已调规则不再标 cold_start，写实 holdout_30_dev |
| `3663c6e` | test: 三集分离 19 条 + 更新旧元数据防回归断言 |
| `ea7afb4` | docs: M6-C/D/F 报告改标「样本内开发指标」+ M6-F 附录补记学生 0.8014 口径 |
| `0fd341d` | docs: M7-S2-REPORT 草稿 |
| `d2a7209` | fix: derived_from_case_ids_hash 写实值 + 防回归测试（deep-reviewer） |
| `37e110c` | docs: §7 测试表修正（deep-reviewer） |
