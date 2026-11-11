# M6-BLOCKER-FIX-REPORT — M6 Blocker 修复交付报告

> 日期：2026-11-12。任务：M6-E-A-REPORT §3 收尾审查（deep-reviewer）发现的
> 2 Blocker / 2 Warning / 1 Nit + ruff format nit 修复 + deep-reviewer 复审。
> 非交互运行；全程未 push、未建 PR、未合并；未改 eval/holdout.txt 与 M4
> 门禁结果（ModelVersion gate_status 保持 passed）；nnInteractive 修正产物
> 不进蒸馏训练集（未碰 distill_data.py 许可门禁）；未改 SubagentStop hook。

---

## 1. 修复清单（每项一提交，本地 main）

| # | 级别 | 内容 | 提交 |
|---|---|---|---|
| 1 | Nit | convergenceSteps 判定基于原始 ΔDice、round(4) 只做展示舍入 | `8e80531` |
| 2 | Warning | 419 对回归删冗余首分支（`len(flagged)==len(holdout)`） | `090ce2b` |
| 3 | Warning | 419 对回归补 skipif（真实产物缺失时 skip 不 fail） | `cd572af` |
| 4 | format | student_pred.py / test_student_pred.py ruff format 对齐 | `b6293ca` |
| 5 | **Blocker 1** | correct 请求体补 `class_name`（tracker key 三端前端对齐）+ 全路径 e2e | `1652d48` |
| 6 | **Blocker 2** | 前端放弃路径接 abandon-draft（abandonedAttempts 生产可采集） | `fed25ee` |

## 2. Blocker 修复（核心）

### 2.1 Blocker 1：tracker key 前端不一致（`1652d48`）

**修复**：`review.html` `m5RunInference` 的 nninteractive-correct 请求体补
`class_name: m5ClassName(lv)`（语义类名）。后端按 `body.class_name` 记
tracker（缺省回退 `label-{n}` 不变），与 submit-nni 的 `acceptedClasses[0]`
（前端同一 `m5ClassName(lv)`）、abandon-draft 的 `class_name` 三端同一约定。

**选择理由**（首选修法）：前端 `m5ClassName(lv)` 是语义类名的单一来源
（`segState` 的 class name），correct/submit/abandon 三处都在前端有
`lv` 在手，补一处请求体字段即对齐；后端做 target_label→语义名映射则
需要新数据源（labels.json 读取 + 各端点接线），且 abandon-draft 的
`class_name` 仍是前端来的语义名——映射只在 correct 一端成立，约定反而
分裂。故选前端补字段。

**验收（真实链路无 mock）**：
`tests/test_interaction_metrics.py::TestCorrectSubmitE2E::
test_correct_to_submit_full_path_interaction_metrics`

- 全路径 HTTP 端到端：correct（前端 payload 形状，含 `class_name`）×2 →
  abandon-draft（`class_name`）→ correct → submit-nni（前端
  btn-submit-nni payload 形状：prompts 每条带 `class` +
  `acceptedClasses[0]` 语义名）
- **推理真实**：`correct_image` 真 nnInteractive CPU 推理（真实
  amos_0004 裁 48³ patch，肝区中心点提示；~18s/段 ×3 段），**不 mock
  该链路**（任务验收要求）；DB/storage 隔离到 tmp
- 断言：`interactionMetrics` 非空（Blocker 1 回归：correct 无 class_name
  时 tracker key 对不上即落空）、`finalLocalDice` 有值（0,1 真实区间——
  基线 ∪ 修正有交叠不重合）、`abandonedAttempts=1`、
  `teacherReliability=high`（aorta 命中教师 117 类表 30 cases）、
  第 1 步 delta=None / 第 2 步 delta 有值（草稿间语义）、DB roundtrip
  复核 provenance 真落 MaskVersion
- **实测：1 passed in 54.93s**（2026-11-12）

### 2.2 Blocker 2：abandon-draft 无前端调用方（`fed25ee`）

