# M7-CONSISTENCY-FIX-RECORD — 审查 findings 修复轮（W1/W2/N1/N2）

worktree `~/work/BodyDonor-consistency`，分支 `feat/m7-consistency-baseline`，
起点 HEAD `b905b73`（基线 origin/main `421ef0c`）。逐项修复、单独提交、fail-first 照旧。

## W1 — 基线 meta.run 构成订正（commit `1a2c295`）

- finding：`eval/consistency_baseline.json:5` meta.run 写「CT-Lite 21 例 +
  AMOS 9 例」为误读（M4-REPORT.md:107 耗时区间当例数）。
- 亲证证据：`eval/holdout.txt` 第二列分组计数（awk sort uniq -c）→
  **15 amos + 15 ts_public**，与生成脚本 evaluate_student_holdout.py:4 自述一致。
- 修：只改 meta.run 构成段为「CT-Lite 15 例 + AMOS 15 例」，其余文字与
  **基线数值零改动**（git diff 1 行）。
- 验证：tests/test_consistency_baseline.py **7 passed**（守卫不断言该文本，实跑确认）。

## W2 — 红实验重做，对称差订正为两元素（报告订正，无代码提交）

- finding：实施报告 §B 红证据记 `1 failed` + `diff=['amos_0004','amos_0006','amos_0016']`
  三元素，与审查两轮独立探针（amos_0016 确定性地 flag）矛盾。
- 重做（2026-11-20）：
  1. 基线先存逐字节副本（sha256 `ba48162ac306…`）；
  2. 改偏 regression_flag（amos_0006→amos_0004 + 全部数值伪造 0.9999，
     与原红实验同一改法，git diff 恰 5 行）；
  3. 临时 pytest 插件（/tmp/w2probe/w2_probe_plugin.py，-p 加载不进仓）包装
     bodydonor.qc.run_qc 逐 case 记 mean_dice/flagged 到 cases.jsonl
     （测试函数体运行时 `from bodydonor.qc import run_qc` 取模块属性，包装生效）；
  4. 跑 test_regression_419_pairs_flag_baseline → **`1 passed in 733.00s
     (0:12:13)`**；
  5. 实测 30 例逐位记录，flagged 5 例 = {s0209 0.1181、s0338 0.7081、
     amos_0005 0.6592、amos_0006 0.7567、amos_0016 0.7862}；
  6. **对称差 = {amos_0004, amos_0006} 恰两元素**（单键替换的预期值）。
  7. 恢复基线副本，sha256 复核一致，`git diff` 干净（逐字节恢复）。
- 结论：测试 passed 是因 diff=2 恰在 `len(diff) <= 2` 容差边界内（非红态
  失效、非非确定性）；amos_0016 本次红跑正常 flag（0.7862 < 0.80），三元素
  记录确证为误记。确定性无矛盾。
- 订正：M7-CONSISTENCY-IMPL-REPORT.md §B 红证据段改写为实测两元素 + 重做
  时间 + 实测值（该报告保持未提交，由协调方随终报提交）。
- 红实验本身不留改动：基线逐字节恢复、无 docstring 变更（diff=2 是确定性
  结果，无未解释不确定性需要声明）。

## N1 — 观察清单行号订正（commit `62ff819`）

- finding：docs/observation-list.md 条目 2 引 nni_runner.py:222 落 docstring 区；
  实现定义 :218、`(arr > 0).sum()` 在 :227。
- 修：改为「src/bodydonor/nni_runner.py:227（函数 `_count_target_voxels`
  定义在 :218-227）」。
- 验证：tests/test_observation_list.py **4 passed**。

## N2 — 实施报告 diffstat 订正（报告订正，无代码提交）

- finding：实施报告 diffstat 段「5 files changed, 318 insertions(+)」；
  实测 `git diff --stat 421ef0c..HEAD` = **6 files changed, 318 insertions(+),
  12 deletions(-)**。
- 修：改为 6 files +318/−12（该报告保持未提交，由协调方随终报提交）。

## 修复后验证

- 受影响 3 文件：tests/test_consistency_baseline.py（**7 passed**）、
  tests/test_observation_list.py（**4 passed**，两文件合并跑 11 passed）、
  tests/test_m6e_wiring.py 全文件重跑 → **exit 0、4 dots、[100%]（4 测试
  全通过；`-q` 摘要行未落盘，日志 /tmp/w2probe/wiring_green.log 仅 80B；
  同回归红态单测 733.00s、impl 轮绿态全文件 596.36s 参照，本轮与 shadow
  协调方全量同机并行故更慢）**。
  其中 419 对回归为真实产物真跑（4 dots 含该测试，非 skip）。
- ruff check src tests → **All checks passed!**
- 基线 sha256 复核 `ba48162ac306…`（W1 提交后值），`git diff` 干净。
- 未跑全量（由协调方独立重跑）。

## 未决清单

- W2/N2 的报告订正落在 M7-CONSISTENCY-IMPL-REPORT.md，该文件按指示保持
  未提交，由协调方随终报一并提交。
- 实施报告原未决 3 项（test_review_session.py:6 旧引用、
  evaluate_student_holdout.py:532 旧路径、观察清单条目 3/4 升级门槛）不变，
  本轮不触碰。
