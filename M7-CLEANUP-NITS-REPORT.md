# M7-CLEANUP-NITS-REPORT — main 自带两 nit 小清理（2026-11-19）

轮次目标：清理 S2 rebase 轮 deep-reviewer 找出的 2 个 nit（经核实均为 main 自带，用户 2026-11-19 定 ①② 都清理）。只做这两处，不顺手重构、不碰相邻代码、不改任何行为。

- worktree：~/work/BodyDonor-cleanup，分支 feat/m7-cleanup-main-nits，基线 origin/main 421ef0c
- 未 push、未 merge、未开 PR（按纪律）

## 提交列表（3 提交，全实测 diffstat）

| 提交 | 内容 | diffstat |
|------|------|----------|
| 636bcc2 | fix(m7-cleanup): ① distill_data.__all__ 去重 ALLOWED_TRAIN_SOURCES + 守卫测试先红后绿 | src/bodydonor/distill_data.py 1(-) · tests/test_all_no_duplicates.py 11(+) · 2 files, +11/-1 |
| 5e5835a | fix(m7-cleanup): ② qc_priority 低频阈值 <10 改用 MIN_PER_CLASS（行为零变化）+ 边界回归守卫 | src/bodydonor/qc_priority.py 6(2-/4+) · tests/test_m6c_priority.py 9(+) · 2 files, +13/-2 |
| b3bf151 | test(m7-cleanup): 审查 2 nit 修复——守卫加条目存在性断言 + 边界测试显式锁定 MIN_PER_CLASS==10 | tests/test_all_no_duplicates.py 2(+) · tests/test_m6c_priority.py 3(+) · 2 files, +5/-0 |

合计 vs origin/main：4 files, +29/-3（src/bodydonor/distill_data.py -1 · src/bodydonor/qc_priority.py +6-2 · tests/test_all_no_duplicates.py +13 · tests/test_m6c_priority.py +12）。工作树 clean。

## ① `__all__` 重复条目 — 逐项证据

- 位置：src/bodydonor/distill_data.py:645-647，`__all__` 里 `"ALLOWED_TRAIN_SOURCES"` 连续出现两次；删一行，:55 常量定义 `frozenset({"total", "manual-correction"})` 不动。行为零变化。
- 测试（先红后绿，tests/test_all_no_duplicates.py::test_distill_data_all_no_duplicates）：
  - 修前跑红：`FAILED tests/test_all_no_duplicates.py::test_distill_data_all_no_duplicates`（重复条目命中 `len(__all__) == len(set(__all__))` 断言）。
  - 修后跑绿：tests/test_all_no_duplicates.py + tests/test_distill_license_gate.py 合跑 64 passed（`[100%]`，2 files）。
- 落点说明：未放 tests/test_distill_license_gate.py（该文件 39.6K 全是许可门禁主题，`__all__` 卫生与其主题不符），新建独立小文件 tests/test_all_no_duplicates.py（13 行）。

## ② `< 10` 硬编码 — 逐项证据

- 位置：src/bodydonor/qc_priority.py:232（原），唯一一处 `< 10`（全文件 grep 确认）。
- 修法：qc_priority.py:74 导入扩展为 `from .interaction_metrics import MIN_PER_CLASS, load_teacher_eval`（interaction_metrics.py:46 `MIN_PER_CLASS = 10`，该模块 `__all__` 已导出）；:232 `< 10` → `< MIN_PER_CLASS`。**未**另定义同名常量，**未**动 :81-82 `_HIGH_RISK_TEACHER_CLASSES` 本地定义（既有模式）。
- 折行：`MIN_PER_CLASS` 比 `< 10` 长，E501 111>100，set-comprehension 折 4 行（ruff 全绿）。
- ② 无先红测试的原因：行为零变化（阈值 10 改前后完全一致），不存在有意义的先红测试——**该边界测试是回归守卫，不是先红测试**。
- 守卫测试（tests/test_m6c_priority.py::test_w4_low_freq_threshold_equals_min_per_class）：
  - 断言 1：`MIN_PER_CLASS == 10`（显式锁定口径，审查 nit 2 修复时加）。
  - 断言 2：`component_low_freq_presence({"a":0.9,"b":0.9}, None, {"a":9,"b":10}) == 0.5`——9 判低频、10 不判。
  - 变异验证（实测）：`MIN_PER_CLASS` 改 9 → `1 failed, 15 deselected in 3.03s`；阈值改 11/`<=` 10 方向由 deep-reviewer 读码论证（两类都低频→1.0≠0.5 红）。
