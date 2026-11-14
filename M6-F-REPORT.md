# M6-F-REPORT — DecisionTrace（决策审计痕迹）

日期：2026-11-12。基线：main c227fb0。M6-f 从本 brief 落地（M6-PLAN.md 不在本机，按任务书 v2 M6-f 实现）。

## 1. 形态决策：轻量实体（DecisionTrace），不是 JSON 列

**选择：`DecisionTrace` 实体（`src/bodydonor/models.py`）+ Alembic 迁移 `a4f9e2c81d03`。**

权衡（任务书 1）：

| 维度 | JSON 列挂 QCReport | 实体（选择） |
|---|---|---|
| M7 校准查询性 | 先 join qc_reports 再解 JSON；按 stage/tier/信号分布过滤要全表扫描解 JSON | SQL where 直查（`ix_decision_traces_stage` / `ix_decision_traces_case_id` / `ix_decision_traces_mask_version_id` 三索引）；`SELECT * FROM decision_traces WHERE pipeline_stage='gate_decision' AND json_extract(gate_decision,'$.tier')='forced'` |
| schema churn | 零新表，但 qc_reports 行膨胀（每 mask 多条决策历史塞一列） | 一新表 + 一迁移；不改既有实体任何列 |
| append/update 语义 | 一列多版本历史要自造数组协议 | 一行一决策，append-only 天然；human_outcome 补填只 update 两列 |

关键差异：一条决策一个阶段一行（qc_scoring / gate_decision 各一条），QCReport 是 mask 级结果、不是决策级——JSON 列会把「同 mask 多轮重评的多条决策」压进一行，M7 校准要按决策粒度取样本（每次 gating 分档一个样本点），实体行的粒度直接对齐。

## 2. Schema（models.DecisionTrace，14 列）

```python
trace_id          dt-{uuid8} 主键（default=new_id）
case_id           qc 阶段可未给出（null，不硬造）
series_id         锚定 Series
mask_version_id   锚定 MaskVersion
timestamp         决策时刻（default=utcnow）
pipeline_stage    'qc_scoring' | 'gate_decision'
input_refs        {mask_version_id, qc_report_id, student_source}  # id/键指针，非像素
process_signals   interaction_coverage + M6-b 两规则信号 + M6-c priority 分量（gate 阶段）
rules_triggered   与同决策流 QCReport.flags 逐项一致（验收 b）
gate_decision     {tier, review_task_generated, review_task_id?, sample_group?}；qc 阶段 null
human_outcome     null=尚无人工决策；回流后 {decision, notes?, decided_at}
calibration       {priority_weights w1–w4, gating_ratios 三比例, source{...}} 生效快照
review_task_id    生成的任务 id（null=auto 不建任务）
updated_at        onupdate（human_outcome 补填时刻）
```

任务书 2 逐项覆盖：

- **pipeline_stage**：本实现取两处决策点——`qc_scoring`（qc_series 产 QCReport 同一事务）与 `gate_decision`（generate_review_tasks 分档+建任务同一事务）。任务书列举的 student inference / multi-teacher consistency 阶段不另记行：它们的输出即 input_refs（student_source=ModelVersion id）与 process_signals（multi_teacher 逐类 Dice 进 w3/w3_available），决策点在 QC 评分与门禁分档——审计链从这两行已可回溯到推理产物。
- **input_refs**：id/键指针（mask_version_id / qc_report_id / student_source=ModelVersion id 或 "no case_id"/"unavailable: …"），无像素。
- **process_signals**：M6-c priority 分量（w1–w4 值 + availability + w3_available + plan note）+ M6-b 规则信号（interaction_convergence / interaction_abandonment 的 detail 全文，skipped 时含原文 reason）+ **interaction_coverage**（该 case 是否有过 nnInteractive 交互——M6-b 指出小样本本身是要记录的事实；纯自动 case `coverage=False`，M7 校准看得到「无交互样本」的分布）。
- **rules_triggered**：与 QCReport.flags 逐项一致（见 §5 验收 b）。
- **gate_decision**：M6-d tier（forced/suggested/auto）+ review_task_generated + id/group；auto → `review_task_generated=False`、无 id。
- **human_outcome**：恒 null 直至人工决策（不造假；M4 报告回流 0/20，M6 大概率仍空——空缺本身是信号，留给 M7 决策回流）。补填走 `/qc/review-tasks/{id}/decision` 端点：**与 ReviewTask 状态写库同一事务**，`update_human_outcome` 幂等不覆盖（已补填的 trace 不改写——历史保留；重放决策不产生新值）。其余列永不回写（append-only；重决策产生新 ReviewTask 由新 trace 承接）。
- **calibration（M7 必需）**：`snapshot_calibration(thresholds)` 从 config 提取生效中的 `priority_weights`（w1–w4）、`gating_ratios`（forced/suggested/auto 三比例）、`source`（sampling_priority/gating 各自的 cold_start|calibrated_on_N 注记）。支持 M6-d 遗留（M7 决策回流后重算三比例/权重——重算前后 trace 可 diff 何时生效什么参数）与 M6-c 遗留（w1/w2 真实数据回归——trace 记录当时的权重与信号分布）。

