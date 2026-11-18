# M7-S3P2 R4a-fix 轮次报告——⑥ check_task 准入规则纠偏（SA 是义务不是挡项）

2026-11-18。基线 R4a HEAD `a1632cc`，本轮 7 提交 `a95200b..6235ec2`（含本报告提交）。只做本修复；⑦ 另派不在本轮。

## 背景

R4a 把 `ready` 拆三字段时，`check_task` 准入写成 `weights_present and
class_map_present and not share_alike`——SA 成了挡项。协调方实测确认这是**政策
变化**不是忠实拆分：

- 旧 `ready=True` 的 nninteractive/interactive_correction（权重已部署、
  class_map 在位）拆分后被 `check_task` 拒绝，理由"挡项: SA 义务"；快照
  `tests/data/m2_decision_table.json` 的 nninteractive route 由
  `[True, "ok+license_inherits"]` 变为 `[False, "blocked:not-ready"]`。
- SA 是许可义务（⑤ 的设计：准入读 license_verified，义务按两轴保留），不是
  可用性——旧 ready=True 已把 SA 接受在内。忠实拆分应保持旧行为不变（零政策
  变化）；SA 是否该挡路由是用户才能定的政策问题，不由实现方悄悄改。
- 附带实测缺陷：拒绝原因模板固定带"checkpoint 未部署"前缀——nninteractive
  权重明明已部署，该前缀是假话。

## 提交列表

| 提交 | 内容 |
|---|---|
| `a95200b` | 失败测试先行——SA 是放行义务注记不作挡项（2 red） |
| `6531609` | check_task 准入纠偏——SA 是义务不是挡项 + 拒绝原因只列真实挡项 |
| `0969cdc` | 快照 nninteractive route 还原 `[True, ok+license_inherits]` |
| `3caae8f` | nninteractive_backend docstring SA 挡项叙述同步 |
| `e003495` | R4a 报告 §4/§2/状态表更正——原预期语义变更论证作废 |
| `a19345f` | 审查 Nit 1 修正（测试注释过时引用）+ 审查 findings 文件 |
| `6235ec2` | R4a-fix 轮次报告（本文件） |

diffstat（实测）：6 files, +137/-45。

## 修复逐项证据

### 1. check_task 准入规则（src/bodydonor/license_registry.py）

- 准入（:641-642）：改 `weights_present(e) and class_map_present(e)`——SA
  不再是挡项（旧 :644-646 `and not e["share_alike"]` 删除）。
- SA 义务注记（:673-678）：`ready` 过滤后取 `share_alike=True` 条目，放行原因
  `allowed（SA 义务：<output_license_inherits 条款逐条拼接>）`；无条款时
  兜底"派生物按保留条款保留"（`.get("output_license_inherits", [])` 安全读）。
- blocked_tasks 一票否决保留（:624-625，最前）；模态过滤路径不变（:628-639）。
- docstring 同步（:612-625 与 :26-29 模块 docstring）：SA 是许可义务不是
  可用性挡项（R4a-fix 忠实拆分零政策变化）。

### 2. 拒绝原因模板（src/bodydonor/license_registry.py:646-665）

- 逐项挡项只写真实挡项：`缺权重`（weights_present=False）、`缺 class_map`
  （class_map_present=False）；SA 不进拒绝原因（:651-656）。
- "checkpoint 未部署" 前缀仅当条目缺权重时出现（:657-659
  `any(not weights_present(e) for e in ok)` 条件化，旧版固定拼接删除）。
- 拒绝尾部就绪三分提示同步为 weights_present/class_map_present 两字段
  （SA 不再参与就绪判定）。
- 协调方三个实测反例闭合（本报告写作前实测）：
  - `interactive_correction`（PET）→ `True | allowed（SA 义务：CC-BY-NC-SA
    4.0（ShareAlike：派生物须同许可并传播署名））`——不再被拒，原因无
    "checkpoint 未部署"假话。
  - `body_mr`（MR）→ 拒绝 `(checkpoint 未部署；挡项: 缺 class_map/缺权重)`。
  - 占位 class_map 合成条目（权重已部署）→ 拒绝 `(挡项: 缺 class_map)`——
    无 checkpoint 前缀。

### 3. 快照还原（tests/data/m2_decision_table.json）

- nninteractive route `[False, "blocked:not-ready"]` →
  `[True, "ok+license_inherits"]`（还原提交 0969cdc）。
- 其余 35 条目零变化（对照脚本实测：`git diff a1632cc..HEAD --
  tests/data/m2_decision_table.json` 只剩 nninteractive route 2 行值变化；
  与 R4a 前正确基线 9a97790 逐条比对 changed = 0）。
- 格式保真：json indent=1、无尾换行与仓库原版一致（python json 写入对照
  git diff，中途 indent 不符曾现 433 行伪 diff，已修正为 2 行真 diff）。
- `distill: blocked:nni-correction` 是 9a97790（M7-S3P4）自身重分类，原值
  保留不动（实测 git show 9a97790 确认）。

### 4. 测试同步（tests/test_teacher_registry.py）

