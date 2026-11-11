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

## 2. M6-a：轨迹结构化——provenance.interactionMetrics（过程级指标）

### 2.1 任务与范围

~/M6-PLAN.md M6-a 节（v2，2026-11-11 修订版为准）：M5-e 的 provenance
`{sessionId, baselineTeacherVersion, prompts[], acceptedClasses[]}` 只存档
不计算；M6-a 增加 per-class 过程指标 `interactionMetrics`，**不改变合规
语义**（nnInteractive 产物仍不进蒸馏训练集，消费方只读本指标、不读像素）：

    {"<class>": {numPrompts, convergenceSteps, perStepDeltaDice[],
                 finalLocalDice, teacherReliability, abandonedAttempts}}

v2 关键修正（本节实现的口径）：

- **perStepDeltaDice[i] = Dice(draft_{i-1}, draft_i)，草稿间语义**——绝不用
  教师 mask 当伪真值（M4 实测教师 prostate 0.5914、AMOS 5/15 整类漏检、
  39/117 低频类不可靠；教师当真值会系统性误导：用户修正教师漏检区域时
  ΔDice 反而显示"变差"）。
- **convergenceSteps = 连续 2 步 perStepDeltaDice < τ（τ=0.01）时的步数**
  （明确可计算；未收敛 null）。
- **finalLocalDice 保留 vs 教师语义，必须带 teacherReliability 标记**：
  M4 高风险类（prostate + 39/117 低频名单）为 "low"，M6-b 对这些类降权。
- **abandonedAttempts** 记录 M5-b"越改越差 → 放弃本类"次数。
- **采集点在 M5-d 推理调度，只持久化已有中间变量，不新增推理开销**；
  在上一版草稿仍在内存时直接算该步 delta，不从事后落盘产物重建草稿序列。

### 2.2 采集点核实结论（关键设计点）

**核实方式**：读 nni_runner.py / nninteractive_backend.py / app.py /
review.html 的实际数据流，2026-11-12 实测。

1. **nnInteractive 修正每次全量跑**：nni_runner.py `run_task` 从空 target
   buffer 开始跑所有 prompts（上游 nnInteractiveInferenceSession 无"上一版
   草稿"状态跨请求驻留）——草稿序列**不存在于单次推理进程内**，只存在于
   **连续多次 HTTP 请求之间**（前端 m5RunInference 每次提示变化发一次
   `/review/{series_id}/nninteractive-correct`）。
2. **因此采集点在 app 层**：`review_nninteractive_correct` 推理成功后把本次
   草稿落 per-series 进程内 tracker（`_interaction_trackers`），上一版草稿
   仍在 tracker 内存中时直接算该步 delta——**不重跑推理、不读 nnInteractive
   像素做任何指标以外的用途**（草稿 mask 本身来自 correct_image 落盘产物，
   只用于 ΔDice 计算，不落库、不进蒸馏）。
3. **tracker key 三端统一**：nninteractive-correct（采集）/ submit-nni
   （持久化）/ abandon-draft（放弃）都用语义类名（body.class_name，前端
   m5ClassName(lv)，缺省回退 `label-{n}`）。
4. **前端放弃路径**（review.html data-discard）目前只清前端状态、不发后端
   请求；abandon-draft 端点已就位（M6-b 消费时前端接线即可，本轮不改前端）。

### 2.3 实现

- **`src/bodydonor/interaction_metrics.py`**（77e1186，纯逻辑新模块）：
  `dice_binary`（双空集=1.0）/ `convergence_steps`（连续 2 步 < τ，1-based）
  / `teacher_reliability`（prostate 显式 low + 低频 <10 cases low，计数缺失
  只按显式名单判）/ `load_teacher_eval`（eval/holdout_results.json 的
  117 类 per_class_cases）/ `InteractionTracker`（record_draft 就地算
  delta、record_abandoned 清草稿+计数、build_metrics 产 JSON 条目）。
- **`app.py` 接线**（fa01c09 + e8aeecb）：
  - `review_nninteractive_correct`：推理成功且 n_target_voxels>0 时草稿落
    tracker，返回值增 `step_delta_dice`（首步 None）+`step_delta_error`；
    采集失败不挡修正主流程。
  - `review_submit_nni`：provenance 增 `interactionMetrics`（该类条目；
    finalLocalDice = 基线 mask ∪ 本次修正体素 vs 基线，结果级、带
    teacherReliability 标记；教师基线 mask 不可读时 null 不猜数字）；
    tracker 无数据时空 dict **不造假**（重启后草稿历史丢失）。
  - `POST /review/{series_id}/abandon-draft`：放弃事件 → abandonedAttempts
    +1、草稿序列清空（与 DraftStore.discard 同语义）。
- `CorrectionResult` 增 `step_delta_dice`/`step_delta_error` 字段
  （nninteractive_backend.py，默认 None 不影响既有调用方）。

