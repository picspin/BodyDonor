# M7-S3P2 R4a-fix 审查 findings

deep-reviewer 1 个（只读，无 shell——findings 由审查子代理产出，本文件为实施方落盘归档）。

审查范围：`git diff a1632cc..HEAD`（src/bodydonor/license_registry.py、
src/bodydonor/nninteractive_backend.py、tests/test_teacher_registry.py、
tests/data/m2_decision_table.json、M7-S3P2-R4a-REPORT.md）。

## findings 清单

### Warning 1（待实施方实测复核）——SA 条目放行后 TS 分发门控可达性

- **file**: src/bodydonor/segmentation.py:85
- **claim**: check_task 对 SA 条目放行（license_registry.py:677 返回 allowed
  义务注记），check_worker_task（segmentation.py:79-87）只看 ok 标志，因此
  task=interactive_correction 现在可通过 TS 分发门控。R4a 报告 §2 称"生产
  路径不过 check_task，无实害"，该论断未经核实。
- **复现路径（审查者无 shell，待实施方补跑）**：注册表
  nninteractive/interactive_correction 条目 weights_present 与
  class_map_present 均为 True 时，`check_worker_task("interactive_correction",
  settings, modality="CT")` 不抛异常；grep 所有以 interactive_correction 调用
  check_worker_task / run_total 的生产路径，确认是否可达。

### Nit 1（属实，随本文件提交修正）——测试注释过时 SA 挡项引用

- **file**: tests/test_teacher_registry.py:234-235
- **claim**: 注释 "share_alike=False 使就绪三分不因 SA 挡" 与 "SA 挡项另有
  测试" 与 R4a-fix 新语义不符（SA 已不挡路由）。
- **修正**：注释改为 share_alike=False 只为隔离 SA 义务注记对本测试的影响，
  删除过时引用。

### Nit 2（待实施方 ruff 补跑）——license_registry.py:83 空行数

- **file**: src/bodydonor/license_registry.py:83
- **claim**: class_map_present 函数体结束后仅一个空行接模块级代码，不符 PEP8
  E305；是否触发取决于 ruff 配置。

### 已核实无问题（审查者确认）

- blocked_tasks 一票否决保留且位于最前。
- modality is None 分支 `ok = entries` 语义正确。
- SA 义务注记取 ready 中条目，多模态双条目下判定正确。
- `e.get("output_license_inherits", [])` 安全；nninteractive inherits 非空。
- 拒绝原因 prefix 与 blocked 基于同一 ok 集合，不会矛盾。
- 快照 `ok+license_inherits` 与 `_reason_key` 分类一致，route/distill
  判定保持测试应通过。

## 实施方补跑实测（本文件提交时补记）

1. **Nit 1 修正**：tests/test_teacher_registry.py:234-235 注释已同步（SA
   义务注记隔离 + 过时引用删除），见提交 3d98691。
2. **Nit 2 ruff**：`ruff check .` 全绿（E305 未触发，ruff 默认不启用 E305）。
3. **快照 diff**：`git diff a1632cc..HEAD -- tests/data/m2_decision_table.json`
   只剩 nninteractive route 2 行值变化（indent=1 与无尾换行已按原版还原，
   见还原提交 0969cdc 与 python json 格式对照）。
4. **受影响测试**：test_teacher_registry.py 31 passed；
   受影响 6 文件合计 150 passed；test_nninteractive_backend.py 非
   local_artifacts 面 26 passed, 4 deselected。
5. **Warning 1 实测**：grep src/ 全树 interactive_correction 生产调用——
   run_total（TS 教师路径）无任何 interactive_correction 调用点；
   check_worker_task 唯一生产调用在 segmentation.py:85（ts_run 派发），
   生产 segment 命令 task 默认 total、API/CLI 均不构造
   interactive_correction 任务。check_worker_task("interactive_correction", …)
   修复后确实不再抛异常（实测确认），但**生产不可达**——R4a 报告"生产路径
   不过 check_task，无实害"论断经 grep 实测成立。此为 R4a 政策返工的预期
   行为（SA 是许可义务不是可用性挡项），非本轮引入的缺陷；SA 是否该在
   分发层单独挡已列入未决清单交用户定。
