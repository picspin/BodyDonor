# M7-S3P2 R4a 审查 findings（⑥ ready 拆分）

deep-reviewer 1 个（只读，无 shell——读码审查）；未完成核对项由实施方（有 shell）补跑实测。审查对象：基线 `eb0183b`，5 提交至 `74b6207` + 修复提交。

## Findings

| 级别 | 位置 | 问题 | 处理 |
|---|---|---|---|
| W1 | license_registry.py:650-652 | check_task 挡项原因三元表达式优先级错误：`"缺权重" if not w else "" + "缺 class_map" if not c else ""` 解析为嵌套 else 分支——组合挡项只报缺权重，吞掉后续挡项 | **属实已修**（ca4aab4）。实施方实测复现：三挡组合（缺权重+缺 class_map+SA）原因只含「缺权重」（复现脚本：构造 wp=False/cp=False/sa=True 条目调 check_task，`'缺 class_map' in msg` == False）。修复：改逐条列表收集 + set 去重；失败测试先行 `test_check_task_rejection_reason_combinations_all_named`（7 组合参数化，修复前 red、修复后 green） |

Blocker 0 / W 1 / N 0。

## 审查未完成项——实施方补跑实测（本文件记录）

1. **total weight_integrity sha256 对照**：`sha256sum ~/.totalsegmentator/nnunet/results/Dataset298_TotalSegmentator_total_6mm_1559subj/nnUNetTrainer_4000epochs_NoMirroring__nnUNetPlans__3d_fullres/fold_0/checkpoint_final.pth` → `3489323a4b759506ab46b489eb78b72142f2143aea6e6d828604d754bd7a9270`，与注册表记录（license_registry.py:102）一致；size_bytes 164951587 与 stat 一致。**核对通过**。
2. **快照 nninteractive route 变更论证**：读 nninteractive_backend.py:183-190——生产修正路径 `correct_image` / `_run_locked` 未接注册表门控（W-7 已知缺口，docstring 明示），check_task 只覆盖 TS 教师路径（run_total → check_worker_task）。故 nninteractive route 从 allowed → blocked(not-ready) 无生产实害；蒸馏防线在数据侧（sidecar source + ontology 判定）。**变更论证成立**。
3. **全仓 grep 零残留**：`grep -rn '"ready"' src/ tests/ scripts/ config/` 除守卫断言文本（test_teacher_registry.py:510/523/528 防回潮测试本身）与 recalibration_ready（qc.py:88-89 数值重算抽检，另一概念豁免）外零命中；src 树由 `test_src_no_ready_residual` 守卫测试锁定。**零残留确认**。
4. **36 条目逐条测试**：`test_all_36_entries_three_ready_fields_valued` 遍历 TEACHER_REGISTRY（len==36 先行断言），逐条断言 wp/cp/share_alike 三 bool + ready 键清零。**覆盖确认**（31 passed 含该测试）。
5. **validate_entry 防回潮**：`ready` 已从 `_REGISTRY_FIELDS` 删除（license_registry.py），validate_entry 缺字段检查只查字段集——外部条目带多余 ready 键不报错但推导只读三字段（weights_present/class_map_present/share_alike），ready 键无消费方（check_task 改读三字段）。**无绕过路径**。