### 2.4 实测数字

**回放验证**（scripts/replay_m6a_metrics.py，4a51e98）：

- **回放源**：无可回放的真实会话——DB mask_versions 表 10 行全部无
  provenance（M5 浏览器会话在修正前 4 轮 manual 版本、nnInteractive 产物
  不建行）；真实 sidecar 仅 1 条（nni-gpu-verify/amos_0004，单 point 提示、
  无草稿序列）；浏览器测试记录（run4-*/results.json）未保留。按任务 7b
  用**真实教师 mask + 脚本化提示序列**构造回放。
- 真实教师 mask：amos_0004 aorta（ts_out/aorta.nii.gz，**14468 体素**）。
- 脚本化提示序列：3 步草稿 = 教师区域 50% → 90% → 100%（模拟医生逐步
  提示补全欠分割区域）。
- **一致性：tracker perStepDeltaDice [0.7143, 0.9474] == 手工草稿间 Dice
  [0.7143, 0.9474]（逐位一致）**。
- **伪真值检查**：vs 教师序列 [0.6667, 0.9474] ≠ 草稿间序列 [0.7143,
  0.9474]（第一步若混入教师会得 0.6667——证明 ΔDice 只由草稿序列决定）。
- finalLocalDice（草稿3=教师）= 1.0；teacherReliability = high（aorta
  30 cases 非低频）；convergenceSteps = null（逐级扩展无 <τ 收敛）；
  abandonedAttempts 放弃后 = 1、序列清空。

**单测**（tests/test_interaction_metrics.py，30 项全绿）：

- dice_binary 4 项（相同 1.0 / 双空集 1.0 / 部分重叠 / 不相交 0）
- convergence_steps 7 项（连续 2 步才计数 / 中断重置 / ΔDice==τ 不算
  below / 单步不足 / 从未收敛 / 空）
- teacher_reliability 6 项（prostate 显式 low / 低频 low / 高频 high /
  计数缺失只按显式名单 / 教师评估产物 117 类加载 / 文件缺失 None）
- InteractionTracker 纯逻辑 9 项（首步无 delta / 草稿间语义与教师无关 /
  **教师整类漏检 synthetic case：草稿扩展 delta ∈ (0,1) 反映真实改进方向
  绝不因教师为空判"变差"** / 完整结构 / <τ 收敛形态 / 放弃计数+清草稿 /
  放弃后重做累计 / JSON roundtrip）
- TestSubmitNniPersistence 4 项（tracker 采集 → submit-nni →
  MaskVersion.provenance.interactionMetrics **DB roundtrip**（生产库同款
  sqlite 往返，numPrompts/sessionId 逐位一致）/ 空 tracker → interactionMetrics
  = {} 不造假 / abandon 端点累计 + 重复放弃计数 → submit 后 provenance
  反映 / 非法 target_label 422）

**接线行为验证**（TestSubmitNniPersistence 实测值）：2 步草稿（16 体素 →
25 体素）→ perStepDeltaDice [0.7805]（= 2·16/41 手工一致）；finalLocalDice
0.9921（基线 label-1 100 体素 ∪ 修正 16 体素有交叠）；"label-1" 不在教师
117 类表 → teacherReliability = low（保守判，语义边界在单测注明）。

### 2.5 提交 hashes（本地 main，未推）

| 提交 | 内容 |
|---|---|
| `77e1186` | feat(m6a): interaction_metrics 模块（per-class 过程指标） |
| `fa01c09` | feat(m6a): 轨迹采集接线（草稿 delta + interactionMetrics 持久化 + abandon-draft 端点） |
| `259d634` | test(m6a): interaction_metrics 单测 26 项 |
| `4a51e98` | feat(m6a): 回放验证脚本（真实教师 mask + 脚本化提示序列） |
| `e8aeecb` | fix(m6a)+test: tracker key 三端统一 + 持久化端到端单测 4 项 |
| `67ca138` | docs: M6-E-A-REPORT M6-a 一节 |
| `fe2b254` | docs: §2.6-2.8 回填最终验证数字（506 passed + ruff 全绿） |

### 2.6 pytest / ruff

- 全套见 §2.8（506 passed）；提交点实测：
  - tests/test_interaction_metrics.py **30 passed**
  - tests/test_review_api.py + test_m5e.py + test_nninteractive_backend.py +
    test_distill_license_gate.py **103 passed**（接线不破坏既有行为）
  - tests/test_m6e_wiring.py（30 例真实 mask 回归）**4 passed**（exit 0）
- ruff check src tests scripts eval alembic config：全绿；
  ruff format：本轮 5 个新改文件已格式化（仓库既有 format 状态不动）

### 2.7 收尾自查

