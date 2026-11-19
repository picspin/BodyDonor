# 影子走查落库检查清单（Shadow Walkthrough Checklist）

定位：走查当天逐项打勾用。所有命令/SQL 已在本轮对 worktree 根
`bodydonor.db` 副本（schema 已升级至 alembic head `f8a3b9c2d1e4`）实测跑通；
对现有库只读查询（`mode=ro` URI）。**绝不写 `~/data/BodyDonor/bodydonor.db`**
（生产/协调方共享，只读不写）。

统一只读打开方式（每条 SQL 前置，省略不重复写）：

```bash
~/work/BodyDonor/.venv/bin/python -c "
import sqlite3
con = sqlite3.connect('file:$PWD/bodydonor.db?mode=ro', uri=True)
cur = con.cursor()
# <SQL>
"
```

（在 worktree 根 `~/work/BodyDonor-shadow` 下执行；下文只列 `cur.execute(...)` 的 SQL。）

## C1. human_outcome 补填——两行齐全

先读代码确定的补填点（亲读 src/bodydonor/qc_trace.py + src/bodydonor/app.py:563）：

- 补填只发生在**决策回流端点** `POST /qc/review-tasks/{review_task_id}/decision`
  （app.py:563）内：它按 `review_task_id` + `pipeline_stage='gate_decision'`
  查出该任务下**全部** DecisionTrace 行（app.py:563 实现里 `traces = s.execute(
  select(DecisionTrace).where(DecisionTrace.review_task_id == rt.review_task_id,
  DecisionTrace.pipeline_stage == 'gate_decision'))`），对每行调
  `update_human_outcome`（qc_trace.py:234，幂等不覆盖）。
- `pipeline_stage='qc_scoring'` 的行**永远不补填**（record_qc_trace
  qc_trace.py:139 里 human_outcome=None，docstring：gate_decision 阶段另记一条）。

所以「两行齐全」= 对每个走了决策回流的 review_task_id：

1. **gate_decision 行**：human_outcome 非空，含 `decision`/`decided_at`（有
   notes 时还有 `notes`）。
2. **qc_scoring 行**：human_outcome 为 null（设计语义，不是遗漏——不造假）。

走查后核对 SQL（实测跑通；现库 0 行 trace，走查后 ≥ 每例 2 行）：

```sql
-- 每例两行齐全：qc_scoring 1 行（human_outcome 应为 null）+ gate_decision ≥1 行（应非 null）
SELECT t.case_id, t.series_id, t.pipeline_stage, t.review_task_id,
       json_extract(t.human_outcome, '$.decision') AS decision,
       json_extract(t.human_outcome, '$.decided_at') AS decided_at
FROM decision_traces t ORDER BY t.case_id, t.pipeline_stage;
```

判定（打勾条件）：

- [ ] 每例 `gate_decision` 行 decision 与医生判定一致（accepted/corrected/rejected）。
- [ ] 每例 `qc_scoring` 行 human_outcome 为 null（恒 null，非遗漏）。
- [ ] `decided_at` 落在会话索引 `recording_start`/`recording_end` 窗口内
      （recording.md 对应关系第 1 条）。
- [ ] 决策回流后 `config/qc_thresholds.yaml` 的 `calibrated_on` 计数 +1/例
      （qc_reflux.py record_review_decision；走查当天 diff config 核对）。

## C2. 日志无 ERROR

本仓库无独立日志文件（亲查：`ls ~/data/BodyDonor/*.log` 无命中；FastAPI
日志走 uvicorn stdout，历轮测试日志在 `~/data/BodyDonor/tmp/` 下）。走查启动
命令重定向到固定文件（剧本文末环境前提）：

```bash
nohup uvicorn bodydonor.app:app --host 0.0.0.0 > ~/data/BodyDonor/tmp/walkthrough-uvicorn.log 2>&1 &
```

查法（grep 模式已在本轮测试日志 `~/data/BodyDonor/tmp/m7-s3r2-fulltest.log`
实测，命中 0 = 通过）：

```bash
grep -cE "ERROR|Traceback|Exception" ~/data/BodyDonor/tmp/walkthrough-uvicorn.log
```

- [ ] 输出 0（无 ERROR/Traceback/Exception）。
- [ ] 补充：409/422 等预期错误（GPU 忙、无 mask）不算失败，但要在走查记录
      里逐条写明原因（对抗性记录，不藏失败）。

## C3. MaskVersion ↔ ReviewTask 对应关系

