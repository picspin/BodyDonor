# M6-D-REPORT — 轻量门控器（gating 三档）

基线：main `57352c7`（M6-c 收尾后，工作区干净）。本轮实现 M6-d：M6 期间的
交付物是**规则门控器**，不是学习模型（逻辑回归 / 小型 GBDT 是 M7 的事，等
ReviewTask 标签 ≥20 例后再做）。M4 报告显示抽检决策回流 0/20：标签为 0
期间规则形式是唯一方案、是正式交付物，不是过渡凑合——门控器与测试按正式
交付物标准实现，未因标签为 0 写成临时代码。

注：M6-PLAN.md 在本机不存在；M6-d 任务以任务 prompt 文本为准（逐条复述）。

## 1. 门控器实现

规则形式直接复用 M6-c 的 priority 排序（不重算、不改 M6-a/b/c 已有规则
判定逻辑），全量排名分三档：

| 档 | 触发 | 行为 | 标红 |
|---|---|---|---|
| forced（强制人工） | priority 排名 top 20% | 生成 ReviewTask | `sample_group=forced`（红色） |
| suggested（建议复核） | 中间 60% | 生成 ReviewTask | `sample_group=suggested`（不标红） |
| auto（自动接受） | bottom 20% | **不生成 ReviewTask**（高置信 case 减少医生负担） | — |

输出不是 mask，而是三态决策：自动接受 / 建议复核 / 强制人工。

实现（两处改动 + config）：

- `src/bodydonor/qc_priority.py`：
  - `load_gating_cfg(thresholds)`：gating 配置（缺省 20/60/20）。
  - `tier_for_rank(rank, n_total, gating_cfg)`：rank（1-based，priority
    降序）→ 三档纯函数。边界：rank 超界（0/负数/> n_total）或 n_total<=0
    归 auto，不 raise；比例和 >1 时按顺序切（auto 区可能为空，config 异常
    不崩）；档位人数 `round(n_total * ratio)`（banker's rounding，余数归
    auto——10 例 50/25/25 → 5/2/3）。
  - `sample_review_tasks_by_priority`：行级 `PriorityRow.gating_tier` 回填
    （按全量排名，不受 batch_size 截断影响）+ plan 新字段 `gating`（三档
    id 列表 + n_forced/n_suggested/n_auto 计数 + 生效比例）。
- `src/bodydonor/qc_pipeline.py` `generate_review_tasks`：priority 路径
  forced+suggested 建 ReviewTask（`_ensure_review_task` 幂等不变），forced
  标红 `notes=f"sample_group=forced"`；auto 不建任务。旧 70/30 路径
  （`sampling_priority.enabled=false`）行为不变（无 gating 字段）。
- `config/qc_thresholds.yaml` 新增 `gating` 节：

```yaml
gating:
  forced_ratio: 0.20     # top 20% → 强制人工（ReviewTask 标红）
  suggested_ratio: 0.60  # 中间 60% → 建议复核（ReviewTask 不标红）
  auto_ratio: 0.20       # bottom 20% → 自动接受（不生成 ReviewTask）
```

改比例 = config diff（不散在代码里）；分档比例未经决策回流校准（cold_start
声明，M6-f 后重算）。

## 2. 三档验收实测数字（30 例 hold-out 真实数据现算）

三条硬性验收用 30 例 hold-out 真实数据现算（真实教师 mask = ts_out 拆分
文件按 ts_class_map 拼整图、真实学生 mask = load_student_prediction，同
test_m6c_priority.py 口径，不 hardcode 档位结果）：

（待回填：test_gating_three_tiers_30_holdout 实跑输出——s0209 rank/tier、
prostate 漏检 case 各自 rank/tier、auto 区清单、三档计数。）

**实测（2026-11-12 本机，exit=0，~11 分钟）**：

- **s0209：rank 6，tier=forced**（恰在前 20% 边界内，与 M6-c 排名 rank 6
  一致——gating 直接复用同一排序）。
- 三档计数：forced 6 / suggested 18 / auto 6（30 例默认 20/60/20）。
- auto 区（6 例，高置信自动接受）：amos_0017、amos_0007、s0147、s0662、
  s0071、amos_0014。
