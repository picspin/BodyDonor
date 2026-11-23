# M7-CONSISTENCY-BASELINE-REPORT — 一致性基线固化 + 观察清单轮（终报）

分支 `feat/m7-consistency-baseline`，worktree `~/work/BodyDonor-consistency`，
基线 `origin/main 421ef0c`。**未 push、未 merge、未开 PR**（合并由用户本人做）。
本轮为验证框架缺口三（差分测试基线）的落点：一致性基线固化 + 观察清单。

## 提交列表

| commit | 内容 |
|---|---|
| `0685287` | A：一致性基线固化 eval/consistency_baseline.json + 守卫测试 |
| `9b7e2c3` | C：观察清单 docs/observation-list.md + 守卫测试 |
| `b905b73` | B：M6-e 回归对比期望值接 consistency_baseline.json |
| `1a2c295` | W1：基线 meta.run 构成订正（见下） |
| `62ff819` | N1：观察清单条目 2 行号订正（见下） |
| （本轮报告提交） | 终报 + IMPL/REVIEW 两报告入仓 |

## Diffstat（已订正，N2）

`git diff --stat 421ef0c..HEAD` = **6 files changed, 319 insertions(+), 12 deletions(-)**。

| 文件 | +/− |
|---|---|
| docs/observation-list.md | 76/0 |
| eval/.gitignore | 1/0 |
| eval/consistency_baseline.json | 66/0 |
| tests/test_consistency_baseline.py | 117/0 |
| tests/test_m6e_wiring.py | 16/12 |
| tests/test_observation_list.py | 43/0 |

**注意：不是 318。**318 是把 3 个实施提交的插入数直接相加（184+118+16），
漏了 W1（`1a2c295`，+1/−1）与 N1（`62ff819`，+2/−1）对净 diff 的影响；
observation-list 终态 76 = 75（`9b7e2c3` 提交时）+2−1（N1 把一行行号引用
拆成两行、净 +1）。**任务书给的 N2 目标值 318 本身是旧数**（审查时 HEAD
还是 `b905b73`，当时确为 318；W1/N1 提交后净值变为 319）——本轮按实测
319 订正，三方独立实测一致（协调方、实施方、审查方）。

## W1 与 N1（已提交）

- **W1**（`1a2c295`）：`eval/consistency_baseline.json:5` meta.run 由
  「CT-Lite 21 例 + AMOS 9 例」订正为「CT-Lite 15 例 + AMOS 15 例」
  （证据：holdout.txt 与 student_holdout_results.json 均 15 amos + 15
  ts_public）。基线数值零改动。守卫测试 7 passed。
- **N1**（`62ff819`）：`docs/observation-list.md:26` 行号 :222→:227
  （`_count_target_voxels` 定义 :218、`(arr > 0).sum()` 在 :227）。
  守卫测试 4 passed。

## W2 — B 红实验重做（本轮，diff=2 确证）

上一轮审查 finding 2（warning）：实施报告 §B 红证据记 `1 failed` +
`diff=['amos_0004','amos_0006','amos_0016']` 三元素，与审查两轮独立探针
（amos_0016 确定性地 flag）矛盾。本轮在持久目录重做（上一轮 /tmp 证据
已随 WSL2 迁移清空，本轮证据在 `~/work/bodydonor-plans/consistency-w2probe/`）：

1. 基线逐字节副本 + sha256（`ba48162ac306…409730`）。
2. 改偏 regression_flag：`amos_0006→amos_0004`，全部数值伪造 0.9999；
   `git diff --numstat` = 5/5（恰 5 行）。
3. 临时 pytest 插件包装 `bodydonor.qc.run_qc`，逐 case 记 mean_dice/flagged
   到 `cases.jsonl`（插件不进仓）。
4. 跑 `tests/test_m6e_wiring.py::test_regression_419_pairs_flag_baseline`：
   **`1 passed`（exit 0，JUnit tests=1 failures=0 errors=0 skipped=0，
   time=606.267s）**。
5. 30 例逐位实测：flagged 5 例 = {s0209 0.1181、s0338 0.7081、amos_0005 0.6592、
   amos_0006 0.7567、amos_0016 0.7862}，与基线/审查探针逐位一致；
   amos_0004 实测 0.8439 ≥ 0.80 未 flag。