## 3. Wiring（决策在哪发生就在哪记，不是事后拼接）

1. **`qc_series`（qc_pipeline.py）**：QCReport 落库的**同一事务**里 `s.add(record_qc_trace(...))`——flags/rule_scores 用同一 `result` 变量（验收 b 的构造级保证），student_source 与 interaction_metrics 是本次评分的真实输入。
2. **`generate_review_tasks`（qc_pipeline.py）**：ReviewTask 建/升级的同一事务里，对候选池**全量**各记一条 `gate_decision` trace——auto 档也在内（自动接受本身是门禁决策，M7 校准需要 auto 样本的信号分布）；tier 取 `gating_tier_by_mv`（gating 汇总回填的 ranked 行级 tier），ReviewTask id/group 取本次 created 列表；旧 70/30 路径也记（tier=group 语义：flagged/clean，不冒充三档）。flags 从**落库 QCReport.flags 列**读取（同一查询带出，验收 b 的数据源）。
3. **`/qc/review-tasks/{id}/decision`（app.py）**：ReviewTask status/decision 写库的同一事务里补填锚定任务的 gate_decision trace 的 human_outcome。

M6-a/b/c/d 规则逻辑零改动（diff 只增不改）；eval/holdout.txt、ModelVersion gate_status、overall.hard_flag_rules 未触碰。

## 4. 一个 case 的完整 trace 示例（验收 a 实测：test_acceptance_a_trace_back_full_chain）

合成 case-wired（teacher mask + 学生 monkeypatch + 决策回流 accepted）：

```jsonc
// qc_scoring 行
{
  "trace_id": "dt-…", "case_id": "case-wired", "series_id": "series-wired",
  "mask_version_id": "mv-wired", "pipeline_stage": "qc_scoring",
  "input_refs": {"mask_version_id": "mv-wired", "qc_report_id": "qc-…", "student_source": "student-test"},
  "process_signals": {
    "interaction_coverage": false,                       // 该 case 无 nnInteractive 交互（如实记录）
    "interaction_convergence": {"flagged": false, "detail": {"skipped": "no interaction metrics"}},
    "interaction_abandonment": {"flagged": false, "detail": {"skipped": "no interaction metrics"}}
  },
  "rules_triggered": [],                                  // 与 QCReport.flags 逐项一致
  "gate_decision": null, "human_outcome": null,
  "calibration": {"priority_weights": {"w1": 1.0, "w2": 1.0, "w3": 1.0, "w4": 1.0},
                  "gating_ratios": {"forced_ratio": 0.2, "suggested_ratio": 0.6, "auto_ratio": 0.2},
                  "source": {"sampling_priority": "cold_start", "gating": "cold_start"}}
}
// gate_decision 行（同 case，generate_review_tasks 时记）
{
  "pipeline_stage": "gate_decision",
  "process_signals": {"interaction_coverage": false, …,
    "priority": 0.4, "components": {"w1": null, "w2": null, "w3": 0.4, "w4": 0.25},
    "availability": {"w1": false, "w2": false, "w3": true, "w4": true},
    "w3_available": true, "gating_tier": "forced"},
  "rules_triggered": [],
  "gate_decision": {"tier": "forced", "review_task_generated": true,
                    "review_task_id": "rt-…", "sample_group": "forced"},
  "human_outcome": {"decision": "accepted", "notes": "trace-back example", "decided_at": "…Z"},
  "review_task_id": "rt-…"
}
```

