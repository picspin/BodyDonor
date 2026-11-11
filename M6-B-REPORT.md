# M6-B 报告：过程级 QC 规则（interaction_convergence / interaction_abandonment）

M6-b 交付：两条过程级 QC 规则接入 `qc_pipeline` 规则框架（rule_scores /
flags / QCReport），消费 `MaskVersion.provenance.interactionMetrics`（M6-a
采集），附带 tracker 跨重启持久化（M6-a 遗留 2 收口）。

基线：main `8004bd9`（36 ahead，未推，工作树干净）。本轮 10 提交，**未推**。

注：任务书指向 `~/workspace/bodydonor/M6-PLAN.md` 的 M6-b 一节（v2），
该文件在本机不存在（`~/workspace/bodydonor` 无此目录）；实施以任务书文本
为准，语义冲突处任务书优先。

---

## 1. 规则实现

新模块 `src/bodydonor/qc_interaction.py`，两规则本体为纯函数（qc.py
rule_outputs 同形状：`(flagged, score, detail)`）：

### 1.1 interaction_convergence（任务 1）

- **普通类**（`teacherReliability=="high"`）：`numPrompts > n_max_prompts`
  或 `convergenceSteps > k_max_steps` 即 flag（即使最终 Dice 不错）。
  `finalLocalDice` 只进 detail 作辅助信息，**不作判据**（任务书 3）。
- **高风险类**（`teacherReliability=="low"`，prostate + 39 低频名单）：只看
  `numPrompts > n_max_prompts`——`convergenceSteps` 对该类无独立校准依据
  （教师伪真值不可靠），不看；`abandonedAttempts` 维度归 abandonment 规则。
- 语义：该类的自动结果不可信，需要人工介入。

### 1.2 interaction_abandonment（任务 2）

- 任一类 `abandonedAttempts >= min_abandonments` 即 flag（高 flag，初值 1）。
- 两层同判据（放弃事件本身是"连交互式修正都无法收敛"的过程信号）；
  `detail.high_risk_flagged` 标注高风险层。
- 语义：连交互式修正都无法收敛。

### 1.3 两层逻辑的分层依据（任务 3）

`is_high_risk(entry)`：直接复用 provenance 条目里 M6-a `build_metrics` 已
按 `teacher_reliability()` 打好的 `teacherReliability` 标记（prostate +
39/117 低频名单），**规则不重算**，两端同一标记同一语义。旧数据缺标记时
回退显式名单（prostate）；无名册且无标记 → 按普通类（不猜）。

**语义校准（提交 9e15a5d）**：初版 convergence 规则在高风险类也查
`abandonedAttempts`，导致同一放弃事件双规则双 flag；修正后规则各管一个
维度（convergence=提示数/收敛步数，abandonment=放弃数），同事件不双 flag。

### 1.4 适用范围与 skipped（任务 4）

`run_qc` 新可选参 `interaction_metrics`；`qc_series` 从最新 MaskVersion 的
`provenance.interactionMetrics` 传入。**只适用于有 nnInteractive 交互的
case**（interactionMetrics 非空 dict）：

- 无 provenance / 空 interactionMetrics（纯自动 case）→ 两规则显式
  `{"skipped": "no interaction metrics"}`，与 M4 §3.3 / multi_teacher
  同语义：不进 flags、不计入 enabled 分母（`enabled_n = len(rule_outputs)`），
  调用方可区分（`rule_scores[key]` 的 skipped 键）。
- disabled → `{"skipped": "disabled"}`（既有模式）。

### 1.5 接线点

- `qc.py run_qc`：新参 `interaction_metrics`；两规则 evaluation 块在
  multi_teacher 之后、汇总之前。
- `qc_pipeline.qc_series`：`mv.provenance.interactionMetrics`（非 dict 时
  归空）传 `run_qc`。
- 边界（铁律，任务 8）：只读 `provenance.interactionMetrics` 标量字段，不读
  nnInteractive 像素输出；不碰 `distill_data.py` 许可门禁（许可负测试全绿，
  见 §5）；不动 `eval/holdout.txt` 与 M4 门禁；两规则不在
  `overall.hard_flag_rules`（过程信号不一票否决，不替代结果门禁）。

---

## 2. 阈值与 cold_start 声明（任务 5）

`config/qc_thresholds.yaml` 新增两规则（与其他规则同结构：