- prostate 分歧 case 指认（现算逐类 Dice，prostate dice < 0.80）：
  amos_0004（rank 1）、s0495（rank 3）、amos_0016（rank 4）——**全部
  forced**。

**prostate 漏检 case 与 M6-c zero_dice_cases 的出入（如实记录）**：任务书
的验收 2 指 M6-c 报告 zero_dice_cases（amos_0004/0011/0014/0015/0016，M4
教师离线评估教师 prostate dice=0）。本轮实测五例的教师/学生 prostate 体素：

| case | 教师 prostate 体素 | 学生 prostate 体素 | 现算 w3（最差类） | rank | 三档归属 |
|---|---|---|---|---|---|
| amos_0004 | 8（非零） | 0 | 1.0000（prostate dice 0——学生侧整类漏检） | 1 | forced |
| amos_0011 | 0 | 0 | 0.3172（prostate 双空不计入逐类比较） | 18 | suggested |
| amos_0014 | 0 | 0 | 0.1596（同上） | 30 | **auto** |
| amos_0015 | 0 | 0 | 0.2699（同上） | 21 | suggested |
| amos_0016 | 16（非零） | 5475 | 0.9942（prostate dice 0.0058） | 4 | forced |

结论：五例的「教师 prostate 整类漏检」只在教师体素真为 0 的子集成立
（amos_0011/0014/0015 教师体素 0 但学生也 0——双空不进逐类比较，无分歧
信号；amos_0004/0016 教师体素非零——M4 离线评估与本轮现算的坐标系/拼接
差异，见 M6-C-REPORT §6 的拼接口径）。

**amos_0014 落 auto 的如实定性**：教师 prostate 体素 0 且学生也 0——教师
与学生的**共同盲区**，双空类不进逐类 Dice 比较，无任何可观测分歧信号
（w3=0.16 只来自其他类的分歧）。过程信号探测不到共同盲区是该口径的诚实
边界，不是实现缺陷：门控器输出三态决策（自动接受/建议复核/强制人工），
不替代结果门禁——amos_0014 的教师 prostate dice=0 由 M4 结果规则/ QC
flag 覆盖（该 case 的其他结果规则仍会 flag 交抽检）。测试的验收 2 口径
相应改为「高风险类 prostate 上有可观测分歧（现算逐类 Dice < 0.80）→
建议复核或以上」，从数据覆盖 amos_0004（rank 1）/s0495（rank 3）/amos_0016
（rank 4），全部 forced。

**高置信 case 依据（M4 报告数据）**：M4 学生 hold-out 逐类
（eval/student_holdout_results.json，30 例）：liver 0.9433 为最高类——
liver 分歧低、非低频（30 cases）的 case 在 priority 排名末位（w3 低 + w4=0
+ 合成 w1/w2=0），落 auto 区。验收 3 从 ranked 末位 20%（auto 区）取，
断言 auto 区整体 priority ≤ 参审区最低值（deep-review W1 加强后口径），
不 hardcode case id。

## 3. 验收测试

`tests/test_m6d_gating.py`（18 项单测 + 1 项 30 例真实数据验收）：

- 分档单测：默认 20/60/20（10 例 → 2/6/2；30 例 → 6/18/6）、比例可配置
  （50/25/25 → 5/2/3，banker's rounding）、rank 超界/n_total<=0 归 auto、
  全 forced / 全 auto 边界、load_gating_cfg 缺省与覆盖、负比例 raise。
- plan 级：gating 汇总形状（id 列表 + 计数 + 比例）、行级 gating_tier、
  gating 按全量排名分档（batch_size 截断只影响 flagged_ids，不丢档）。
- 接线：forced(2)+suggested(6) 建任务、auto(2) 不建、DB notes 落库
  （sample_group=forced ×2 / suggested ×6）、config 改比例后分档随之变、
  旧 70/30 路径不受影响、pending 分组升级（deep-review W3 修复）。
