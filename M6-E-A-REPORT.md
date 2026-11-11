# M6-E-A-REPORT — BodyDonor M6-e/a 交付报告

> 日期：2026-11-11。M6 计划见 ~/M6-PLAN.md（v2，2026-11-11 用户评审修订）。
> 本文件首节为 M6-e（生产 QC 接入学生 mask，关闭 M4 §3.3 open deviation），
> 由 M6-e 实施方撰写；M6-a（轨迹结构化）由 M6-a 实施方在同一文件追加。
> 非交互运行；全程未 push、未建 PR、未改 eval/holdout.txt、未改 M4 门禁
> 结果（ModelVersion gate_status 保持 passed）。

---

## 1. M6-e：生产 QC 接入学生 mask

### 1.1 任务与范围

M4 §3.3 承认的 open deviation：`rule_multi_teacher_consistency` 已启用
（threshold 0.80，均值聚合），但 qc_pipeline 调用点无学生 mask 可用，生产
路径上该规则永远 skipped。M6-e 的任务（~/M6-PLAN.md M6-e 节）：

- 在 qc_series 输入参数增加学生 mask 的可选路径（M4 已实现 run_qc 参数，
  缺的是调用点接线）
- 处理学生预测不存在的 case（显式 skipped，M4 已定语义不变）
- 用 M4 的 419 对一致性分布作回归基线，确保接入后 flag 行为与离线评估一致

第 1 步（前轮已合入）：`src/bodydonor/student_pred.py` 的
`load_student_prediction` 可复用读取入口——给定 case id，从当前已通过门禁
的学生 ModelVersion（gate_status=passed，最新 created_at）解析 weights_key
→ dataset.json 类别表 → 读同 case 预测 mask；缺失显式 raise
StudentPredictionError（不返回假数据）。

### 1.2 series→case 映射核实结论（关键设计点）

**核实方式**：直接查生产库 bodydonor.db（cases/series/mask_versions 表）
+ 数据目录比对 + 体素级验证，2026-11-11 实测。

结论分三层：

1. **series_id 字符串层面：交集为 0（第 1 步结论成立）**。生产 series id
   形如 `1.2.826.0.1.3680043.8.498.*` / `nifti-*`，hold-out case id 形如
   `s0045` / `amos_0001`，两者无任何字符串映射。Series 表本身没有
   case→holdout 的查询键。
2. **case_id 外键层面：存在真实映射但覆盖极小**。Series 表有 case_id 外键
   （series.case_id → cases.case_id）；生产库里 4 个 case 中 2 个的 case_id
   与 hold-out case id **同名**：`nifti-b63b1d93` → `amos_0004`、
   `nifti-3fd14fa6` → `amos_0001`。体素级验证：DB `amos_0004` series 的
   image/mask/学生预测 shape 完全一致（512×512×78）——同一底层 image。
3. **教师 mask 来源不同，Dice 不可直接复用**：DB `amos_0004` 的 teacher
   mask 是上传时导入的 TS 推理产物（117 类，TS 编号 16–28 可见），而 M4
   离线评估用的是 `downloads/amos22_subset/amos_0004/ts_out` 逐类文件。
   两者在 aorta 上体素数相同（14468）但逐体素 Dice 仅 0.0297——affine
   错位（storage 副本 image 方向为 +x，AMOS 原图为 −x）。即：同名 case
   的 DB 教师 mask 与 hold-out 评估教师 mask 不是同一坐标系产物。

**接线设计**：`qc_series` 接受可选 `case_id` 参数——**有则查、无则
skipped，不硬造映射**。调用方（app 端点等）知道 case 归属时显式传入；
不传时保持 M4 skipped 语义。理由：(a) 生产绝大多数 case 与 hold-out 无
映射，自动"从 series_id 猜 case_id"必然落到缺失信号；(b) 同名 case 的
Dice 因坐标系差异不可靠，映射决策应显式而非隐式。

### 1.3 实现

- **`qc_pipeline.qc_series`**（74d2079）：
  - 新增可选参数 `case_id: str | None = None`
  - `case_id` 给出且 multi_teacher_consistency enabled 时，经
    `load_student_prediction(case_id, settings)` 读学生 mask，传给 run_qc
    ——规则在生产路径真跑
  - 学生预测缺失（StudentPredictionError，生产 case 一律走此信号——预测
    产物只覆盖 hold-out 30 例）→ 显式 skipped
    `{"skipped": "no student mask"}`，不进 flags、不计入 enabled 分母
    （M4 已定语义不变），不 raise、不造假
  - 返回值新增 `student_source` 字段（case 级血缘，不落库）：
    `no case_id` / `unavailable: <原因>` / `<ModelVersion id>`
- run_qc 本体不动（M4 阶段 5 已实现 student_mask_arr/student_classes
  参数与 skipped 语义），只做调用点接线。

### 1.4 实测数字

**接线行为验证**（生产库副本 + 最小 thresholds，只开 multi_teacher）：

- 无 case_id → `skipped="no student mask"`，student_source=`no case_id`
- 生产 case_id（`amos_0004`，有 hold-out 预测文件）→ 规则真跑，
  student_source=`student-nnunet-v2-distill-20261109T062920Z`

