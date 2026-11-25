# M7-CONSISTENCY-IMPL-REPORT — 一致性基线固化 + 观察清单轮（实施报告）

分支 `feat/m7-consistency-baseline`，基线 `origin/main 421ef0c`。

## 提交列表与 diffstat

| commit | 内容 |
|---|---|
| `0685287` | feat(m7-consistency): 一致性基线固化 eval/consistency_baseline.json + 守卫测试 |
| `9b7e2c3` | docs(m7-consistency): 观察清单 docs/observation-list.md + 守卫测试 |
| `b905b73` | test(m7-consistency): M6-e 回归对比期望值接 consistency_baseline.json |

diffstat（origin/main..HEAD）：6 files changed, 319 insertions(+), 12 deletions(-)

- `0685287`：eval/.gitignore +1、eval/consistency_baseline.json +66、tests/test_consistency_baseline.py +117
- `9b7e2c3`：docs/observation-list.md +75、tests/test_observation_list.py +43
  （提交时 75 行；后随 N1 订正 `62ff819` 把一行行号引用拆成两行、净 +1，
  终态 `git diff --numstat 421ef0c..HEAD` = 76/0——分提交数字相加少 1 的原因）
- `b905b73`：tests/test_m6e_wiring.py +16/-12
- 另含后续订正提交 `1a2c295`（W1，+1/−1）、`62ff819`（N1，+2/−1），
  净 diff 合计 6 files +319/−12（非 3 个实施提交插入数直接相加的 318）

## A. 一致性基线固化 eval/consistency_baseline.json

**改动**：eval/consistency_baseline.json（新建，66 行）+ eval/.gitignore 例外 + tests/test_consistency_baseline.py（7 测试）。

**关键核实（写文件前完成）**：
- 源产物 `eval/student_teacher_consistency.json` 存在于本 worktree 但被
  eval/.gitignore 挡住（git ls-files eval/ 无此文件，eval/.gitignore 规则
  `*` 只放行脚本与 holdout.txt）——基线文件必须入仓，故加例外
  `!consistency_baseline.json`。
- **原始 419 对数值未保留**：生成脚本 eval/evaluate_student_holdout.py
  只 `scores.append(d)` 后即聚合（:277 `n_pairs=len(scores)`，逐对分数
  落盘前丢弃）；`eval/student_holdout_results.json` 的 cases 列表每 case
  仅 case_id/source/mean_dice/n_classes_scored/per_class_dice，两处
  teacher_student_consistency 块均为聚合统计。**结论：基线固化充分统计量
  （overall + per_class 15 类均值与 cases + below_080_classes + n_pairs），
  元数据 raw_pairs_retained=false 写明"原始 419 对数值未保留在源产物中，
  本文件固化的是其充分统计量"，绝不编造原始数值。**

**口径（meta/metric 段）**：教师 mask（TS total 输出，ts_class_map.json
按名对齐）vs 学生 mask（student_holdout/pred/<case_id>.nii.gz）逐类 3D
体素对称 Dice（2|T∩S|/(|T|+|S|)），只算两边任一非空的类，空对空不计入
n_pairs；overall mean/p10 为 419 对逐对分布统计量。来源：源产物文件 +
M4-REPORT.md:169（§1.5 表行）/:249-257（§3.5）；运行 2026-11-11。

**基线值**：mean 0.8560、p10 0.6986（主值）；median 0.9220、p25 0.8521、
min 0.0（对照）。regression_flag 段：mean 聚合、阈值 0.80、5 例
（s0209 0.1181、amos_0005 0.6592、s0338 0.7081、amos_0006 0.7567、
amos_0016 0.7862，来源 M4-REPORT.md:142-143 §1.5 阈值决策 + §3.4
:241-247，case id 与数值逐字核对）。

**测试**：tests/test_consistency_baseline.py 7 项（结构/overall/per_class
15 类/below_080/regression_flag/raw_pairs_note/对照源产物漂移即红——
源产物缺失时 skip 不 fail）。
- 先红证据：基线文件临时移走跑测试 → 6 failed（FileNotFoundError /
  断言失败）；恢复后 7 passed in 0.02s。

## B. M6-e 回归对比接基线

**改动**：tests/test_m6e_wiring.py（4 处，零逻辑改动到 flag 规则本身）：
- :5-7 docstring 改为基线文件出处；
- `_baseline_flagged_cases()`（:222-231）不再硬编码 5 例，改读
  `eval/consistency_baseline.json` regression_flag 段（单一事实来源）；
- `_real_products_available()`（:236-247）探测路径把旧源产物换成基线文件；
- 回归测试 `baseline_mean` 读取路径改 `["baseline"]["mean"]`（:289-292）。

**容差**：±1 例已由既有断言实现（`len(diff) <= 2` 即对称差 ≤1 例语义，
flag 数差 ≤1），断言信息写清差了哪几例（diff=sorted 列表）。flag 规则
本身（mean 聚合、阈值 0.80）未动。