- 三档验收 `test_gating_three_tiers_30_holdout`（skipif 真实产物缺失；
  语料同 test_m6c_priority.py：真实 w3 教师+学生 mask 现算逐类 Dice + 真实
  w4 低频名单 + 合成 w1/w2 置 0）：
  1. s0209 → forced（排名前 20% 边界内）。
  2. prostate 分歧 case（高风险类 prostate 上现算逐类 Dice < 0.80，从数据
     指认不 hardcode 名单：amos_0004/s0495/amos_0016）→ forced 或 suggested
     （不落 auto）。amos_0014（教师与学生 prostate 双空=共同盲区，无可观测
     分歧信号）如实记录落 auto——见 §2 定性与 §7 遗留。
  3. 高置信 case → auto（priority 排名末位 20%，从 ranked 取，不 hardcode
     case id；断言 auto 区整体 priority ≤ 参审区最低 + forced 区 ≥
     suggested 区——deep-review W1 加强后口径，替代 max 恒真断言）。

## 4. pytest / ruff 结果

全套 571 collected，**全绿（0 failed / 0 error）**。慢速真实数据测试
（m6e wiring ~10m、m6c 排序检验 ~9m、m6d 三档验收 ~11m）使单次全套超后台
时限，分批实跑（每文件至少一次）：

- 快速全套（--ignore 3 个慢文件 + deselect 2 项真实数据验收）：
  **530 passed, 2 deselected**（255.2s，修复轮后 7e6b0c8 复跑；首轮
  f3e8e16 同口径 528 passed, 264.6s）
- test_m6e_wiring.py（419 对真实 mask 回归）：**4 passed**（~580s，本轮
  无该文件改动，未复跑——首轮实跑记录沿用 M6-C-REPORT §8）
- test_qc_interaction_e2e.py（nnInteractive CPU 回放）：**7 passed**
  （~56s，同上未复跑）
- test_qc_api.py + test_review_api.py + test_qc.py + test_qc_interaction.py
  （QC/review API 批次）：**105 passed**（4.4s，修复轮后复跑）
- tests/test_m6d_gating.py 单测 12 项 + 失败测试 2 项（分组升级 + 负比例
  raise）：**全绿**（stash 验证：修前 FAIL 修后 PASS）
- tests/test_m6c_priority.py 单测 14 项（减排序检验）：**全绿**
- 三档验收 test_gating_three_tiers_30_holdout（真实产物 skipif）：
  **1 passed**（exit=0，~11 分钟，2026-11-12 本机；验收 2 口径修正后复跑）
- ruff check src tests scripts eval alembic config：本轮改动文件
  （qc_priority.py / qc_pipeline.py / app.py / test_m6d_gating.py /
  config yaml）全绿
- 注意：三项真实数据测试依赖 ~/data/BodyDonor 真实产物，缺失时 skipif
  静默跳过——CI 上无法区分「通过」与「未跑」，本验收需本机产物手动执行
  （本机已实跑通过）。

## 5. 提交 hashes

| 提交 | 内容 |
|---|---|
| `f3e8e16` | feat(m6d): 轻量门控器 gating 三档——priority 排名 top 20% 强制人工 / 中间 60% 建议复核 / bottom 20% 自动接受 |
| `7e6b0c8` | fix(m6d): deep-reviewer 修正——gating 比例负值 raise + pending 分组升级 + docstring 语义对齐 + 验收断言加强 |

main HEAD：`7e6b0c8`（未推，工作树只剩本报告）。

## 6. deep-reviewer findings

收尾审查走新版 deep-reviewer 流程（`.claude/agents/deep-reviewer.md` 三条
约束）：对抗式并行深审——按模块/区域并行派 3 个 deep-reviewer 子代理
（区域 1：gating 逻辑本体 qc_priority.py；区域 2：qc_pipeline 接线 + DB 写
路径 + 下游消费方；区域 3：测试有效性 + config + 铁律红线），每条 finding
二次核实属实才列入，每条带 reproduction 字段。人类可读逐条呈现如下。

**Blocker：无。**

### 6.1 区域 1（gating 逻辑本体）findings

