# M7-CONSISTENCY-W2N2-REPORT — W2 红实验重做 + N2 diffstat 订正 + 受影响 3 文件重跑

worktree `~/work/BodyDonor-consistency`，分支 `feat/m7-consistency-baseline`，
起点 HEAD `62ff819`（基线 `origin/main 421ef0c`）。本报告未提交（协调方统一提交）。

## 环境预检

- HEAD `62ff819`，git status 干净（仅 4 个未跟踪报告文件 + junit）。
- 基线 sha256（改前）：`ba48162ac30607043ffa0ead95f8e7a63fe4a1f36ac671a5b459fd0c3b409730`
  （与任务书期望一致）。逐字节副本 `~/work/bodydonor-plans/consistency-w2probe/baseline_pristine.json`。
- worktree `bodydonor.db`（156.0K）与 `eval/*.json` 齐全（coldstart_results /
  consistency_baseline / holdout_results / student_holdout_results /
  student_teacher_consistency），无 skipif 静默跳过风险。
- 导入指向实测：`PYTHONPATH=<worktree>/src` → `import bodydonor` 解析到
  `<worktree>/src/bodydonor/__init__.py`（非主 checkout，非 venv editable 指向）。
- pytest 用主 checkout venv `/home/gogvf/work/BodyDonor/.venv/bin/python`
  （pytest 9.2.0，`-q` 无尾行摘要，以 exit code + 点数 + JUnit 为准）；
  ruff 同 venv `bin/ruff`（PATH 无 ruff，rtk bypass 报 spawn 失败——用绝对路径）。

## W2 — 红实验重做（B 红证据核对）

按 FIX-RECORD W2 节同一改法逐字复现：

1. 基线逐字节副本 + sha256（见环境预检）。
2. 改偏 regression_flag：`amos_0006→amos_0004`，全部数值伪造 0.9999。
   `git diff --numstat` = **5/5**（恰 5 行，符合任务要求）。
3. 临时 pytest 插件 `~/work/bodydonor-plans/consistency-w2probe/w2_probe_plugin.py`
   （`-p w2_probe_plugin` 加载，插件目录加入 PYTHONPATH，不进仓）：
   包装 `bodydonor.qc.run_qc`（模块属性替换，`_w2_wrapped` 幂等标记），
   逐 case 追加 series_id/mean_dice/flagged 到 `cases.jsonl`；
   `pytest_sessionstart` 装包装 + 清上一轮残留，`pytest_runtest_logstart` 再保险（幂等）。
   首跑教训：`-p w2_probe_plugin` 不带插件目录在 PYTHONPATH 时报
   `No module named 'w2_probe_plugin'`（exit 4 = usage error，测试未跑、非红态失效）。
4. 跑 `tests/test_m6e_wiring.py::test_regression_419_pairs_flag_baseline`：

   **`1 passed`（exit 0，junit tests=1 failures=0 errors=0 skipped=0，
   time=606.267s ≈ 0:10:06）**。日志 `w2_red.log`（-q 摘要行未落盘，
   以 exit code + 点数 + JUnit 为准）、JUnit `w2_red_junit.xml` 留盘。

5. 30 例逐位结果（cases.jsonl，
   `~/work/bodydonor-plans/consistency-w2probe/cases.jsonl`）：

   | case | mean_dice | flagged | | case | mean_dice | flagged |
   |---|---|---|---|---|---|---|
   | amos_0001 | 0.8695 | False | | s0045 | 0.9288 | False |
   | amos_0004 | 0.8439 | False | | s0071 | 0.9407 | False |
   | amos_0005 | 0.6592 | **True** | | s0147 | 0.9330 | False |
   | amos_0006 | 0.7567 | **True** | | s0209 | 0.1181 | **True** |
   | amos_0007 | 0.9207 | False | | s0319 | 0.9305 | False |
   | amos_0009 | 0.8967 | False | | s0321 | 0.8926 | False |
   | amos_0010 | 0.8493 | False | | s0338 | 0.7081 | **True** |
   | amos_0011 | 0.8939 | False | | s0392 | 0.9249 | False |
   | amos_0014 | 0.9197 | False | | s0447 | 0.8427 | False |
   | amos_0015 | 0.9067 | False | | s0495 | 0.8771 | False |
   | amos_0016 | 0.7862 | **True** | | s0556 | 0.8705 | False |
   | amos_0017 | 0.9197 | False | | s0662 | 0.9442 | False |
   | amos_0019 | 0.9066 | False | | s0723 | 0.9107 | False |
   | amos_0021 | 0.8265 | False | | s0797 | 0.8605 | False |
   | amos_0023 | 0.8971 | False | | s0847 | 0.8368 | False |

   flagged 5 例 = {s0209 0.1181、amos_0005 0.6592、s0338 0.7081、
   amos_0006 0.7567、amos_0016 0.7862}——30 例逐位与 FIX-RECORD W2 节
   及审查两轮探针记录一致。