6. 对称差（实测 flagged 集 vs 改偏期望集）= **{amos_0004, amos_0006} 恰两元素**。
7. 恢复基线副本，sha256 复核与改前一致，`git diff` 干净。

结论：测试 passed 是因 diff=2 恰在 `len(diff) <= 2` 容差边界内（非红态失效、
非非确定性）；amos_0016 本次红跑正常 flag（0.7862 < 0.80，距阈值 0.0138），
三元素记录确证为误记。确定性无矛盾，**测试 docstring 未动**（无未解释
不确定性需要声明）；IMPL-REPORT §B 与实测一致，**未动**。

协调方独立核实：亲读 `cases.jsonl` 30 行（值与上表逐位一致）、红实验 JUnit
（tests=1，time=606.267）、基线 sha256（`ba48162a…409730`，恢复后与改前一致）、
`git status`（tracked 零改动）。

## N2 — 实施报告 diffstat 订正（本轮）

`M7-CONSISTENCY-IMPL-REPORT.md` diffstat 段改为 6 files +319/−12，并补
75→76 原因句（N1 拆行净 +1）与 W1/N1 订正提交说明行。协调方、实施方、
审查方三方独立实测均为 319。

## 受影响 3 文件重跑 + ruff（本轮）

跑法：主 checkout `.venv/bin/python -m pytest`，PYTHONPATH 指 worktree `src`，
cwd 为 worktree（worktree 有 bodydonor.db 与 eval/*.json，实跑非 skip）。

| 文件 | 结果 |
|---|---|
| tests/test_consistency_baseline.py | 7 passed（exit 0，秒级） |
| tests/test_observation_list.py | 4 passed（exit 0，秒级） |
| tests/test_m6e_wiring.py 全文件 | 4 passed（exit 0，594.130s；JUnit tests=4；419 对回归 testcase 590.731s，真实产物真跑非 skip） |
| ruff check src tests | All checks passed!（exit 0） |

合计 **15 passed / 0 failed / 0 skipped**。协调方独立重跑两个快文件：
**11 passed in 0.03s**（JUnit tests=11 failures=0）；亲核 wiring_green JUnit
（tests=4 failures=0 errors=0 skipped=0 time=594.130）。

## 全量验证（三方一致 759）

| 轮次 | 结果 | 耗时 |
|---|---|---|
| 实施方 | 759 passed, 0 failed, 0 errors, 0 skipped | 3022.20s |
| **协调方独立重跑（本轮）** | **759 passed, 0 failed, 0 errors, 0 skipped** | **2708.89s（0:45:08）** |

协调方全量：主 checkout `.venv/bin/python -m pytest tests/ -p no:warnings`，
PYTHONPATH 指 worktree `src`（导入亲核指向 worktree `src/bodydonor/__init__.py`），
JUnit `m7-consistency-suzi-fulltest-junit.xml`（tests=759 failures=0 errors=0
skipped=0 time=2708.887，testcase 759 个），日志
`consistency-my-fullrun.log`（尾行 `759 passed in 2708.89s`），回归单测
`test_regression_419_pairs_flag_baseline` time=586.110s 通过。串行跑
（GPU 锁，未与他轮并行）。

## Deep-reviewer 验证（2026-11-23，本轮）

审查方独立验证 W2/N2 修复（只读审查，追加在
`M7-CONSISTENCY-REVIEW-FINDINGS.md` 末尾「W2/N2 修复验证」一节，上一轮
内容未动）：

- **W2 属实**：独立算得 flagged 5 例、对称差恰 {amos_0004, amos_0006}、
  amos_0016 0.7862 正常 flag，junit/log 与报告逐位一致，基线 sha256 逐字节
  恢复，`len(diff)<=2` 断言亲读确认（test_m6e_wiring.py:329）。
- **N2 属实**：独立实测 6 files +319/−12、numstat 逐项一致、75→76 解释成立、
  318 仅剩解释性提及、「5 files」残留 0。
- **不动项属实**（tests/ 自 b905b73 零 diff、§B 与 FIX-RECORD 一致）；
  **3 文件重跑与 ruff 可信**（审查方独立重跑 11 passed in 0.08s、ruff 全绿）；
  **范围外无越界改动**（tracked 零改动）。
- **新 findings：0**（Blocker 0 / Warning 0 / Nit 0）。
- **未证实观察：4 条**——①首跑红实验 usage error 无日志留证（见下“过程说明”）；
  ②FIX-RECORD「未改」无历史可比对（协调方注：本轮开始前已 scp 该文件到 VM，
  md5 `6fdcad5f…` 与现 worktree 一致，确证未改）；③2026-11-20 /tmp 首次重做
  不可复验（本轮 11-23 持久目录重做已确证 30 例逐位一致，结论不受影响）；
  ④改偏态 5/5 numstat 只能由对称差间接确证（基线已恢复，无法事后直接验证）。

## 过程说明（供父代理复核）

- **首跑红实验 usage error**：实施方首跑因插件目录不在 PYTHONPATH 报
  `No module named 'w2_probe_plugin'`（exit 4 = usage error，测试未跑），
  重跑后成功。该失败无日志留盘，仅报告文字记录；结论以成功重跑的 JUnit
  与 cases.jsonl 为准，不受影响。
- **Magpie 循环保护两次拦停实施方 turn**：实施方后台跑长测试、用相同命令
  轮询进度，8 次相同调用触发 Magpie 停 turn；agent 经后台任务完成通知
  自行恢复并完成全部工作。以后派长测试活应嘱咐：前台跑 + 长时限，不要紧轮询。
- **模型名过期**：WSL2 `~/.claude/settings.json` 仍指
  `litellm/mga-glm-5.3-flash[1m]` 与 `litellm/mga-claude-haiku-5-5[1m]`，
  但 Magpie `/v1/models`（55 个）已无 `[1m]` 变体；Claude Code 2.1.318 对
  `[1m]` 与无后缀名均报 `[claude-code:unrecognized_model]`，但运行仍能完成
  （本轮实施与审查均如此）。settings.json 属用户配置，本轮只在审查派遣时
  用 `--model` + env 覆盖，未改文件；建议父代理决定是否更新 settings.json。
- **WSL2 迁移后环境**：本轮跑在已迁移的发行版上（根盘 /dev/sdd 1007G，
  内存 27G、8 核、swap 8G，tailscaled 与 magpie-age 均 active）。

## 本轮入仓文件

- `M7-CONSISTENCY-BASELINE-REPORT.md`（本终报）
- `M7-CONSISTENCY-IMPL-REPORT.md`（N2 订正）
- `M7-CONSISTENCY-REVIEW-FINDINGS.md`（上一轮 4 findings + W2/N2 验证节）

未入仓（留 worktree 未跟踪，父代理定）：`M7-CONSISTENCY-FIX-RECORD.md`、
`M7-CONSISTENCY-W2N2-REPORT.md`、`m7-consistency-impl-fulltest-junit.xml`、
`m7-consistency-suzi-fulltest-junit.xml`（协调方全量 JUnit）。
证据目录 `~/work/bodydonor-plans/consistency-w2probe/`（不进仓）：
`w2_probe_plugin.py`、`cases.jsonl`、`analyze_w2.py`、`w2_red_junit.xml`、
`w2_red.log`、`baseline_pristine.json`、`wiring_green_junit.xml`、
`wiring_green.log`；协调方全量日志 `consistency-my-fullrun.log`。
以上报告、JUnit、日志均已同步到 VM `~/workspace/bodydonor/`。

## 未决清单（与实施报告一致，本轮不触碰）

- `tests/test_review_session.py:6` docstring 仍引用旧源产物文件名
  （纯 docstring 不读文件）。
- `eval/evaluate_student_holdout.py:532` 仍写旧源产物路径（生成脚本）。
- 观察清单条目 3/4 出现次数各 1：按门槛不进硬规则，若后续轮再出现
  同类问题应升级为硬规则。
- B 项单回归测试重跑绿证据以 596.36s 全文件跑为准（本轮 wiring_green
  594.130s 为第三份绿证据）。

## 下一步

- 等用户合（PR/merge 由用户本人做）；S4 未批。
- 下一一致性工作（若有）：按观察清单门槛，条目 3/4 再出现同类问题时升级硬规则。