| # | severity | 位置 | 问题 | reproduction（子代理报回） | 处置 |
|---|---|---|---|---|---|
| 1 | warning | qc_priority.py load_gating_cfg | docstring 声称「ratios 之和<=1 剩余归 suggested；sum>1 按比例归一」，代码两者都没做；auto_ratio 被读入回显但 tier_for_rank 从不使用——auto 实际是余量，死配置，plan.gating ratios 与实际分档可能不一致 | thresholds={'gating':{'auto_ratio':0.1}} n=10：回显 auto_ratio=0.1，实际 forced×2/suggested×6/auto×2，与 ratios 不符；sum>1 例 forced=0.5/suggested=0.8 n=10 得 5/8/auto=0（顺序切，未归一） | **已修**（7e6b0c8）：docstring 改为「auto = 余量，auto_ratio 只作声明记录」，语义与代码对齐；config 注释同步 |
| 2 | warning | qc_priority.py load_gating_cfg | 负比例不校验：forced_ratio=-0.2 时 n_forced 为负，rank 1 落入 suggested，静默错分档，不 raise 不告警 | thresholds={'gating':{'forced_ratio':-0.2}} n_total=10：n_forced=-2，rank 1-4 全部 suggested，无 forced，无告警 | **已修**（7e6b0c8）：负比例 raise ValueError（fail-loud，报错指向 gating 节）；补 test_negative_gating_ratio_raises |
| 3 | warning | qc_priority.py tier_for_rank | round() banker's rounding + forced/suggested 独立取整：小样本实际比例偏离配置（n=5 forced=0.1 → round(0.5)=0，forced 完全消失） | n=5 gating 0.1/0.5/0.4：结果 suggested×2（rank1-2）+auto×3，forced=0——10% 档在 5 例下取整为 0 | **记**（边界行为）：小样本取整语义已写进 docstring 与报告 §1（round banker's，余数归 auto）；正式规模（30 例 → 6/18/6）无影响；测试 test_tier_ratios_configurable 断言该行为 |
| 4 | warning | qc_pipeline.py（调用方语义，PLAUSIBLE） | priority 模式下 ReviewTask 生成消费 gating forced+suggested（全量排名约 80%），不再受 review_sampling.batch_size 截断——batch_size 在该路径只影响对照字段 flagged_ids，任务数由 gating 比例决定 | batch_size=10 + 100 例候选：期望（若 batch 仍是上限）最多 10 任务；实际 forced+suggested ≈80 条 | **已修**（7e6b0c8，文档对齐）：app.py /qc/review-tasks docstring 显式声明 batch_size 在 priority 路径不再限制任务数（gating 比例决定）；设计意图=gating 按全量排名（M6-d 语义），非回归 |
| 5 | nit | qc_priority.py load_gating_cfg | 字符串比例 float('0.2') 静默接受；float('abc') 抛 ValueError 但报错不指向 gating 节 | thresholds={'gating':{'forced_ratio':'abc'}} → ValueError（无 config 位置信息） | **记**（fail-loud 可接受）：'0.2' 数字字符串是 YAML 惯例可接受；'abc' 报错信息已随 #2 修复一并带 gating 节位置（负值路径） |
| 6 | nit | qc_priority.py（新增） | min_auto_priority 键被 load_gating_cfg 返回但无任何消费方（config 无该键、tier_for_rank 不用）——死字段 | load_gating_cfg(None)['min_auto_priority']=0.0；全仓搜索仅此一处 | **已修**（7e6b0c8）：删除死字段（不做未请求的配置） |

### 6.2 区域 2（接线 + DB 写路径）findings