回溯路径（只读 decision_traces 两行）：inputs（input_refs → MaskVersion/QCReport/学生 ModelVersion）→ 过程信号（coverage=false + M6-b skipped 原文 + M6-c 分量/可用性）→ 触发规则（rules_triggered=[] 与 flags 一致）→ 门禁档位（forced + 任务 rt-…）→ 人工结果（accepted，回流补填）。

## 5. 验收

**a. trace-back 全链**：`test_acceptance_a_trace_back_full_chain`（合成 case-wired，决策回流端点补填后从 DecisionTrace 行单独回溯五段全链）+ `test_real_holdout_trace_back_30_cases`（真实 hold-out 30 例现算，见 §6）。

**b. rules_triggered 与 QCReport.flags 逐项一致**：三层保证——
1. 构造同源：`record_qc_trace(flags=result.flags)` / `record_gate_trace(flags=落库 QCReport.flags)`——同一变量/同一列；
2. 对比测试：`test_acceptance_b_rules_triggered_matches_flags`（qc_series 全流程后逐 mask 断言 `list(trace.rules_triggered) == list(QCReport.flags)`，并断言同 run 返回 flags 三端一致）；
3. 接线测试：`test_generate_review_tasks_writes_gate_traces` 对 3 个 series 的 gate trace 逐一与落库 flags 比对。

## 6. 真实 hold-out 验收（30 例现算）

`test_real_holdout_trace_back_30_cases`：真实 w3（教师+学生 mask 现算逐类 Dice）+ 真实 w4（117 类低频名单）+ w1/w2 置 0（hold-out 无真实交互轨迹，**synthetic 如实标注**——无过程信号不造假）。`sample_review_tasks_by_priority` 排名分档 → `record_gate_trace` 落成 30 条 gate_decision trace → 从 trace 单独回溯任选 case（priority 最高者从 ranked 指认，不 hardcode id）+ 三档覆盖断言（30 例 20/60/20 → 6/18/6，不 hardcode 档位分配）。

实测（2026-11-12，951s 单跑 1 passed）：

```
M6-F 真实 hold-out trace: {
 "top_case": {"case_id": "amos_0004", "tier": "forced", "priority": 1.0667,
   "components": {"w1": null, "w2": null, "w3": 1.0, "w4": 0.0667},
   "availability": {"w1": false, "w2": false, "w3": true, "w4": true}},
 "n_tiers": {"forced": 6, "suggested": 18, "auto": 6}}
```

amos_0004（M6-c 排序验收同一 case，prostate 整类分歧逐类 min 0.0）rank 1 → forced；w1/w2 null（无交互轨迹，availability=False 如实记录）；w3=1.0（最差类聚合把整类漏检顶到 1.0）；w4=0.0667（参评类 15 类中 1 类低频）；三档 6/18/6 与 M6-D-REPORT 三档验收一致。s0209 同样在 forced 区（M6-c 排序验收 rank 6）。amos_0014 共同盲区 case 在本实现中仍落 auto（教师/学生 prostate 双空 → 逐类比较无该类 → w3 无该类信号）——与 M6-d 如实记录的诚实边界一致，由 M4 结果规则/QC flag 覆盖，trace 不掩盖（coverage/components 如实为空）。

**M7-S2 改标（2026-11-14）**：本节 trace-back 基于 30 例 hold-out（gate 集）现算，该批 case 同时承担 w3/w4 定义校准与 priority 排序验证——**以上分档比例（6/18/6）与排名为「样本内开发指标」（in-sample dev），不得冒充独立验收**。派生统计来源哈希见 config/qc_thresholds.yaml note 与 M7-S2-REPORT，独立 calib 集建好后须复核。原数字与原结论不动。

## 7. 测试与结果

`tests/test_m6f_trace.py`（16 条，deep-reviewer 修复轮后）：

| 组 | 测试 |
|---|---|
| schema（3） | snapshot_calibration 缺省/覆盖；qc trace 形状（stage/rules=flags/human_outcome null/calibration）；gate trace 形状（tier+任务+priority 信号） |
| 降级（3） | 无 interactionMetrics（coverage=False + skipped 原文）；case_id 未给 null；tier=auto 不建任务 |
| update（1） | human_outcome 补填 + 幂等不覆盖 |
| 接线（7） | qc_series 同一事务记 qc_scoring trace；generate_review_tasks 全量 ranked 记 gate trace（auto 在内 + tier 一致 + 验收 b）；旧 70/30 路径 tier=group + ReviewTask id 带入；决策回流端点补填 + 回放不覆盖；旧路径人工决策补填；重跑累积多 trace 全补填 |
| 验收（2） | b：rules_triggered vs flags 逐项；a：case-wired 全链回溯 |
| 真实（1） | 30 例 hold-out trace-back（951s 单跑） |