**修复**：`review.html` data-discard 放弃路径发
`POST /review/{series_id}/abandon-draft`（`target_label` + `class_name`
语义类名）；失败不挡本地放弃（本地状态已清，上报失败只 toast——过程
信号少一条，不阻断用户操作）。

**验收（真实链路无 mock）**：同 e2e 类两项：

1. `test_abandon_flow_via_endpoint_increments`：correct（真实推理，
   1 passed 内 19.47s）→ abandon-draft（前端 payload 形状）→
   `abandonedAttempts=1` → 重复放弃 `=2`（重复放弃同样计数，语义：
   放弃事件本身是过程信号）
2. 全路径测试中 abandon 后重新 correct → submit →
   `provenance.interactionMetrics.abandonedAttempts=1`（DB roundtrip
   复核）

**实测：2 项均 passed**（含于 33 passed，见 §3）。

## 3. Warning / Nit / format 修复

1. **Nit（`8e80531`）**：`interaction_metrics.py` `build_metrics` 改为
   基于原始 ΔDice 判定 `convergence_steps`、`round(4)` 只对展示列表做
   舍入——阈值边界（恰 0.01 附近）判定不因展示精度漂移。新增边界回归
   `test_metrics_convergence_judged_on_raw_delta`（delta=10/1005≈
   0.0099502：原始值 <τ、round 后 =0.01 不 <τ；旧实现得 None、新实现
   得 2）。
2. **Warning 冗余断言（`090ce2b`）**：`test_m6e_wiring.py:269` 删
   `len(flagged) == len(holdout) or` 首分支——全 30 例 flag 时下游
   symmetric_difference 断言（diff=25）仍 fail，首分支冗余；删后行为
   不变。
3. **Warning skipif（`cd572af`）**：419 对回归加
   `@pytest.mark.skipif(not _real_products_available(), ...)`——探测
   `downloads/amos22_subset`、`ct_lite/ts_class_map.json`、
   `ct_lite/ts_out`、`student_holdout/pred/dataset.json` 四项，缺失时
   SKIPPED 不误报 fail。实测：`HOME=/tmp/fakehome` →
   `SKIPPED [1] tests/test_m6e_wiring.py:244`（exit 0）；产物齐全时
   正常收集。
4. **format（`b6293ca`）**：`ruff format` 只对
   `src/bodydonor/student_pred.py` + `tests/test_student_pred.py` 对齐
   （前轮 af2e5b4/26aef88 文件）；仓库其余文件既有 format 状态不动。

## 4. 不修（任务指定）

- **SubagentStop hook**：post-review-to-pr.sh 对 background 子代理的
  findings 解析 SKIP 是已知现象，hook 本身行为正确——未修、未绕。

## 5. 最终验证数字（2026-11-12 实测）

- **全套 pytest**：**509 collected，全绿**（含 m6e_wiring 30 例真实 mask
  回归 + interaction_metrics 2 项真实推理 e2e）。分跑口径：
  - 全套 `.venv/bin/python -m pytest -q --tb=no`：exit 0，进度 dots
    750/0 F/E（509 collected 与 pytest --co 一致；738 快速 + 11 分摊
    慢项；summary 行被输出捕获截断，以 dots 计数 + exit code 为准）
  - tests/test_interaction_metrics.py 单文件：**33 passed in 73.27s**
    （31 快速 + 2 真实推理 e2e）
  - tests/test_student_pred.py：**5 passed**（format 无行为变化）
  - tests/test_m6e_wiring.py：**4 passed in 591.07s（0:09:51）**（3 快速
    接线单测 + 1 例 30-case 真实 mask 回归；skipif 修复 52bf0e0 后
    单文件复跑）
- **ruff check src tests scripts eval alembic config：All checks passed!**
- **ruff format**：本轮 5 个触碰文件已格式化（其余文件既有状态不动）
- 门禁与许可边界：未动 eval/holdout.txt、未动 M4 门禁结果、未碰
  distill_data.py 许可门禁；本轮全部提交 `git diff 8cefb1b..HEAD` 对
  eval/ 与 distill* 为空