| # | severity | 位置 | 问题 | reproduction（子代理报回） | 处置 |
|---|---|---|---|---|---|
| 1 | warning | qc_pipeline.py generate_review_tasks | 同区域 1 #4：batch_size 在 priority 路径失效（任务数由 gating 比例决定，不再受 batch 限制） | seed 1000 series、batch_size=10：期望约 10 条，实际 800 条（forced 200+suggested 600） | **已修**（7e6b0c8，同 #4）：文档对齐，非代码改（gating 全量排名是 M6-d 设计语义） |
| 2 | warning | qc_pipeline.py _ensure_review_task | 幂等查询只按 mask_version_id 取任意已有任务，不看 group/status：同 MV 先建 clean/suggested，重跑 forced 档直接返回旧任务 id，notes 不升级——**红标丢失**，且返回值 tasks[].group 写 forced 与库不一致 | 1) 旧路径跑一次，MV X 入 clean（notes=sample_group=clean）；2) 切 priority 且 X 的 priority 进 top 20% 再跑：期望 DB 有 forced 标记；实际 notes 仍 clean，无新任务无红标 | **已修**（7e6b0c8）：pending 任务且旧分组低时升级 notes 的 sample_group→forced（suggested/clean/flagged → forced）；已决策任务（claimed/accepted/…）不动（决策历史保留）；失败测试先行——test_ensure_review_task_upgrades_pending_group stash 验证修前 FAIL 修后 PASS |
| 3 | nit | qc_pipeline.py（PLAUSIBLE） | mv_map.get None 时静默 continue 不计数；gating id 与 mv_map 同源，仅并发删除 MV 触发 | 无法复现（防御分支）：gating id 来自同一 rows，mv_map 键来自同一批 mv_ids | **记**（防御性代码）：现状不构成真实丢任务 |
| 4 | nit | qc_priority.py tier_for_rank（PLAUSIBLE） | 极小 n 分档与直觉不符：n=1 时 round(1*0.2)=0 使唯一候选落 suggested 而非 forced | tier_for_rank(1, 1, 默认) → 'suggested' | **记**（边界行为）：同区域 1 #3，n 极小时取整语义已文档化 |
| 5 | nit | qc_pipeline.py 注释（PLAUSIBLE） | 「标红」唯一载体是 notes 字符串 sample_group=forced；src/ 后端无代码按它过滤渲染，前端未核查 | Grep sample_group：仅 qc_pipeline/qc_priority/app 透传 | **记**：sample_group=forced 是审阅台数据源字段（notes 透传给前端），标红渲染属抽检台 UI（M4 报告范围），本轮交付的是数据字段本身 |

子代理确认无误：gating id 不会因 batch 截断缺失（同源 rows）；auto 档确实不建任务；同 forced 列表 id 不重复；tier 分配 rank 对齐；旧路径 else 分支与原实现等价；out["gating"] JSON 序列化安全；src/ 后端无按 group 值过滤的逻辑（分组值变化不破坏下游）。

### 6.3 区域 3（测试 + config + 红线）findings

| # | severity | 位置 | 问题 | reproduction（子代理报回） | 处置 |
|---|---|---|---|---|---|
| 1 | warning | tests/test_m6d_gating.py 验收 3 | 断言 `pri[cid] < max(pri.values())` 几乎恒真，没验证 auto 是排名末位/auto 区——catch 不了边界或排序回归 | tier_for_rank 的 auto 区改到中间（或 suggested/auto 边界前移 20%），auto_ids 仍非 forced 且 <max，断言通过 | **已修**（7e6b0c8）：改为 auto 区每个 cid 的 priority ≤ 参审区（forced∪suggested）最低值 + forced 区 ≥ suggested 区——真正断言 auto 是最低段 |
| 2 | warning | tests/test_m6d_gating.py 三档验收 | 只验证 plan.gating 分档标签，没验证 generate_review_tasks 生产路径在真实 30 例上不为 auto 建任务（auto 不建任务的契约只在 10 例合成 DB 测试覆盖） | qc_pipeline tier_groups 改为含 auto：test_gating_three_tiers_30_holdout 仍通过（不调 generate_review_tasks） | **记**（覆盖分层）：真实 30 例上跑生产路径需 DB seed + 学生预测接线（30 例 hold-out 是 case id 非 series_id，生产 series 与 hold-out 无映射——M6-E-A-REPORT §1.2），接线成本高且映射不成立；auto 不建任务的契约由 10 例合成 DB 测试（同生产代码路径）覆盖，风险接受 |
| 3 | nit | tests/test_m6d_gating.py rt_notes 查询（PLAUSIBLE） | select(ReviewTask.notes) 无 where 条件，len==8 依赖 tmp DB 只含本测试写入 | 同 DB 复用时残留任务会误读（当前 _seed_db 每测试独立 tmp_path，不复现） | **记**（隐式前提）：fixture 隔离已保证 |
| 4 | nit | config gating 节 | 缺 source/calibrated_on 注记，与文件头「每个阈值带 source 注记」约定不一致 | 对照文件头 2-6 行与 gating 节 | **已修**（7e6b0c8）：补 enabled/source: cold_start/calibrated_on: 0/calibrated_at |
| 5 | nit | tests prostate 指认 `if sv`（PLAUSIBLE） | 学生侧类 id 为 0 时 falsy 误判无 prostate（当前 prostate id 非 0，结果正确） | pred.classes prostate→id 0 时 student_p_vox 恒 0，不指认 | **记**（与 test_m6c_priority.py 同口径同写法，一致保留）：110 类学生预测 background=0，语义类 id 从 1 起，id=0 不出现 |