pytest：**16 passed**（test_m6f_trace.py 除真实 hold-out 例 15 passed ~4s；真实例 951s 单跑 1 passed）。全套结果见 §10 收尾数据。m5e 的 alembic head 断言更新为 a4f9e2c81d03（M6-f 链尾）。

### 全套收尾（分批，每文件至少一次；单次全量 run 超环境后台时限 kill，无假合计）

| 批次（文件） | 结果 |
|---|---|
| test_qc/test_db/test_storage_config/test_qc_api/test_qc_interaction | 94 passed 3.5s |
| test_m6f_trace/test_student_pred/test_sqlite_jobs_queue/test_zip_bomb/test_zip_streaming/test_ts_runner/test_tui_smoke | 58 passed 571.7s（trace 真实例 951s 单跑 1 passed 另计） |
| test_review_api/test_review_session/test_ontology/test_ontology_nl/test_license_whitelist/test_m5e/test_agent_context_phi | 127 passed 5.1s |
| test_m6e_wiring | 4 passed |
| test_holdout/test_evaluate_holdout/test_evaluate_student_holdout/test_gpu_lock/test_gpu_probe/test_segmentation/test_source_instances/test_selector_guard/test_interaction_metrics | 125 passed 97.3s |
| test_distill_license_gate | 34 passed |
| test_m6c_priority | 15 passed |
| test_m6d_gating | 13 passed |
| test_api/test_api_ingest/test_api_ontology/test_ingest/test_deid_usage/test_export_seg/test_nifti_geom_strict/test_ocr_precision/test_burned_ocr | 92 passed 12.0s |
| test_qc_interaction_e2e | 7 passed 55.3s |
| test_nninteractive_backend | 30 passed 147.4s |

分批合计 **599 passed / 0 failed / 0 error**（45 文件每文件至少一次；batched——分批计数如实列上表，不并入单次 run 假合计）。ruff（src tests scripts eval alembic config）：**All checks passed**。

## 8. 降级语义（不造假）

- 无 interactionMetrics（纯自动 case）→ `interaction_coverage=False`，M6-b 信号记 skipped 原文，w1/w2=None（availability=False）。
- multi_teacher skipped / 无学生 mask → w3=None + `w3_available=False` + plan note 进 trace。
- 旧 70/30 路径 → tier=group（flagged/clean），无 priority 分量冒充。
- human_outcome 恒 null 直至人工决策；补填幂等不覆盖。
- student prediction 缺失 → input_refs.student_source="unavailable: …"（原文，不清洗）。

## 9. Deep-reviewer closing review（三区域并行，对抗式）

按 `.claude/agents/deep-reviewer.md` 三约束：并行 3 个 deep-reviewer 子代理按区域深审（本体 models/qc_trace/迁移、接线 qc_pipeline/app、测试与红线），每条报回 issue 二次核实（本机实跑复现），findings 逐条呈现。审查基线 c227fb0..696c14c。

**Blocker（1 件，实跑复现，失败测试先行已修）**

1. **决策回流测试改写真实 `config/qc_thresholds.yaml`（版本控制内阈值文件）。**
   - 文件:行：tests/test_m6f_trace.py:515（修复前）/ app.py:616 / qc_reflux.py:37,53。
   - 证据：回流端点 `record_review_decision(settings, decision)` 未传 path → 落到模块级 `QC_THRESHOLDS_PATH` → `yaml.safe_dump` 写盘；跑 `test_decision_endpoint_backfills_human_outcome` 后 `config/qc_thresholds.yaml` 的 `calibrated_on` 20→22（本机 sha256 前后实测变化）。
   - 复现：干净树记录 `sha256sum config/qc_thresholds.yaml` → 跑该测试 → sha256 变化、`decisions` 计数增加。
   - 修复：两个用回流端点的测试（endpoint 补填 + acceptance a）monkeypatch `qc_reflux.QC_THRESHOLDS_PATH` 到临时副本，并断言测试后真配置 == 备份（零改动）、临时副本字节有变化（回流计数进的是副本）。提交 62e9e82。