```yaml
interaction_convergence:
  enabled: true
  source: cold_start
  calibrated_on: 0
  n_max_prompts: 15
  k_max_steps: 6
interaction_abandonment:
  enabled: true
  source: cold_start
  calibrated_on: 0
  min_abandonments: 1
```

**cold_start 未校准，显式声明**：`note` 字段注明"M6-a 采集 2026-11-12 起，
无回放语料；初值为量级假设，M6-f 决策回流后重算"（计划示例 N_max=15、
K_max=6 采为初值，仅供参考的来源已注明）。改阈值 = config diff，不散在
代码里；`calibrated_on: 0`（无 case 参与校准）。

---

## 3. 验收回放与互补性（任务 7）

### 3.1 真实全路径回放（不 mock 链路）

`tests/test_qc_interaction_e2e.py::TestReplayRealPath`：复用
TestCorrectSubmitE2E 的真实链路——correct→abandon→correct→submit HTTP 端到端，
真实 nnInteractive CPU 推理（48³ patch ~18s/次 ×3），**不 mock 该链路**；
仅 DB/storage 隔离到 tmp。真实 case（amos_0004 patch 源）缺失时 `skipif`。

断言（exit=0）：
- submit 后 `provenance.interactionMetrics["aorta"]`：abandonedAttempts=1、
  numPrompts=1（放弃后新序列）。
- `qc_series` 回放（最小 thresholds，只开两过程规则）：
  `interaction_abandonment` **flag**（普通类 aorta，放弃 1 次）；
  `interaction_convergence` 不 flag（numPrompts=1、convergenceSteps=None）。
  `rule_scores` detail：`flagged_classes=["aorta"]`、`worst_num_prompts=1`。

### 3.2 合成边界 case（任务 7a 分支覆盖）

- 单测（`tests/test_qc_interaction.py`，32 条，合成条目，不依赖推理）：
  两规则 × 普通/高风险 × numPrompts 上下界（15/16）× convergenceSteps
  上下界（6/7/99）× abandonedAttempts 0/1/2/3 × 纯自动 skipped × 阈值
  边界（恰 15/6 不 flag，16/7 flag）× finalLocalDice 不作判据 × 旧数据
  无标记 × 结构异常条目 × config 加载（source: cold_start、calibrated_on: 0、
  注记、阈值翻转、cfg 只读）。
- e2e 合成 seed（`TestSyntheticBoundary`，5 条，provenance 手工构造，
  sessionId 标注 `synthetic-*` 只用于分支覆盖、不冒充真实会话）：高风险
  放弃 flag（prostate teacherReliability=low）+ 高风险不看 steps +
  普通类超提示 flag + 无 provenance skipped + multi_teacher 互不干扰。
- 真实链路补两条：`test_replay_pure_auto_skipped`（seed teacher MV
  provenance=None——segmentation worker 落 MV 的真实形态，两规则 skipped）
  与互补性断言。

### 3.3 互补性数字（任务 7b）

真实回放 1 case（nifti-685473f2，48³ patch，交互 case 占比 1/1），落盘
`m6b_complementarity.json`（tmp）：

| 口径 | 规则集合 |
| --- | --- |
| 过程级 flag（最小 thresholds，只开两过程规则） | `interaction_abandonment` |
| 结果级 flag（全 thresholds） | `boundary_smoothness`, `interaction_abandonment` |
| 重叠 | 1（`interaction_abandonment`） |
| 仅结果级 | 1（`boundary_smoothness`） |
| 仅过程级 | 0（规则名级；最小 thresholds 是全 thresholds 子集，集合差恒空——口径见下） |

**可度量互补性**：结果级 flag 集合里既有过过程级 flag（重叠 ≥1）又有过程
级不覆盖的维度（`boundary_smoothness`——边界体素/体积比的体素统计，过程
信号不覆盖；48³ 合成 patch 边界/体积比超冷启动阈值 1.2 的正常结果）。两类
规则各提供对方没有的信号，**非完全重叠**。互补来自：abandonment 是事件级
过程信号（结果级规则看不到放弃事件），boundary_smoothness 是体素级统计
（过程级规则不看像素）。

### 3.4 样本量（任务 7c，M6-f DecisionTrace 未建）

回放语料规模：**1 case**（真实全路径 48³ patch），交互 case 占比 1/1。
合成边界 case：单测 32 条 + e2e 合成 5 条（分支覆盖用，不冒充真实会话）。
样本量小本身是事实；DecisionTrace 未建（任务书：本轮只在报告里记，不造
实体），冷启动阈值未经回放语料校准（§2）。