- 失败测试先行（a95200b）：`test_check_task_rejection_reason_names_blocked_item`
  SA 组改断言放行 + 原因含 `SA`/`ShareAlike` 义务注记；
  `test_check_task_rejection_reason_combinations_all_named` 组合挡项清单
  移除 SA（7 组合 → 6 拒绝组合），新增全 True 组合断言放行 + SA 义务注记。
- 缺权重/缺 class_map 拒绝路径断言不变（仍逐项报挡项）。
- 模块 docstring 与模态测试注释过时 SA 挡项引用同步（a95200b、a19345f）。

### 5. nninteractive_backend docstring 同步（src/bodydonor/nninteractive_backend.py:185-191）

check_task 准入纠偏使旧叙述"share_alike=True（check_task 会以 SA 义务挡）"
成为假话——改为"就绪三分齐（权重部署、class_map 在位；SA 是放行义务注记不作
挡项），check_task 对该任务实际放行"。W-7 已知缺口（生产修正路径不过
check_task）与数据侧蒸馏防线叙述不变。

## SA 挡路由的设计问题（请用户定）

已还原为零政策变化：准入只读 `weights_present and class_map_present`，SA
移入放行义务注记，旧 ready=True 行为保持。**SA 是否该挡路由是政策问题，请
用户定**——若用户决定 SA 应挡，需把挡项语义写进需求（当前实现为"义务注记
不挡"）；届时蒸馏防线（数据侧 sidecar source + ontology 判定）不受影响。

## 测试（数字实测）

- 失败测试先行：2 red（`test_check_task_rejection_reason_names_blocked_item`、
  `test_check_task_rejection_reason_combinations_all_named`）→ 修复后全绿。
- tests/test_teacher_registry.py：**31 passed**（修复后）。
- 受影响 6 文件合计 **150 passed**（test_teacher_registry +
  test_research_field_decision_table + test_license_whitelist +
  test_distill_license_gate + test_ontology + test_qc）。
- tests/test_nninteractive_backend.py 非 local_artifacts 面 **26 passed,
  4 deselected**（local_artifacts 用例涉真卡推理未跑，GPU 锁互斥）。
- ruff：`ruff check .` 全绿（"All checks passed!"，改动文件含其中）。
- 未跑全量套件（GPU 锁互斥；本项未触 GPU 路径代码）。
- 实测反例闭合脚本（Python -c 三断言）全部通过（见 §2）。

## 审查 findings 清单与处理

deep-reviewer 1 个（只读，无 shell；findings 见 M7-S3P2-R4a-fix-REVIEW.md）：

- **W1（实测复核：属实但生产不可达，不修）**：SA 条目放行后
  check_worker_task 也会放行 interactive_correction——grep src/ 全树实测：
  run_total（TS 教师路径）无任何 interactive_correction 调用点；
  check_worker_task 唯一生产调用在 segmentation.py:85（segment_series 派发），
  API（app.py:686 task 默认 "total"）与 CLI（cli.py segment --task 默认
  "total"）均不构造该任务，生产不可达。此为 R4a 政策返工的预期行为，非缺陷；
  SA 是否该在分发层单独挡已列入未决清单。审查者建议"若可达则在分发层单独
  拒绝或要求用户确认"——不可达，但用户确认保留为政策未决项。
- **Nit 1（属实已修，a19345f）**：模态测试注释"share_alike=False 使就绪三分
  不因 SA 挡 / SA 挡项另有测试"过时——改为"只为隔离 SA 义务注记影响"。
- **Nit 2（ruff 补跑：未触发）**：license_registry.py:83 空行数 E305——
  `ruff check .` 全绿（ruff 默认不启用 E305），不修。
- Blocker 0。
- 审查已核实无问题：blocked 一票否决位置、modality is None 分支、SA 义务
  注记取 ready 条目、`get("output_license_inherits", [])` 安全、prefix 与
  blocked 同源不矛盾、快照与 `_reason_key` 分类一致。
- 审查未完成核对项（审查者无 shell）由实施方补跑，全部通过：快照 diff
  2 行真 diff、受影响测试 150 passed、grep interactive_correction 生产调用
  不可达——见 REVIEW.md「实施方补跑实测」节。

## 未决清单

1. **SA 是否该挡路由**：已还原为零政策变化（义务注记不挡）；是否改回"SA 挡"
   由用户定（政策问题，本实现方不再动）。
2. **SA 条目经 check_worker_task 的可达性**：生产当前不可达（grep 实测），
   但门控语义上 TS 分发层现在放行 SA 条目——若用户决定 SA 应挡，此层需同步。
3. **license_verified 字段方案 / synthseg、moose 两轴取值 / vertebrae_body
   NC 框架消解论证 / synthseg、moose 蒸馏学生权重出站许可形态**（R4a 报告
   未决 1-4）：本轮未动，仍待用户/法务。
4. **快照 tests/data/m2_decision_table.json 历史字段名 research_distill**
   （R4a 报告未决 5）：保留；本轮 route 值还原一处（nninteractive），
   与 R4a 前基线 9a97790 一致。
5. **recalibration_ready（qc.py）**：数值重算抽检另一概念，本轮未动。
6. **⑦ 全仓清查**：另派，不在本轮。