- 全套 pytest：见 §2.8（506 passed）
- /code-review 自查：headless 下斜杠命令不可用，改为逐项自查（结论：
  (a) 采集只读 correct_image 已落盘的草稿 mask 算 ΔDice，**不读
  nnInteractive 像素做指标以外用途、不落库**；(b) interactionMetrics 只进
  provenance JSON 列，nninteractive_guided 来源不进蒸馏训练集（许可门禁
  负测试全绿）；(c) 默认行为不变——tracker 无数据时
  interactionMetrics={}，step_delta_dice=None，既有 submit-nni 测试全绿；
  (d) 无 mock 数字——回放用真实教师 mask，回归用真实 mask 产物；
  (e) 过程信号不替代结果门禁——未动 eval/holdout.txt 与 M4 门禁
  （ModelVersion gate_status 保持 passed））

### 2.8 最终验证数字

- 全套 pytest：**506 passed, 0 failed**（2026-11-12 实测；分两条跑：
  38 个快速文件 472 passed in 194.03s + 2 个慢文件
  test_interaction_metrics.py 30 项 + test_m6e_wiring.py 4 项（30 例真实
  mask 回归）34 passed in 576.14s；首次全套单跑因 m6e_wiring 30 例回归
  拖慢在 10 分钟后台超时被杀，无 fail——分两条覆盖全部 40 个测试文件）
- ruff check src tests scripts eval alembic config：全绿
- 本轮 5 个新改/新增文件 ruff format 已格式化（仓库其余 61 个文件的
  既有 format 状态不动，与 M6-e 同口径）

### 2.9 遗留

1. **前端放弃路径未接 abandon-draft**：review.html 的 data-discard 只清
   前端状态，不发后端请求（abandonedAttempts 只在后端端点被调用时计数）。
   M6-b 消费 abandonedAttempts 时接线（一处 fetch 调用），本轮不改前端。
2. **tracker 是进程内存缓冲**：服务重启后草稿历史丢失，后续 submit 的
   interactionMetrics 退化为空 dict 或只含放弃计数——不造假重建（任务 2
   诚实边界）。若 M6-b 需要跨重启的完整轨迹，需把 tracker 状态序列化到
   sidecar 或 DB（属 M6-b 范围）。
3. **nni_runner 单次推理无草稿序列**：perStepDeltaDice 的"步"粒度 =
   HTTP 请求粒度（前端每次提示变化发一次），不是 runner 内逐步。上游
   nnInteractiveInferenceSession 有 predict_next 语义但未暴露增量接口——
   请求粒度对 M6-b 的 flag 逻辑（numPrompts/convergenceSteps 阈值）够用，
   更细粒度留 M7 特征工程。
4. **"label-1" 类合成类名保守判 low**：测试用合成类名不在教师评估 117 类
   表 → teacherReliability=low。生产类名是语义名（前端 m5ClassName），命中
   117 类表按 cases 数判；不在表内的自定义类同样保守判 low（合理默认：
   无教师可靠性证据时不用 Dice 判断）。

---

## 3. M6-e + M6-a 收尾审查（deep-reviewer 子代理）

> 2026-11-12 审查会话执行。范围：`git log e6357f6..HEAD`（12 提交：学生预测
> 读取入口 load_student_prediction、qc_series 学生 mask 接线 + 419 对回归、
> interaction_metrics 模块 + 采集接线 + 26+4 项单测 + 回放脚本 + 报告）。
> 审查 agent：`.claude/agents/deep-reviewer.md`（只读，tools 仅
> Read/Grep/Glob，Write/Edit 企图被 PreToolUse hook 阻断）。本节只记录，
> 未修任何代码。

### 3.1 findings 摘要（2 blocker / 2 warning / 1 nit）

**Blocker（均经审查会话逐条核实成立）**

1. **tracker key 前端不一致**（`src/bodydonor/static/review.html:812-816`
   + `src/bodydonor/app.py:1184`）：nninteractive-correct 请求体只发
   `prompts` + `target_label`，无 `class_name`；后端缺省回退
   `label-{N}`。而 submit-nni 端 `app.py:1506` 用 `accepted[0]`
   （前端 `m5ClassName(lv)` 的语义类名，review.html:955）查 tracker——
   两边 key 对不上，**生产浏览器流程 interactionMetrics 落 `{}`、
   finalLocalDice 为 null**。e8aeecb "tracker key 三端统一"只在后端代码
   内成立，前端 correct 请求未对齐；单测（TestSubmitNniPersistence）
   直调 tracker 绕过 HTTP 路径，回放脚本直调 tracker 同样绕过，故未暴露。
   修复建议：correct 请求补 `class_name: m5ClassName(lv)` + 端到端测试
   （前端实际 payload 形状 → correct → submit-nni → 断言
   interactionMetrics 非空）。**等定夺再修。**