---

## 4. tracker 跨重启持久化（任务 6，选"做"）

M6-a 遗留 2（tracker 是进程内存缓冲，重启后草稿历史丢失）在本轮收口为
**标量状态恢复**：

- `interaction_metrics.restore_tracker_from_metrics(tracker, metrics)`：
  从落盘 provenance.interactionMetrics 恢复 `abandonedAttempts` /
  `numPrompts`（标量可诚实恢复；叠加语义——tracker 已有计数时累加；
  prompts 只在该类无草稿序列时恢复，record_draft 会覆盖；类型异常跳过，
  0 计数不进 dict）。
- `app._interaction_tracker`：首次为该 series 建 tracker 时从最新
  MaskVersion provenance 惰性恢复（`_restore_tracker`；DB 异常静默跳过，
  不挡采集主流程）。
- **不造假重建（诚实边界不变）**：草稿序列（drafts）不在落盘产物里，无法
  恢复；恢复后 `convergenceSteps` / `perStepDeltaDice` 从新草稿序列重算，
  重启前的草稿间 delta 如实缺失。
- 验收：e2e 跨重启（`test_abandon_survives_tracker_reset`，真实推理 ×2）
  ——进程 1 correct+abandon+submit（provenance 落 abandoned=1）→ 清空
  `_interaction_trackers`（重启模拟）→ 进程 2 abandon-draft 返回 **2**
  （恢复 1 + 本次 1）。单测 6 条（恢复/叠加/草稿优先/类型异常/noop/零计数）。

---

## 5. 测试与静态检查

- 新单测：`tests/test_qc_interaction.py` 32 条（含 deep-review W1 修复后的
  旧数据 prostate 兜底测试）+ e2e 7 条（合成 5 + 真实回放 2）+
  持久化单测 6 条 + 跨重启 e2e 1 条（新增/追加 46 条）。
- 全套 pytest：见 §5.1（后台跑，结果待填）。
- `ruff check` + `ruff format`（本轮 8 个改动文件）全绿；`qc.py` 存在
  HEAD 既有的 format 漂移（非本轮行，未动——surgical changes）。

### 5.1 全套 pytest 结果

全套 555 collected，**全绿（0 failed / 0 error）**。分两批跑（单次全套超
后台时限，38% 处被停一次后拆批重跑；两批合起来 = 全套无遗漏）：

| 批次 | 文件 | 结果 |
| --- | --- | --- |
| Batch 1（M6-b 受影响 + 许可门禁） | test_qc_interaction.py、test_qc_interaction_e2e.py、test_interaction_metrics.py、test_qc.py、test_qc_api.py、test_distill_license_gate.py | exit=0、100%、0 F/E（含真实推理 e2e：回放 2 条 + 跨重启 1 条 + Blocker 验收链路） |
| Batch 2（其余全量回归） | tests/ 其余全部（--ignore 上批 6 个） | exit=0、100%、0 F/E（476 tests，含 test_m6e_wiring 真实 mask 回归） |

注：pyproject addopts=-q 无尾行 summary，以 exit code + 点数 + 0 F/E 为准。
另见一条既有 deprecation warning（class-scoped fixture as instance method，
test_interaction_metrics.py real_patch——M6-a 既有，非本轮引入，未动）。

### 5.2 许可门禁负测试

`tests/test_distill_license_gate.py` 全绿（后台全套覆盖）；本轮未碰
`distill_data.py`，nnInteractive 修正产物不进蒸馏训练集的许可语义不变。

---

## 6. 提交 hashes

| 提交 | 内容 |
| --- | --- |
| `aa7f1c9` | feat(m6b): 两规则本体 + config 阈值 + run_qc/qc_series 接线 |
| `3028ce2` | test(m6b): 单测全分支（合成边界 case） |
| `9e15a5d` | fix(m6b): 两层逻辑语义校准——规则各管一维度，同事件不双 flag |
| `486c129` | test(m6b): 真实全路径回放 e2e + 合成边界 case |
| `6843acb` | fix(m6b): 回放 e2e 三处接线修正（storage 根统一 / pure-auto seed / 互补性口径） |
| `1b44b86` | feat(m6b): tracker 跨重启持久化——标量状态从 provenance 惰性恢复 |
| `bd5e208` | fix(m6b): deep-review W1/W2/W3 修复（高风险兜底可达 + 恢复可观测 + 并发守卫） |
| `2cdf623` | docs: M6-B-REPORT（规则实现/阈值/回放互补性/tracker 持久化/deep-reviewer findings/遗留） |
| `f066395` | docs: M6-B-REPORT 测试计数同步（32 单测 + 46 新增/追加） |
| `331b38d` | docs: M6-B-REPORT §5.1 全套 pytest 结果回填（555 collected 两批全绿 0 F/E） |