**失败测试先红后绿证据**：
- 红：基线 regression_flag 故意改偏（amos_0006→amos_0004、数值伪造
  0.9999）跑 test_regression_419_pairs_flag_baseline。**本轮重做
  （2026-11-20，W2）**：临时 pytest 插件包装 run_qc 逐 case 记录实测
  结果（/tmp/w2probe/cases.jsonl）→ `1 passed in 733.00s (0:12:13)`
  ——红态下实测 flagged 5 例 = {s0209 0.1181、s0338 0.7081、
  amos_0005 0.6592、amos_0006 0.7567、amos_0016 0.7862}（30 例逐位
  与基线/审查探针一致），与改偏期望集对称差 = **{amos_0004,
  amos_0006} 两元素**；测试通过是因 diff=2 恰在 `len(diff) <= 2`
  容差边界内，非红态失效。**原记录（1 failed、diff 三元素含
  amos_0016）为误记**：本次红跑 amos_0016 正常 flag（0.7862 < 0.80），
  与审查两轮独立探针一致——确定性无矛盾，订正为实测两元素。
- 绿：全文件 4 测试 `4 passed in 596.36s (0:09:56)`——该跑在改偏之前，
  期望值已走基线文件读取路径（diff 实测：工作树基线与 A 提交
  0685287 逐字节一致，_baseline_flagged_cases 当时即读基线），即
  30 例真跑 flag 集合与基线 5 例对比通过（含 mean Dice 偏差 <0.02
  与 419 对基线均值核对）。单回归测试重跑绿证据被 600s 后台时限
  截断未取第二份，绿以 596.36s 全文件跑为准。

**注意**：test_m6e_wiring.py 全文件 4 passed in 596.36s（0:09:56），
其中 419 对回归为真实产物真跑（非 skip）。

## C. 观察清单 docs/observation-list.md

**改动**：docs/observation-list.md（新建，docs/ 目录本轮新建）+
tests/test_observation_list.py（4 测试）。

**五条（每条三段齐全：历史故障 → diff 触发条件 → 检查项），出处均亲读**：
1. **MaskVersion.source 列宽**：M4-REPORT.md:58/:300、M4-STAGE3-REPORT.md
   :112/:270；M5-e 修为 String(32)（src/bodydonor/models.py:144、
   M5-REPORT.md:81）。检查：source 值长度 ≤ 列宽，迁移与模型一致。
2. **target_label 计数**：commit f26693e 亲读（liver=44、GPU 验证非零
   16754 体素 sidecar 报 0、修为 (mask>0).sum()，src/bodydonor/
   nni_runner.py:222）。检查：二值 mask 一律 >0 计数。
3. **门禁口径**：M4-REPORT.md §3.1（:213-221，首轮 117 类 mean 0.1027
   FAIL 落库、27387c8 分口径修正、in-scope 0.8014 passed、scope_naive
   保留不删）。检查：评分子集与训练子集一致。
4. **测试污染 config**：M6-F-REPORT.md:164-170 Blocker（62e9e82 修复、
   759411f 回滚污染文件、calibrated_on 20→22 sha256 变化）。检查：
   测试前后真配置 sha256 不变，写操作走临时副本。
5. **s0209 方向矩阵**：M4-REPORT.md:115/:173/:374-376（max|cos|=1.2e-4、
   SVD 偏差 0.00011mm、QR 3.28mm 不可用；实现 eval/
   evaluate_student_holdout.py:135、检查 src/bodydonor/nifti_geom.py）。
   检查：非正交走 SVD 不走 QR，偏差有断言。

**门槛**：同类问题两个 PR 出现过才固化进硬规则；条目 3/4 目前各一次，
先记清单不进硬规则；条目 1/2/5 修复 commit 逐条 git log --grep 核实
（条目 1 仅 539ffe6、条目 2 仅 f26693e、条目 5 仅 3e4674d + M4 存量），
均为单次发现，只写核实过的次数。

**测试**：tests/test_observation_list.py 4 项（存在/五条标题/三段齐全
×5/门槛句）。先红后绿：文件移走 4 failed → 恢复 4 passed in 0.02s。

## 全量验证

- 全量 pytest：`python -m pytest tests/ --junitxml=m7-consistency-impl-fulltest-junit.xml -p no:warnings`（PYTHONPATH 指本 worktree src，实跑前 `import bodydonor` 核对指向 worktree）→ **759 passed, 0 failed, 0 errors, 0 skipped, 3022.21s (0:50:22)**（junit 实测 tests=759 failures=0 errors=0 skipped=0；日志 /home/gogvf/work/bodydonor-plans/consistency-impl-fullrun.log，JUnit 与日志留盘）。
- 首两次全量启动被 600s 后台时限杀掉，第三次以 2h 时限重启后完成（教训：本仓全量含 30 例真跑批，50 分钟量级，须长时限）。
- ruff：`ruff check src tests` → **All checks passed!**

## 未决清单

- B 项单回归测试重跑绿证据被 600s 后台时限截断未取第二份——绿以
  596.36s 全文件跑为准（期望已走基线读取路径，基线与 A 提交逐字节
  一致）；全量 759 passed 含该回归测试通过，二次确认。
- test_review_session.py:6 docstring 仍引用旧源产物文件名
  （eval/student_teacher_consistency.json）——纯 docstring 不读文件，
  不在本轮范围（surgical changes），如实记录不动。
- eval/evaluate_student_holdout.py:532 仍写旧源产物路径——生成脚本，
  本轮不改（surgical changes）。
- 观察清单条目 3/4 出现次数各 1：按门槛不进硬规则，若后续轮再出现
  同类问题应升级为硬规则。
