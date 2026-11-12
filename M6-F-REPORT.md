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

实测（2026-11-12）：

```
M6-F 真实 hold-out trace: {
 "top_case": {"case_id": "s0209", "tier": "forced", "priority": 0.4,
   "components": {"w1": null, "w2": null, "w3": 0.4, "w4": 0.0},
   "availability": {"w1": false, "w2": false, "w3": true, "w4": true}},
 "n_tiers": {"forced": 6, "suggested": 18, "auto": 6}}
```

s0209（M6-c 排序验收同一 case）rank 1 → forced；w1/w2 null（无交互轨迹，availability=False 如实记录）；w4=0.0（参评类无低频类）；三档 6/18/6 与 M6-D-REPORT 三档验收一致。amos_0014 共同盲区 case 在本实现中仍落 auto（教师/学生 prostate 双空 → 逐类比较无该类 → w3 无该类信号）——与 M6-d 如实记录的诚实边界一致，由 M4 结果规则/QC flag 覆盖，trace 不掩盖（coverage/components 如实为空）。

## 7. 测试与结果

`tests/test_m6f_trace.py`（14 条）：

| 组 | 测试 |
|---|---|
| schema（3） | snapshot_calibration 缺省/覆盖；qc trace 形状（stage/rules=flags/human_outcome null/calibration）；gate trace 形状（tier+任务+priority 信号） |
| 降级（3） | 无 interactionMetrics（coverage=False + skipped 原文）；case_id 未给 null；tier=auto 不建任务 |
| update（1） | human_outcome 补填 + 幂等不覆盖 |
| 接线（5） | qc_series 同一事务记 qc_scoring trace；generate_review_tasks 全量 ranked 记 gate trace（auto 在内 + tier 一致 + 验收 b）；旧 70/30 路径 tier=group；决策回流端点补填 + 回放不覆盖 |
| 验收（2） | b：rules_triggered vs flags 逐项；a：case-wired 全链回溯 |
| 真实（1） | 30 例 hold-out trace-back |

pytest：**14 passed**（test_m6f_trace.py 单跑；真实 hold-out 例在 30 例教师+学生 mask 现算上实测通过）。全套回归另报（见 §9 提交记录后的收尾数据）。

## 8. 降级语义（不造假）

- 无 interactionMetrics（纯自动 case）→ `interaction_coverage=False`，M6-b 信号记 skipped 原文，w1/w2=None（availability=False）。
- multi_teacher skipped / 无学生 mask → w3=None + `w3_available=False` + plan note 进 trace。
- 旧 70/30 路径 → tier=group（flagged/clean），无 priority 分量冒充。
- human_outcome 恒 null 直至人工决策；补填幂等不覆盖。
- student prediction 缺失 → input_refs.student_source="unavailable: …"（原文，不清洗）。

## 9. 提交

| 提交 | 内容 |
|---|---|
| d9c09ea | feat(m6f): DecisionTrace 实体 + trace 记录接线（qc_scoring/gate_decision 两阶段） |
| 696c14c | test(m6f): DecisionTrace 单测 14 条 |

（后续 fix(m6f) / docs 提交见 git log。）

## 10. Leftovers（M7 输入）

1. **M6-c 遗留（w1/w2 真实回归）**：interactionMetrics 采集始于 M6-a（2026-11-12），生产 case 尚无真实交互轨迹——trace.process_signals 的 w1/w2 availability 分布是 M7 回归的样本底账。
2. **M6-d 遗留（比例/权重重算）**：M7 决策回流标签 ≥20 例后重算 gating 三比例 + priority 权重；trace.calibration 提供重算前参数快照，`review_task_id` 列可直接 join ReviewTask.decision 取标签。
3. **human_outcome 回流**：M4 报告决策 0/20，本轮回流端点补填已接通（测试实证），生产标签积累是运营侧事项。
4. **学习型门控器**（逻辑回归/小型 GBDT）：M7、标签 ≥20 例后；trace 的 rules_triggered/组件值即训练特征底账。
5. **pipeline_stage 粒度**：student inference / multi-teacher 阶段不另记行（§2 说明其输出已入 input_refs/process_signals）；若 M7 需要推理级粒度（如逐步草稿 delta 回放），由 nninteractive 侧 provenance 承接，不在 DecisionTrace。