**419 对回归**（30 例 hold-out，真实教师 = ts_out 拆分文件按 ts_class_map
拼整图（只拼 Dataset501 15 类）+ 真实学生 mask = load_student_prediction，
run_qc 均值聚合 threshold 0.80）：

- **flag 5/30，与 M4 离线评估（M4-REPORT §3.4）flag 的 5 例完全一致**：
  s0209 0.1181、amos_0005 0.6592、s0338 0.7081、amos_0006 0.7567、
  amos_0016 0.7862——逐例 case 级 mean Dice 与离线评估**逐位吻合**
  （差异记录：无；30/30 例均真跑，无一 skipped，无一越 ±1 边界）
- 30 例 case 级 mean Dice 均值 0.8457，419 对离线总体 mean 0.856（偏差
  0.0103 < 0.02；来源：case 级均值 vs pair 级均值的口径差 + s0209 单例
  极端值权重不同，非实现差异）
- 逐类参与比较类数 n：12–15（学生范围外教师类按 M4 语义跳过，记
  skipped_teacher_only_classes，不算不一致）
- s0209 只作回归 case 记录（aorta 0.17 → 本轮 case 级 mean 0.1181
  flag，与 M4 §3.6 结构性失败结论一致），未用过程信号或 QC 规则"修"它

### 1.5 单测（tests/test_m6e_wiring.py，4 项）

1. `test_qc_series_without_case_id_skips_multi_teacher`：无 case_id →
   `rule_scores[multi_teacher_consistency] == {"skipped": "no student
   mask"}`、不进 flags、student_source=`no case_id`
2. `test_qc_series_with_missing_prediction_skips`：case_id 给出但学生预测
   缺失 → 显式 skipped，不 raise、student_source 以 `unavailable:` 开头
3. `test_qc_series_with_student_mask_runs_rule`：注入学生预测 → 规则真跑
   （mean_dice/n_compared_classes 断言、student_source=注入的
   ModelVersion id）
4. `test_regression_419_pairs_flag_baseline`：30 例 hold-out 真跑，flag
   集合与 M4 离线 5 例对比（±1 例边界）、总体 mean 与 0.856 偏差 < 0.02

### 1.6 提交 hashes（本地 main，未推）

| 提交 | 内容 |
|---|---|
| `74d2079` | feat(m6e): qc_series 学生 mask 接线 + 419 对回归测试（qc_pipeline 接线 + tests/test_m6e_wiring.py 4 项） |

第 1 步（前轮）：`af2e5b4`（load_student_prediction 入口）、`26aef88`
（入口单测 5 项）。

### 1.7 pytest / ruff

- pytest：全套见 §1.8 收尾自查；本文件提交点实测 test_m6e_wiring.py
  4 项全绿（3 接线单测 + 1 回归，回归含 30 例真实 mask 运行）
- ruff check src tests：全绿；ruff format：qc_pipeline.py /
  test_m6e_wiring.py 已格式化

### 1.8 收尾自查

- 全套 pytest：见 §1.9 最终验证数字
- ruff check src tests scripts eval alembic config：全绿
- /code-review 自查：headless 下斜杠命令不可用，改为逐项自查（结论：
  接线只加可选参数、默认行为不变（case_id=None → skipped 与接线前一致）；
  学生 mask 只读不写、不进蒸馏训练集；run_qc 分母语义未动——skipped 规则
  不进 flags、不计入 enabled 分母；无 mock 数字——回归用真实 mask 产物）
- eval/holdout.txt 与 M4 门禁结果：未动（ModelVersion gate_status 保持
  passed）

### 1.9 最终验证数字

- 全套 pytest：**476 passed**（472 + 本轮 4 项：3 接线单测 + 1 回归），
  2026-11-11 实测
- ruff check src tests scripts eval alembic config：全绿

### 1.10 遗留

1. **app 端点未传 case_id**（保持接线前行为）：`POST /qc/series/{series_id}`
   仍不传 case_id（生产 series 与 hold-out 无映射，传了也是 skipped）；
   M6-c 的 w3 项（teacher_student_disagreement）消费时再决定 case 归属的
   传递方式（DecisionTrace / cases 表查询）。
2. **同名 case 的坐标系差异**（§1.2 结论 3）：DB `amos_0004` 教师 mask 与
   hold-out ts_out 教师 mask affine 错位（aorta Dice 0.0297）。若后续在
   生产路径对同名 case 真跑一致性，需先统一坐标系（resample 或重导），
   否则 Dice 不可比。本轮未处理（接线语义是"有则查"，查到的预测与学生
   评估产物同坐标系——错位的是 DB 教师 mask 一侧）。
3. **M4 离线 5 例 flag 清单只存在于报告文本**：eval/
   student_teacher_consistency.json 只有逐类均值无 case 级分布，回归测试
   的基线清单（_baseline_flagged_cases）取自 M4-REPORT §3.4 文本。后续若
   重跑离线评估，应把 case 级分布写进产物 JSON（本任务不改 M4 产物）。

---

（M6-a 一节由 M6-a 实施方在此追加。）