2. **abandon-draft 端点无前端调用方**（`src/bodydonor/app.py:1595`）：
   review.html 无任何 abandon fetch——生产路径 abandonedAttempts 恒为 0。
   注：报告 §2.9 遗留 1 已显式声明此遗留（M6-b 消费时接线），属"已知且
   已记录"的 blocker，非新发现。**等定夺再修（即 §2.9 遗留 1 的接线）。**

**Warning（3 条）**

1. **flag 数断言首分支冗余**（tests/test_m6e_wiring.py:269）：审查员认为
   `len(flagged) == len(holdout) or abs(...) <= 1` 首分支使全 30 例 flag
   直接通过。审查会话核实：**下游 symmetric_difference 断言（:273-276，
   diff>2 即 fail）兜底**——全 flag 时 diff=25 仍 fail，非漏洞，实为
   冗余断言（首分支可删，行为不变）。严重性降级。
2. **419 对回归无 skipif**（tests/test_m6e_wiring.py:228）：依赖
   `~/data/BodyDonor` 真实产物，无 pytest.mark.skipif/importorskip；
   产物缺失时以 fail（误报回归）呈现而非 skip。核实成立。
3. **回归实跑核实**：审查员只读环境未运行 30 例真实 mask 回归；本轮
   审查会话全套 pytest 实跑已覆盖（见 §3.3），flag 集合与 mean 断言
   通过（exit 0，无 F/E）。

**Nit（1 条）**

1. **round(4) 后算 convergenceSteps**（`src/bodydonor/interaction_metrics.py:168-171`）：
   perStepDeltaDice 先 round(…,4) 再由 convergence_steps 判 <τ，阈值边界
   （恰 0.01 附近）可能与原始值判定不同。核实成立，可接受精度差；建议
   docstring 注明或在舍入前判定。**等定夺再修。**

### 3.2 SubagentStop hook 表现（post-review-to-pr.sh）

- **实测**：hook 以 `hook_success` 触发，exit 0，duration 87ms，输出
  `post-review-to-pr: SKIP — no ```findings block in reviewer output`。
- **与预期失败点不同**：任务预期"当前 main 无 open PR，PR 查找步干净
  失败"（脚本第 4 步）；实测在第 2 步（findings 块解析）就 SKIP——
  background 子代理的最终输出在 hook 收到的 transcript_path（父会话
  jsonl）里以 task-result 呈现，不是 assistant text block，脚本的
  findings 正则解析不到（父会话 transcript 实测 findings 块 0 个；
  子代理自身 transcript 里 1 个、正则可命中）。未走到 PR 查找步
  （`gh pr list --head main` 返回 `[]`，无 open PR，与预期一致）。
- **如实记录**：hook 本身行为正确（无 PR 场景不投递、exit 0 不阻断）；
  偏差根因是 background 子代理形态与 hook 解析假设（同步子代理、最终
  消息即 assistant text block）不匹配。**hook 未修、未绕过**（任务 2
  要求）；修法（读 task 输出文件或改同步调用）等定夺。

### 3.3 最终验证数字（审查会话实测，2026-11-12）

- **全套 pytest：506 passed, 0 failed**（单次全量跑，
  `.venv/bin/python -m pytest -q`，exit 0；进度 dots 506/0 F/E，与
  `pytest --co` 收集数 506 一致；summary 行被输出捕获截断，数字以
  dots 计数 + exit code 为准，与 §2.8 的 506 吻合）
- **ruff check src tests scripts eval alembic config：全绿**
  （All checks passed!）
- **ruff format --check**：全仓 61 文件既有不齐（与本轮无关，未动）；
  本 diff 9 文件中 7 个已格式化，`src/bodydonor/student_pred.py` 与
  `tests/test_student_pred.py`（前轮 af2e5b4/26aef88 文件）format 不齐
  ——记为 nit，本轮不修。
- 独立抽查：`distill_data.py` REJECTED_SOURCES 含 `nninteractive_guided`
  （本 diff 未动）；蒸馏与 qc_pipeline 均不消费 interactionMetrics；
  `git diff e6357f6..HEAD` 对 distill*/eval/holdout.txt 为空——许可边界
  与门禁产物未动，与 §1.8/§2.7 自查一致。

### 3.4 审查遗留（待定夺，本轮不修）

1. Blocker 1（tracker key 前端补 class_name + 端到端测试）
2. Blocker 2 = §2.9 遗留 1（前端 abandon-draft fetch 接线）
3. Warning 1（test_m6e_wiring.py:269 首分支删除——冗余断言）
4. Warning 2（419 回归补 skipif/importorskip）
5. Nit 1（interaction_metrics convergence 判定基于原始值再舍入）
6. format 两条（student_pred.py / test_student_pred.py）
7. hook 对 background 子代理的 findings 解析（改读 task 输出文件或同步调用）

---

（审查一节完。）