6. 对称差（实测 flagged 集 vs 改偏期望集 {s0209, amos_0005, s0338, amos_0004, amos_0016}）：
   **= {amos_0004, amos_0006} 恰两元素**（单键替换的预期值；实测
   amos_0004 mean 0.8439 ≥ 0.80 未 flag、amos_0006 mean 0.7567 < 0.80
   flag，改偏只动期望集不动 flag 判定——阈值 0.80 硬编码在 MIN_THRESHOLDS）。

7. 恢复基线副本，sha256 复核：
   `ba48162ac30607043ffa0ead95f8e7a63fe4a1f36ac671a5b459fd0c3b409730`
   （与改前逐字节一致），`git diff` 干净（`git status eval/` 无改动）。

8. 结论：**红实验复得两元素 diff**——测试 passed 是因 diff=2 恰在
   `len(diff) <= 2` 容差边界内（非红态失效、非非确定性）；amos_0016 本次
   红跑正常 flag（0.7862 < 0.80，距阈值 0.0138），三元素记录确证为误记。
   确定性无矛盾，按任务要求**不动 test_m6e_wiring.py 的 docstring**。

对照 IMPL-REPORT §B 红证据段：与实测**一致**（FIX-RECORD W2 重做记录的
时间 2026-11-20、实测 5 例值、两元素结论均与本轮实测逐位吻合；
`/tmp/w2probe/cases.jsonl` 证据已随 WSL2 迁移清空，本轮以
`~/work/bodydonor-plans/consistency-w2probe/` 持久目录重做并留新证据）——
按任务要求**不动 §B**。

## N2 — 实施报告 diffstat 订正

- 实测 `git diff --stat 421ef0c..HEAD` = **6 files changed, 319 insertions(+),
  12 deletions(-)**（与协调方实测一致，非 318）。
- 实测 `git diff --numstat 421ef0c..HEAD`：
  observation-list.md **76**/0、.gitignore 1/0、baseline 66/0、
  test_consistency_baseline 117/0、test_m6e_wiring 16/12、test_observation_list 43/0
  （与任务书 numstat 逐项一致）。
- 318 的来源核实：3 个实施提交插入数直接相加 = 184+118+16 = 318；漏了
  `1a2c295`（W1，+1/−1）与 `62ff819`（N1，+2/−1）对净 diff 的影响；
  observation-list 终态 76 = 75+2−1（N1 把一行行号引用拆成两行、净 +1）。
  分提交 numstat 逐 commit 实测（git show --numstat）与报告自述一致。
- 改动：`M7-CONSISTENCY-IMPL-REPORT.md` diffstat 段 → 6 files +319/−12，
  补 75→76 原因句（N1 拆行净 +1）+ 两个订正提交说明行。

## 受影响 3 文件重跑 + ruff

跑法：主 checkout `.venv/bin/python -m pytest`，PYTHONPATH 指 worktree `src`，
cwd 为 worktree。

- `tests/test_consistency_baseline.py`：**7 passed**（exit 0，7 dots [100%]，
  秒级）。
- `tests/test_observation_list.py`：**4 passed**（exit 0，4 dots [100%]，
  秒级）。
- `tests/test_m6e_wiring.py` 全文件：**4 passed**（exit 0，耗时 595s ≈
  0:09:55；JUnit `w2_green_junit.xml` 实测 tests=4 failures=0 errors=0
  skipped=0 time=594.130s；日志 `wiring_green.log`，-q 摘要行未落盘，
  以 exit code + 点数 + JUnit 为准）。其中 419 对回归为真实产物真跑
  （testcase `test_regression_419_pairs_flag_baseline` time=590.731s，
  非 skip；3 个接线单测 0.421/0.021/0.019s）。
- `ruff check src tests`（主 checkout `.venv/bin/ruff`）：**All checks
  passed!**（exit 0）。

汇总：3 文件重跑 **7 + 4 + 4 = 15 passed、0 failed、0 skipped**，
ruff 全绿。全量 pytest 未跑（由协调方独立重跑，GPU 锁串行）。

## 交付清单

- 新报告：`M7-CONSISTENCY-W2N2-REPORT.md`（本文件，未提交）。
- 改动文件：`M7-CONSISTENCY-IMPL-REPORT.md`（N2 订正；§B 红证据段
  与实测一致，**未改动**）。
  `tests/test_m6e_wiring.py` docstring：**未改动**（diff=2 确定性结果，
  无未解释不确定性需要声明）。
- 探针与产物（持久目录，不进仓）：
  `~/work/bodydonor-plans/consistency-w2probe/{w2_probe_plugin.py, cases.jsonl,
  analyze_w2.py, w2_red_junit.xml, w2_red.log, baseline_pristine.json,
  wiring_green_junit.xml, wiring_green.log}`。
- `M7-CONSISTENCY-FIX-RECORD.md` 未改（上一轮记录，保持原样）。

## 最终 git 状态

- 工作树仅：`M eval/consistency_baseline.json` 已逐字节恢复（sha256
  `ba48162ac306…409730` 与 HEAD 版本一致，git diff 干净）、未跟踪报告
  文件（FINDINGS/IMPL-REPORT/FIX-RECORD/W2N2-REPORT + junit）。src/、
  config/、tests/、docs/ 零改动，未 commit、未 push。
