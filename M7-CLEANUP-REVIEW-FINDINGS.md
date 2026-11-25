# M7-CLEANUP 审查 findings（deep-reviewer，2026-11-19）

结论：无 blocker、无 warning，2 nit，均已 double-check 复现属实并修复（提交 b3bf151）。

## Nit 1 — 守卫测试只查重复，不查条目真实存在（CONFIRMED 已修）

- 文件：tests/test_all_no_duplicates.py:10-11
- 问题：`__all__` 拼错导出名（如 `DISTILL_DATASET_NAMEX`）时测试仍 passed，`import *` 报 AttributeError。
- 复现（调用方实跑）：sed 把 `"DISTILL_DATASET_NAME",` 改成 `"DISTILL_DATASET_NAMEX",` 后跑
  `PYTHONPATH=~/work/BodyDonor-cleanup/src ~/work/BodyDonor/.venv/bin/python -m pytest tests/test_all_no_duplicates.py -q -p no:warnings`
  → 修前 `.` [100%] passed（守卫失守），`import *` → `AttributeError: ... no attribute 'DISTILL_DATASET_NAMEX'`。
- 修复：加 `assert all(hasattr(distill_data, n) for n in distill_data.__all__)`。
- 验证：修后无探针 `.` [100%] passed；探针下 `1 failed in 0.28s`（先红后绿成立）。探针已还原。

## Nit 2 — 边界测试名暗示"与常量相等"但断言用字面量（CONFIRMED 已修）

- 文件：tests/test_m6c_priority.py:152
- 问题：测试名 `test_w4_low_freq_threshold_equals_min_per_class` 暗示与 `MIN_PER_CLASS` 相等，断言却只用字面量 9/10，读者会误解锁定语义。
- 复现（调用方实跑）：sed 把 `interaction_metrics.py` 的 `MIN_PER_CLASS = 10` 改 9 后跑
  `PYTHONPATH=... python -m pytest tests/test_m6c_priority.py -p no:warnings --tb=no -k low_freq_threshold`
  → `1 failed, 15 deselected in 3.03s`（锁定力成立；审查的命名问题为读码结论，行为无问题）。
- 修复：测试内显式 `assert MIN_PER_CLASS == 10`（名字与断言一致，口径锁死）。
- 验证：ruff 全绿 + `49 passed, 1 deselected in 3.96s`。

## 核查项通过（deep-reviewer 读码结论）

- ① `__all__` 去重后 17 项与顶层公开定义逐一对照，无丢名（PROJECT_ROOT/DATA_ROOT/CT_LITE/AMOS 未导出为既有口径）。
- ② `MIN_PER_CLASS` 值确认 10（interaction_metrics.py:46），无循环导入（interaction_metrics 无相对导入），行为零变化。
- ③ 折行后运算符优先级不变（`in`/`<` 先于 `or`）。
- 未核实：未跑 pytest（deep-reviewer 无 Bash）；qc.py 反向导入未查（本轮未改 qc.py）。