关联键（亲读 models.py 表定义 + 实测双键 join）：

- `review_tasks.qc_report_id` → `qc_reports.qc_report_id`
- `qc_reports.mask_version_id` → `mask_versions.mask_version_id`
- 一致性双键：`review_tasks.series_id` 与 `mask_versions.series_id` 应相等
  （本条 SQL 实测跑通：现有库 0 mismatch）。

```sql
-- 关联链核对：每个 ReviewTask 能追到唯一 MaskVersion + 版本号/来源
SELECT rt.review_task_id, rt.series_id, rt.status, rt.decision,
       q.qc_report_id, q.passed, q.overall_score,
       m.mask_version_id, m.version, m.source
FROM review_tasks rt
JOIN qc_reports q   ON rt.qc_report_id = q.qc_report_id
JOIN mask_versions m ON q.mask_version_id = m.mask_version_id;
```

```sql
-- 双键一致性（应 0 行 = series_id 全一致）
SELECT rt.review_task_id
FROM review_tasks rt
JOIN qc_reports q   ON rt.qc_report_id = q.qc_report_id
JOIN mask_versions m ON q.mask_version_id = m.mask_version_id
WHERE rt.series_id != m.series_id;
```

```sql
-- mask_version 走查后应递增：nnInteractive 提交（Case C）产生新行 source=nninteractive_guided
SELECT series_id, version, source, created_at FROM mask_versions
ORDER BY series_id, version;
```

- [ ] 每个 ReviewTask 追到唯一 MaskVersion（join 1 行/任务）。
- [ ] 双键 mismatch 查询 0 行。
- [ ] Case C 走查后 `mask_versions` 有新行，source=`nninteractive_guided`，
      version = 基线 teacher 版本 +1（MaskVersion.series_id 下递增）。
- [ ] gate_decision trace 的 `mask_version_id` 与上面 join 到的一致
      （trace.input_refs.qc_report_id ↔ 同 QCReport）。

trace 与 QCReport.flags 一致性（rules_triggered 逐项一致，models.py docstring
验收 b 语义；join 已实测跑通）：

```sql
SELECT t.trace_id, t.pipeline_stage, t.rules_triggered, q.flags
FROM decision_traces t
JOIN qc_reports q ON json_extract(t.input_refs, '$.qc_report_id') = q.qc_report_id;
```

- [ ] qc_scoring 行 rules_triggered == 同 QCReport.flags（逐项）。

## C4. 每例通过/不通过判定标准（具体条件）

判定依据（config/qc_thresholds.yaml 亲读 + load_qc_thresholds 实测）：

- **单例 QC 通过** = `qc_reports.passed = 1`。条件（qc_thresholds.yaml
  overall 节，实测加载：`hard_flag_rules: ['volume_prior']`，
  `min_score: 0.5`）：
  - `overall_score >= 0.5`（低于 = passed=false，低质量不进蒸馏）；
  - `volume_prior` 未 flagged（一票否决）；
  - flags 数量越多 overall_score 线性越低（0 flag → 1.0）。
- **走查流程通过**（每例打勾）：
  - [ ] 导入选中（选择器规则不排除；层数 ≥30 防线不触发——本轮四例 nibabel
        实测 78/93/278/419 层）。
  - [ ] teacher 分割成功，MaskVersion(source=teacher) 落库。
  - [ ] QCReport 落库 + qc_scoring trace 落库（每例）。
  - [ ] 决策回流成功（gate_decision trace 补填，C1 判定全过）。
  - [ ] Case C 追加：nnInteractive 提交产生 nninteractive_guided MaskVersion
        + sidecar `nninteractive-correction/correction_source.json` 存在：
        `ls ~/data/BodyDonor/storage/<case_id>/<series_id>/nninteractive-correction/`。
- **走查流程不通过**（任一命中即记不通过 + 原因，不静默重试）：
  - 端点 409/422/500 且非预期（预期：GPU 忙 409、无 mask 422）。
  - C1/C3 任一判定不满足。
  - 日志 grep 命中非预期 ERROR（C2）。
- [ ] 每例在走查记录里写明：通过 or 不通过 + 判定条件引用（不写「全部就绪」，
      不通过如实记）。

## 收尾（全清单跑完）

- [ ] 本清单 C1–C4 全打勾 or 不通过项已逐条写明原因。
- [ ] 走查记录（含 SQL 输出粘贴）归档 `~/data/BodyDonor/walkthrough-recordings/`。