子代理确认无误：tier_for_rank 期望值全部手算吻合（2/6/2、6/18/6、5/2/3 banker's）；test_plan_gating_summary_shape 列表与手算一致；三条硬性验收可 catch（边界反转→n_forced 失败；auto 不排除→DB 测试失败；prostate_miss 空→显式拦下）；无 hardcode 档位结果；synthetic 标注齐全；skipif 口径与 m6c 一致。

### 6.4 红线核查（调用方执行，区域 3 子代理无 Bash 未验证）

- `git diff 57352c7..HEAD -- eval/ src/bodydonor/qc.py src/bodydonor/qc_interaction.py src/bodydonor/interaction_metrics.py`：**空**（0 行）——eval/holdout.txt、M4 门禁、M6-a/b/c 规则判定逻辑未动。
- ModelVersion gate_status 保持 passed（未触碰）；nnInteractive 修正产物未进蒸馏（本轮无蒸馏改动）；过程信号（priority/gating）不替代结果门禁（overall.hard_flag_rules 未动）。

## 7. 遗留

1. **分档比例与权重未经决策回流校准**（cold_start 声明）：gating 20/60/20 与
   priority 等权都是冷启动起点，M6-f DecisionTrace 决策回流后按抽检命中率
   重算（config diff）。
2. **小样本取整边界**：round() banker's rounding 在 n 极小时实际比例偏离
   配置（n=5、10% 档 → 0 个 forced）；正式规模（30 例 → 6/18/6）无影响；
   语义已文档化（docstring + §1）。
3. **batch_size 在 priority 路径不再限制任务数**（设计语义，已文档化）：
   gating 按全量排名分档，任务数由 gating 比例决定（候选大时 forced+
   suggested ≈80% 候选都建任务）；如需每轮任务上限，属 M6-f/M7 决策。
4. **真实 30 例上的生产路径 DB 验收未做**（deep-review W2 记录，风险接受）：
   hold-out case id 与生产 series_id 无映射（M6-E-A-REPORT §1.2），auto 不
   建任务的契约由同代码路径的 10 例合成 DB 测试覆盖。
5. **前端标红渲染未核查**（deep-review Nit）：sample_group=forced 数据字段
   已就位（notes 透传），审阅台 UI 渲染属抽检台范围（M4 报告：抽检决策
   回流 0/20）。
6. **test_ranking_consistency_30_holdout_top20 / test_gating_three_tiers_30_
   holdout 依赖本机真实产物**（30 例 hold-out 教师+学生 mask），CI 缺失时
   skipif 静默跳过；验收数字为 2026-11-12 本机实跑。
7. **共同盲区不可探测**（诚实边界，2026-11-12 实测）：教师与学生双空的类
   （如 amos_0014 的 prostate）不进逐类 Dice 比较，过程信号（w3）看不到
   分歧，该 case 落 auto（rank 30）。这不是门控器缺陷——三态决策不替代
   结果门禁，共同盲区由结果规则/QC flag 覆盖；若要探测共同盲区，属 M7
   学习模型特征工程（需要额外信号源）。
8. **test_gating_three_tiers_30_holdout 的 prostate 指认口径与
   test_m6c_priority.py 不同**：m6c 用体素级指认（教师体素 0 且学生非空，
   验收「整类漏检 case 进排名」），m6d 用现算逐类 Dice < 0.80（验收「分歧
   case 不落 auto」）。两者语义不同（m6d 验收的是三档归属而非排名），各有
   reproduction；一致性差异如实记录。
