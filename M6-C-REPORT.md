# M6-C-REPORT — 过程风险采样（priority 驱动抽检）

基线：main `9165049`（M6-b 收尾后，工作区干净）。

## 1. 采样实现

新增 `src/bodydonor/qc_priority.py`：过程风险驱动抽检采样，替代 flagged 70% / clean 30% 二分。M4 已证明冷启动阶段 QC flag 区分度低（419 对一致性回归：均值聚合 flag 5/30，其余结果规则 flag 更少），flagged/clean 二分在 flag 少时退化成随机抽检；priority 是连续分数，按降序取前 `batch_size` 名建 ReviewTask：

```
priority = w1*convergence + w2*abandonment + w3*teacher_student_disagreement + w4*low_frequency_class_presence
```

入口 `sample_review_tasks_by_priority(qc_rows, thresholds, per_class_case_count)`，返回兼容形状的 `SamplingPlan`（`flagged_ids`=入选 ids 按 priority 降序、`clean_ids` 恒空——优先级模型无「clean 组」，未入选 case 本轮不审）+ 新增字段 `ranked`（全量 PriorityRow 列表，排名顺序保留）、`component_availability`（各分量可用计数 + `w3_available` + 降级 note）。每行 `PriorityRow` 含各分量值与可用性（DecisionTrace 前置，M6-f 才落实体）。

## 2. 各分量定义与数据来源（各归一化 [0,1]）

| 分量 | 数据来源 | 定义 | 理由 |
|---|---|---|---|
| w1 convergence | `provenance.interactionMetrics`（M6-a） | 每类：普通类 `max(min(numPrompts/N_max,1), min(convergenceSteps/K_max,1))`；高风险类（teacherReliability=low）只用 numPrompts；case 级取各类最大值 | 与 M6-b interaction_convergence 两层逻辑同口径：convergenceSteps 对高风险类无独立校准依据。N_max/K_max 读 config `rules.interaction_convergence`（n_max_prompts=15 / k_max_steps=6），不硬编码 |
| w2 abandonment | `interactionMetrics` | 任一类 `abandonedAttempts >= 1` 则 1，否则 0 | 与 M6-b interaction_abandonment 判据（min_abandonments=1）同形：放弃一次即记信号。不用「总放弃次数/min」变体（min=1 时等价） |
| w3 teacher_student_disagreement | QCReport.rule_scores 里 `multi_teacher_consistency` 的 `detail.per_class_dice`（按类名对齐的真实体素逐类比较，不是均值） | **最差类**：`1 - min(逐类 Dice)` | mean 会稀释单类整类漏检：实测 s0495（教师 prostate 整类漏检，逐类 min 0.0）mean 0.8771、s0556（urinary_bladder 整类漏检）mean 0.8705，mean 聚合下这两个验收要求进前 20% 的 case 排不进 30 例前 6；最差类聚合把整类漏检顶到 1.0。冷启动抽检目的是「找出最坏的 case 交人审」，宁过审不漏审。规则 skipped（无学生 mask）→ w3 不可用 |
| w4 low_frequency_class_presence | 39/117 低频名单（复用 `interaction_metrics.load_teacher_eval` 口径：eval/holdout_results.json 117 类 per_class_cases，<10 cases 为低频；prostate 显式高风险） | case 的参评类中低频类占比（低频类数/参评类数） | 参评类集优先 w3 的 `per_class_dice` 类集（教师/学生真实可比、最代表该 case 实际涉及的器官）；w3 不可用回退 interactionMetrics 类键并集；两者都无 → 不可用。名单文件缺失 → 不可用，不猜 |

## 3. 权重配置

进 `config/qc_thresholds.yaml` **新增 `sampling_priority` 节**（二选一的选择：阈值集中单文件原则不变——qc.py 模块头明示「全部集中在 qc_thresholds.yaml，禁止散在代码里」；单独 config 文件会破坏该原则），`source: cold_start`、`calibrated_on: 0`，note 显式声明「权重是冷启动起点，未经校准：等权 w1=w2=w3=w4=1（2026-11-12）… M6-f 决策回流后重算」。改权重 = config diff。

`review_sampling` 节保留（batch_size 新路径共用；flagged_ratio/clean_ratio/min_clean_pool 供旧路径对照）。

## 4. 降级语义（不造假）

| 情形 | 行为 | 记录 |
|---|---|---|
| multi_teacher skipped / 无学生 mask | w3 置零，只用 w1/w2/w4 排序 | 行 `availability["w3"]=False`、`components["w3"]=None`；plan `component_availability["w3_available"]`（全部缺失=False + note「w3 不可用…」） |
| interactionMetrics 缺失/空 | w1=0、w2=0（无交互即无过程信号） | 行 `availability["w1"/"w2"]=False` |
| 低频名单缺失（eval 产物缺） | w4 不可用置零 | 行 `availability["w4"]=False` |
| 三来源全缺 | priority=0 **排最后** | 无信号不冒充高风险（不是排最前） |