main HEAD：`331b38d`（46 ahead，未推，工作树干净）。

---

## 7. deep-reviewer findings

deep-reviewer 子代理（`/code-review --effort high` 等价）静态审查
`aa7f1c9..1b44b86`（Read/Grep，环境无 Bash 未能跑 pytest，断言一致性
抽样判断）。**Blocker：无。**

已核查正确的部分：两层逻辑（`hit = num_prompts > n_max or (not high_risk
and steps > k_max)`）、skipped 与汇总循环互斥（无同 key 覆盖）、
`_int_field` bool 排除、config 结构同模式、合成测试标注齐全。

| # | 级别 | 位置 | 结论 | 处置 |
| --- | --- | --- | --- | --- |
| 1 | Warning | qc_interaction.py:80 | is_high_risk 兜底不可达（条目内无 `__class__` 字段；run_qc 未传名单），旧数据无标记 prostate 按普通类误判 steps>k_max | **已修**（bd5e208）：签名改 `(cls, entry, 名单)` 按 key 判；run_qc 传显式名单（prostate + 39 低频，load_teacher_eval 口径）；补单测 |
| 2 | Warning | app.py:82 | _restore_tracker `except Exception: pass` 恢复失败全静默，漏报无信号 | **已修**（bd5e208）：异常范围缩到 DB 查询段 + stderr 警告（`[m6b] tracker 恢复失败`）；无 MV/provenance 仍静默（正常空态非异常） |
| 3 | Warning | app.py:39 | _interaction_tracker 惰性创建无锁，并发首访同 series 后写覆盖前写丢计数 | **已修**（bd5e208）：`threading.Lock` 双重检查（锁外快路径 + 锁内 setdefault 语义保留先建实例） |
| 4 | Nit | interaction_metrics.py:207 | restore 叠加语义生产路径只触发一次（仅新建 tracker），前提未文档化 | 记遗留（docstring 已写"新建 tracker 惰性恢复"，叠加分支为防御性，测试已断言语义） |
| 5 | Nit | qc_interaction.py:134 | abandonment 的 high_risk_classes 参数仅 detail 标注用 | 记遗留（签名对齐两规则同形，可接受设计） |

**Blocker 关闭情况**：无 Blocker；3 Warning 全部修复（bd5e208），2 Nit 记
遗留（§8.4/§8.5）。修复后快测子集（单测 32 + 合成 e2e 5 + 持久化 6）全绿。

---

## 8. 遗留

1. **阈值冷启动未校准**（本轮声明，非缺陷）：N_max=15 / K_max=6 /
   min_abandonments=1 为量级假设，无回放语料校准；M6-f DecisionTrace
   决策回流后重算（config diff，calibrated_on 更新）。
2. **草稿序列不可恢复**（诚实边界）：跨重启持久化只恢复标量
   （abandonedAttempts/numPrompts）；重启前的草稿间 delta（perStepDeltaDice）
   如实缺失，不从事后落盘产物重建。更细粒度（runner 内逐步 delta）留
   M7 特征工程（M6-E-A-REPORT §2.9 遗留 3）。
3. **restore 叠加语义为防御性**（deep-review Nit 4）：生产路径只对新建
   tracker 恢复一次，叠加分支（tracker 已有计数时 +=）不触发；保留为
   防御（若后续对非空 tracker 二次恢复，叠加语义避免覆盖丢计数）。
4. **abandonment 的 high_risk_classes 参数仅 detail 标注**（deep-review
   Nit 5）：签名对齐两规则同形，判据不依赖（abandonment 两层同判据）。
5. **qc.py HEAD 既有 format 漂移**：`ruff format` 对 qc.py 有非本轮行的
   重排建议（HEAD 就有，本轮未动）；下轮顺手或单独 style 提交收口。
6. **deep-reviewer 未跑 pytest**（环境无 Bash）：静态审查为主，断言
   一致性抽样判断；全套 pytest 结果见 §5.1 补充验证。