## 6. 提交 hashes（本地 main，未推；基于 8cefb1b）

```
8e80531 fix(m6a): convergenceSteps 判定基于原始 ΔDice、round(4) 只做展示舍入
090ce2b test(m6e): 419 对回归删冗余首分支（len(flagged)==len(holdout)）
cd572af test(m6e): 419 对回归补 skipif（真实产物缺失时 skip 不 fail）
b6293ca style: student_pred.py / test_student_pred.py ruff format 对齐
1652d48 fix(m6a): correct 请求体补 class_name（tracker key 三端前端对齐）+ 全路径 e2e
fed25ee fix(m6a): 前端放弃路径接 abandon-draft（abandonedAttempts 生产可采集）
52bf0e0 test(m6e): skipif 探测补 per-case 学生预测 + 类别表前置（deep-reviewer 复审）
```

## 7. deep-reviewer 复审 findings

**复审方式**：`.claude/agents/deep-reviewer.md` 子代理（只读，tools 仅
Read/Grep/Glob），2026-11-12 执行。范围：本轮 6 提交
`git log 8cefb1b..HEAD`。

### 7.1 结论：无 Blocker（两个 Blocker 均关闭）

复审员核实（其报告原文要点）：

- **三端 key 约定一致（Blocker 1 关闭）**：app.py:1184（correct）、
  :1506（submit-nni 用 accepted[0]）、:1616（abandon-draft）三处 key
  约定一致；前端 review.html:830（correct 请求体 class_name）、:776
  （abandon-draft）、:970（submit-nni acceptedClasses）都用
  `m5ClassName(lv)`，与后端一致。缺省回退 `label-{n}` 只在前端不传时
  生效，当前前端三处都传。
- **data-discard async 改动正确（Blocker 2 关闭）**：本地状态变更都在
  第一个 await 之前同步执行，fetch 慢或失败不会造成 UI 状态错乱；
  fetch 失败只 toast 不阻断，符合设计。
- **收敛判定改动正确（Nit 关闭）**：interaction_metrics.py:174-175 收敛
  判定基于 raw_deltas，round(4) 只用于展示与 finalLocalDice。
- **删冗余分支行为不变（Warning 关闭）**：下游 symmetric_difference
  断言判定不受删除分支影响。
- **许可与合规**：本轮改动文件不涉及蒸馏门禁 / eval/holdout.txt / M4
  门禁产物；eval/holdout.txt 仅被 test_m6e_wiring 只读使用。

### 7.2 复审 findings（1 warning，已修）

**Warning**：`tests/test_m6e_wiring.py` `_real_products_available()` 只
检查教师产物 + `student_holdout/pred/dataset.json` 四路径，但回归测试
经 `load_student_prediction` 还依赖每个 hold-out case 的
`pred/<case_id>.nii.gz` 与 DB passed ModelVersion 的 weights_key 定位的
`nnUNet_raw/Dataset*/dataset.json`——部分缺失时 skipif 不触发，测试因
StudentPredictionError 以 fail 误报，与 `cd572af` 意图不符。

**已修（`52bf0e0`）**：探测补 eval/holdout.txt 各 case 的 pred 文件
存在性 + `load_student_prediction` 前置条件（StudentPredictionError
捕获转 skip 条件）。实测双向：产物齐全时正常收集（no skip output）；
`HOME=/tmp/fakehome` → `SKIPPED [1] tests/test_m6e_wiring.py:267`
exit 0。DB 前置只读（SELECT passed ModelVersion，真库
mask_versions=10 不变）。

**复审后复跑**：skipif 修复后 tests/test_m6e_wiring.py 单文件复跑
**4 passed in 591.07s**（含 30 例真实 mask 回归）。复审 findings 无
Blocker/Major，复审收敛（一轮修复后关闭）。

复审员同时注明的核实边界（未运行测试、未用 Bash 看 diff，按文件内容
核对；TestCorrectSubmitE2E 全文与测试数字断言的数值来源未逐行核对），
由本轮全套 pytest 实跑覆盖（§5）。