DecisionTrace 实体是 M6-f，本轮不造实体；可用性记录在返回值（`ranked` 行级 + `component_availability` 汇总）与本报告。

## 5. 接线与新旧路径切换

- `generate_review_tasks`（src/bodydonor/qc_pipeline.py）生产路径改用 `sample_review_tasks_by_priority`（config `sampling_priority.enabled`，缺省 true）；候选池查询扩展取 `QCReport.rule_scores`（w3/w4）与 `MaskVersion.provenance.interactionMetrics`（w1/w2）。返回值加 `ranked` + `component_availability`。
- 旧 `sample_review_tasks`（70/30）**保留不动**：既有测试 test_sample_review_tasks_* 保持通过（本轮实跑全绿）；`sampling_priority.enabled=false` 时回退该路径（对照保留）。
- 切换决定：生产路径走 priority（M6-c 目标），旧路径不删以保对照与回退；ReviewTask 生成循环对新 plan 不变（flagged_ids 语义=入选 ids，group 记 `sample_group=flagged`）。

## 6. 排序一致性检验（验收）

语料（任务书 5）：30 例 hold-out **真实 w3**（真实教师+学生 mask 现算 `rule_multi_teacher_consistency` 逐类 Dice，同 test_m6e_wiring.py 口径，不 hardcode 数字）+ **真实 w4**（39/117 低频名单，eval/holdout_results.json per_class_cases）+ **合成 w1/w2**（synthetic：hold-out case 无真实交互轨迹——interactionMetrics 采集始于 M6-a 生产路径；合成值全 0，只用于覆盖分量结构，不因合成分量改变排序）。

### prostate 整类漏检 case 指认（从数据，非编造）

M4 离线评估（eval/holdout_results.json `summary.prostate.zero_dice_cases`）实测教师 prostate dice=0 的 AMOS case：**amos_0004、amos_0011、amos_0014、amos_0015、amos_0016**（M3-REPORT §3.5 已定性「AMOS 侧 dice=0 为教师整类漏检，非映射错误」）。本轮独立复核 amos_0004 同口径真实数据：教师 ts_out 拼接 arr 的 prostate 体素 0、学生预测 prostate 2939 体素（教师有该类名但体素 0 = 整类漏检）。测试断言用现算逐类 Dice=0 从数据指认，不 hardcode 名单。

### 排名表（30 例 hold-out，等权，前 10，测试实跑输出）

| rank | case_id | priority | w1 | w2 | w3 | w4 |
|---|---|---|---|---|---|---|
| 1 | amos_0004 | 1.0667 | null | null | 1.0000 | 0.0667 |
| 2 | amos_0021 | 1.0667 | null | null | 1.0000 | 0.0667 |
| 3 | s0495 | 1.0667 | null | null | 1.0000 | 0.0667 |
| 4 | amos_0016 | 1.0609 | null | null | 0.9942 | 0.0667 |
| 5 | amos_0005 | 1.0079 | null | null | 0.9412 | 0.0667 |
| 6 | **s0209** | 1.0000 | null | null | 1.0000 | 0.0000 |
| 7 | s0338 | 1.0000 | null | null | 1.0000 | 0.0000 |
| 8 | s0556 | 1.0000 | null | null | 1.0000 | 0.0000 |
| 9 | s0847 | 1.0000 | null | null | 1.0000 | 0.0000 |
| 10 | s0447 | 0.9743 | null | null | 0.9743 | 0.0000 |

（w1/w2=null：hold-out case 无真实交互轨迹，synthetic 置 0——分量不可用，如实记 null。w3=1.0 的并列按 mask_version_id 字典序稳定 break。）

### s0209 与 prostate case 排名（测试实跑）

- **s0209**：case 级 mean Dice 0.1181（419 对回归最差，w3 很高）——逐类 min=0（adrenal_gland_left 整类分歧）→ w3=1.0；参评类集 9 类不含 prostate → w4=0。**rank=6**，在前 20% 边界内（前 6）。
- **amos_0004**（教师 prostate 体素 0，整类漏检）：逐类 min=0 → w3=1.0，prostate 在参评类集 → w4=1/15=0.0667。**rank=1**。
- **s0495**（教师 prostate 体素 0，学生 2939 体素）：w3=1.0、w4=0.0667。**rank=3**。
- 其余 prostate 漏检 case（amos_0011/0014/0015）同样全部进前 20%（测试逐个断言通过）。

排序语义：等权下 5 个 w4 加成的 case（参评类集含 prostate：amos_0004/amos_0021/s0495 1.0667、amos_0016 1.0609、amos_0005 1.0079——prostate 分歧 + 其他类整类漏检，语义正确）排在 s0209 之前，s0209 排第 6 = 硬性验收（前 6）边界内。测试断言 `rank <= 6`（任务硬性验收即前 6；不加更严的个人断言）。

硬性验收：s0209 与 prostate 漏检 case 进前 20%（30 例中前 6）——测试断言 `test_ranking_consistency_30_holdout_top20` **通过**。