**Warning（7 件；4 件已修，3 件定性/文档）**

2. **旧 70/30 路径的 gate trace 写 `rt_id=None`（实跑复现，失败测试先行已修）。** flagged/clean 组确实经 `_ensure_review_task` 建 ReviewTask，但旧实现 trace 写 `review_task_generated=False`、无 id——决策回流按 `review_task_id` 锚定找不到 trace，人工决策后 `human_outcome` 永久 null，违背「null=尚无人工决策」语义。复现：`sampling_priority.enabled=false` 跑 generate_review_tasks → 决策端点 → trace.human_outcome 仍 null。修复：两条路径统一从 `created` 回查 rt_id/group；`test_old_path_decision_backfills_human_outcome` 失败测试先行。提交 62e9e82。
3. **重跑累积多条 gate trace 只补 timestamp 最新一条（实跑复现，失败测试先行已修）。** 每次 generate_review_tasks 对全量 ranked 各 add 一条 trace，重跑同一 review_task_id 下累积多条；旧实现只补最新一条，更早的行永久 null，M7 校准会误读为「无决策」。复现：连续两轮 generate_review_tasks → 决策端点 → `[None, {'decision': ...}]`。修复：回流端点补填同 review_task_id 下全部 gate trace；`test_repeated_gate_runs_backfill_all_traces` 失败测试先行（修复前该测试红、修复后绿）。提交 62e9e82。
4. **迁移漏 `ix_decision_traces_review_task_id` 索引（读码核实，已修）。** models.py `review_task_id` 有 `index=True`（生成 `ix_decision_traces_review_task_id`），迁移只建了三个索引——迁移建库与 create_all 建库索引漂移，回流查询正按该列过滤。修复：迁移补 create_index + downgrade 对应 drop。提交 bebabe1。
5. **human_outcome=None 存为 JSON 字面量 'null' 而非 SQL NULL（读码核实，定性不修）。** SQLAlchemy JSON 列默认 `none_as_null=False`，Python 层读回是 None（代码路径正常），但 M7 若用 SQL `WHERE human_outcome IS NULL` 统计会漏行。定性：保留现状（ORM 查询路径 `trace.human_outcome is None` 判断），M7 Leftover 记明查询口径。
6. **重放 rejected 时 ReviewTask=rejected / trace.human_outcome=accepted 并存（读码核实，定性为设计语义）。** ReviewTask.status 记最终决策、trace.human_outcome 记首次决策（幂等不覆盖=决策历史保留）——测试补断言重放后 ReviewTask 状态与 trace 值，锁定语义（acceptance a/endpoint 测试断言 trace 不改写）。
7. **`decided_at` 自引用断言判别力弱（读码核实，定性）。** `"decided_at": gate_t.human_outcome["decided_at"]` 只证键存在。定性：decided_at 格式由 `update_human_outcome` 单点产生（UTC ISO 字符串），M7 校准不消费该字段；不补正则断言。
8. **重跑 auto 档 trace 的 `review_task_generated=False` 陈旧性（读码核实，定性为本次运行语义）。** 某 mv 之前建过任务、本次分档 auto → 本次 trace 写 False。「本次运行」语义下成立；既有任务由其建档时的 trace 锚定回流。

**Nit（4 件；1 件已修，3 件定性）**

9. **series_id[:60] 截断（已修，e6a8868）。** 列宽 String(64) 足够，截断使 trace 与 Series 表关联静默失配。修复：`record_qc_trace`/`record_gate_trace` 不再截断——trace 列 String(64) 存完整值（与 qc_pipeline 既有 ReviewTask.series_id 列宽 String(60) 纪律区分：那是 ReviewTask 自己的列宽限制，trace 列更宽）。
10. **YAML 显式 null 使快照抛 TypeError（读码核实，定性不修）。** `float(None)` 会回滚 qc_scoring 事务——与 qc_priority `_load_priority_cfg` 同样的 config 异常行为（fail-loud 语义一致），不是 trace 引入的新类别。
11. **分档断言可能空转 / 单 series 可能静默 skip（读码核实，定性）。** 接线测试的 tier 循环在空列表时不断言——但 `assert len(gate)==3` 已锚定行数；单 series skip 场景本机实测未触发（case-wired 落 forced，15 passed 无 skip）。
12. **`plan.ranked`/`component_availability` 的 type: ignore（读码核实，定性不修）。** SamplingPlan 新增属性是 M6-c 既有模式（`plan.ranked = rows  # type: ignore[attr-defined]`），补类型声明超出 M6-f 范围（不碰冻结的 qc_priority 逻辑）。