- 导入验证：`PYTHONPATH=~/work/BodyDonor-cleanup/src .../python -c "from bodydonor.qc_priority import MIN_PER_CLASS; assert MIN_PER_CLASS == 10"` → `import-ok 10`。
  - 坑（实测）：带 `-I` 跑会忽略 PYTHONPATH 导入到主 checkout 的 bodydonor（`/home/gogvf/work/BodyDonor/src/...`），报 ImportError——**正确跑法不带 -I**（memory 既有教训，本轮再次踩实）。

## 测试与 ruff 实测数字

- ① 后：test_all_no_duplicates.py + test_distill_license_gate.py = 64 passed。
- ② 后（排除 30 例 hold-out 慢测试跑法）：test_m6c_priority.py + test_qc.py + test_all_no_duplicates.py = **49 passed, 1 deselected in 3.96s**（test_ranking_consistency_30_holdout_top20 非本轮改动路径，`@pytest.mark.local_artifacts`）。
- 含慢测试的全量（第一次跑，pytest 本体在 2h 后台时限内完成）：49 个 `.` `[100%]` 无 F/E（summary 行被 pipe tail 吞，点数实测；事后 48+1deselect 复跑核验同集）。
- ruff：4 个改动文件 `All checks passed!`。
- 慢因记录：test_m6c_priority.py 含真实 30 例 hold-out 教师+学生 mask 现算逐类 Dice（历史实测 ~37 分钟 CPU），本轮与另一会话 gate-hardening 全量测试并行慢跑，均非挂起（utime 持续增长实测确认）。

## 审查 findings 清单与处理

deep-reviewer 1 个子代理（只读，无 Bash）：**无 blocker、无 warning，2 nit**，逐条 double-check（带复现实测）后确认属实并修复：

1. **Nit 1（CONFIRMED 已修）**：tests/test_all_no_duplicates.py 守卫只查重复不查条目真实存在——探针实测复现（`__all__` 改成不存在名字后守卫仍 passed，`import *` AttributeError）。修复：加 `assert all(hasattr(distill_data, n) for n in distill_data.__all__)`（b3bf151）。验证：修后探针下 1 failed（先红后绿成立），探针还原（git diff 空）。
2. **Nit 2（CONFIRMED 已修）**：边界测试名暗示"与常量相等"但断言用字面量 9/10。锁定力实测成立（MIN_PER_CLASS 改 9 → 1 failed），命名问题属实。修复：测试内显式 `assert MIN_PER_CLASS == 10`（b3bf151）。
3. 核查项全过：① 去重后 `__all__` 17 项与顶层定义逐一对照无丢名；② 无循环导入（interaction_metrics 无相对导入）、值确认 10；③ 折行后运算符优先级不变。
4. 未能核实（deep-reviewer 无 Bash）：全部基于读码；调用方已对关键项实测复现（见上）。

详见 M7-CLEANUP-REVIEW-FINDINGS.md。

## 未决清单

（空——两项清理 + 审查修复均已落地；无遗留。）

未做的（按纪律主动不做）：不顺手重构 ② 相邻的 `_HIGH_RISK_TEACHER_CLASSES` 本地定义（既有模式）；不碰 holdout.py:31 的同值定义（另一模块的既有模式，改动需跨模块统一，超出本轮范围）；不 push/merge/开 PR。