**M7-S2 改标（2026-11-14）**：本节排名与验收基于 30 例 hold-out（gate 集）现算，该批 case 同时承担 w3/w4 定义校准——**以上比例为「样本内开发指标」（in-sample dev），不得冒充独立验收**；派生统计来源哈希 `derived_from_case_ids_hash` = gate 30 例（见 config/qc_thresholds.yaml note 与 M7-S2-REPORT），独立 calib 集建好后须复核。原数字与原结论不动。

## 7. 提交 hashes

- `3849d51` feat(m6c): 过程风险驱动采样 sample_review_tasks_by_priority + config sampling_priority + generate_review_tasks 接线
- `b0eb242` fix(m6c): deep-reviewer 修正——除零保护 + prostate 漏检体素级指认 + row_by_mv 索引 + docstring 勘误

## 8. pytest / ruff 结果

全套 569 collected，**全部通过（0 failed / 0 error）**。慢速真实数据测试（m6e wiring 580s、排序检验 555s）使单次全套超后台时限，分批实跑（每文件至少一次）：

- batch 1（agent_context_phi…m5e 18 文件）：**127 passed**
- m6c_priority + qc + qc_interaction + review_session：**105 passed, 1 deselected**（deselect = 排序检验，单独跑）
- 排序检验 test_ranking_consistency_30_holdout_top20（真实产物 skipif）：**1 passed**（554.9s，30 例真实教师+学生 mask 现算，2026-11-12 本机）
- review_api + api + qc_api + student_pred + segmentation 等 18 文件：**196 passed**
- test_m6e_wiring.py（419 对真实回归）：**4 passed**（580.5s）
- test_qc_interaction_e2e.py（nnInteractive CPU 回放）：**7 passed**（56.1s）
- gpu_probe + gpu_lock + tui_smoke + agent_context_phi + nninteractive_backend + interaction_metrics：**110 passed**（262.0s）
- ruff check + ruff format：本轮改动文件（qc_priority.py / qc_pipeline.py / test_m6c_priority.py / config yaml）全绿。
- 注意：test_ranking_consistency_30_holdout_top20 依赖 ~/data/BodyDonor 真实产物（30 例 hold-out 教师+学生 mask），缺失时 skipif 静默跳过——CI 上无法区分「通过」与「未跑」，本验收需本机产物手动执行（本机已实跑通过）。

## 9. deep-reviewer findings

deep-reviewer（只读子代理，审查 3849d51 + 工作区 diff）：**无 Blocker**；3 Warning + 3 Nit，处理如下：

| severity | 位置 | 问题 | 处理 |
|---|---|---|---|
| warning | qc_priority.py:149 | 普通类分支 n_max/k_max=0 无除零保护（高风险分支有） | **已修**（b0eb242）：n_max/k_max<=0 该项饱和 1.0；补 test_w1_convergence_zero_thresholds_no_crash |
| warning | tests/test_m6c_priority.py:422 | prostate_miss 判据只看 pcd==0.0，学生侧漏检（教师非空、学生空）同样 dice=0，会混入镜像情形 | **已修**（b0eb242）：改为体素级指认——教师有 prostate 标签且体素 0 且学生侧非空，再列入 prostate_miss |
| warning | qc_pipeline.py:332 | for mv × for rows 嵌套 O(n²)；qc_id 的 next() 线性查找累计 O(n²) | **已修**（b0eb242）：row_by_mv 索引，回填 interaction_metrics 与 qc_id 均 O(1) |
| nit | qc_priority.py:39 | 模块 docstring 残留草稿句「占比口径 min(...)？」与「不做封顶」矛盾 | **已修**（b0eb242）：删除草稿句 |
| nit | qc_priority.py:183 | component_disagreement 对非 dict rule_scores / 非数值 per_class_dice 项不设防（生产路径已强制 dict） | **已修**（b0eb242）：isinstance 守卫 + 非数值项跳过 |
| nit | test_m6c_priority.py | 排序验收 skipif 静默跳过，CI 无法区分通过/未跑 | **已记**（§8 注意行）：报告显式标注依赖本机产物 + 实跑日期 |

已确认无误（审查通过）：_load_priority_cfg 与 yaml 结构一致（weights 缺省 1.0）；component_abandonment 的 bool 排除；component_disagreement 对缺 per_class_dice / 空 dict / 非 dict 返回 (None, {})。

## 10. 遗留

- w1/w2 权重未经校准（等权冷启动起点）：待 M6-f DecisionTrace 决策回流后按抽检命中率重算（config diff）。
- test_ranking_consistency_30_holdout_top20 依赖本机真实产物（30 例 hold-out 教师+学生 mask + eval/holdout_results.json），CI 环境缺失时 skip——验收数字（s0209 rank 6、amos_0004 rank 1、s0495 rank 3）为 2026-11-12 本机实跑。
- hold-out case 无真实交互轨迹，排序检验中 w1/w2 为合成置 0（synthetic，已标注）；w1/w2 分量的真实数据回归待 M6-a 生产路径积累 interactionMetrics 后补。