**已核实无问题**：qc_scoring 同事务（run_qc 异常在 Session 打开前，无孤立 trace）；重跑幂等 created append 语义正确；窗口子查询 flags 传递正确；锁纪律（三处写路径均持 db.write_lock）；`_table_exists` 幂等；降级语义不造假；human_outcome 无伪造路径；TestClient lifespan 指向临时库；红线（nnInteractive/distill_data/holdout.txt/gate_status/hard_flag_rules/M6-a–d 规则本体）零触碰。

## 10. 提交

| 提交 | 内容 |
|---|---|
| d9c09ea | feat(m6f): DecisionTrace 实体 + trace 记录接线（qc_scoring/gate_decision 两阶段） |
| 696c14c | test(m6f): DecisionTrace 单测 14 条 |
| 62e9e82 | fix(m6f): deep-reviewer 修正——Blocker 阈值文件污染 + 旧路径 rt_id + 多 trace 全补填 |
| e6a8868 | fix(m6f): deep-reviewer W9 修正——series_id 截断删除 |
| 759411f | fix(m6f): 回滚 config/qc_thresholds.yaml 到基线——696c14c 误提交 pre-fix 测试污染的 reflux 计数器 |
| 58cebce | docs: M6-F-REPORT——DecisionTrace 形态/schema/接线/trace 示例/验收/分批收尾/deep-reviewer findings |
| bebabe1 | fix(m6f): 迁移 a4f9e2c81d03 补 review_task_id 索引——deep-review W4 真正落地 |

（docs 提交见 git log。）

## 11. Leftovers（M7 输入）

1. **M6-c 遗留（w1/w2 真实回归）**：interactionMetrics 采集始于 M6-a（2026-11-12），生产 case 尚无真实交互轨迹——trace.process_signals 的 w1/w2 availability 分布是 M7 回归的样本底账。
2. **M6-d 遗留（比例/权重重算）**：M7 决策回流标签 ≥20 例后重算 gating 三比例 + priority 权重；trace.calibration 提供重算前参数快照，`review_task_id` 列可直接 join ReviewTask.decision 取标签。
3. **human_outcome 回流**：M4 报告决策 0/20，本轮回流端点补填已接通（测试实证），生产标签积累是运营侧事项。
4. **学习型门控器**（逻辑回归/小型 GBDT）：M7、标签 ≥20 例后；trace 的 rules_triggered/组件值即训练特征底账。
5. **pipeline_stage 粒度**：student inference / multi-teacher 阶段不另记行（§2 说明其输出已入 input_refs/process_signals）；若 M7 需要推理级粒度（如逐步草稿 delta 回放），由 nninteractive 侧 provenance 承接，不在 DecisionTrace。

## 12. 附录：M7-S2 补记（2026-11-14）

- **学生门禁 0.8014 / `gate_status` passed 的来源与口径**（M4-REPORT §1.6 `27387c8`）：学生只训了 15 类（DISTILL_CLASSES，AMOS 15 类同源），门禁分口径评——**in-scope**（`gate_scope="student-15-classes"`）mean **0.8014**、passed True、failures 空、整类漏检空；naive 117 类对照（mean 0.1027、passed False，63 条每类下限失败）保留在 summary["scope_naive"] 留作口径对照，不删。首轮按 117 类评得 0.1027 FAIL 已落库是**口径错误**（102 个超范围类天然 dice=0，完美学生也永远 FAIL）；DB 已从 failed/0.1027 纠正为 passed/0.8014（读回核对），旧值在 M4-REPORT 与提交信息里写明，不掩盖。验收语料 = eval/holdout.txt 30 例 gate 集——**M7-S2 改标：0.8014 是样本内开发指标**（该批 case 同时用于蒸馏名单排除与门禁验收；derived_from_case_ids_hash = gate 30 例哈希，见 config/qc_thresholds.yaml note 与 M7-S2-REPORT）。
- **三集分离改标**：M6-C/D/F 基于 30 例的门控比例已在 §6（本文件）与 M6-C/D-REPORT 相应节改标「样本内开发指标」；calib 集现状 pending/空，独立 calib 建好后须复核。
