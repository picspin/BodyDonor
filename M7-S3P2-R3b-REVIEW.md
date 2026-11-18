# M7-S3P2 R3b 审查 findings（deep-reviewer，2026-11-18）

审查范围：基线 a135fbd..c802870（⑤ 两轴迁移 11 提交）。
审查方式：deep-reviewer 只读（无 Bash），逐条读码核对；findings 由调用方
复现核实。测试由调用方实跑：受影响 4 文件 103 passed（c802870 后复跑），
ruff 改动文件全绿。

## 结论

- Blocker 0
- Warning 1（属实，已修）
- Nit 1（属实，已修）

## Findings 与处理

### W1（Warning，属实已修）：docstring unverified 计数漂移

- 位置：src/bodydonor/ontology.py:601、src/bodydonor/license_registry.py:8-9
- 问题：docstring 写「5 个 unverified 任务与 25 个 allowed」，是按注册表旧
  三值枚举（research_distill=unverified）的口径；新 license_verified=False
  实为 7 条（5 单证据 + nnunet_v2/framework + vertebrae_body）。
- 复现：统计 TEACHER_REGISTRY 中 license_verified=False 的条目 = 7。
- 处理：改不带具体数量的描述（「准入未核实的条目与 allowed 条目同为
  Apache-2.0」），避免与注册表漂移。提交 c802870。

### N1（Nit，属实已修）：两轴枚举校验放过 int

- 位置：src/bodydonor/license_registry.py validate_entry
- 问题：`attribution_required not in (True,)` / `share_alike not in (True, False)`
  枚举比较中 Python `1 == True`，整数 1/0 通过校验；license_verified 已用
  isinstance(bool)，三字段口径不一致。
- 复现：构造条目 attribution_required=1、share_alike=0（其余字段合法），
  validate_entry 不抛异常（修复前实测 REPRODUCED）。
- 处理：两轴改 isinstance(bool) 校验（attribution_required 还要求恒 True）；
  失败测试先行补进 test_validate_entry_rejects_bad_new_fields（int 1/0 两
  case），修复后 103 passed。提交 c802870。

## 审查逐项核对结论（deep-reviewer）

1. 判定等价（36 条）：读码核对通过——license_verified=False 恰 7 条与快照
   blocked:unverified 7 条一致；conditional 4 条 license_verified=True 且
   inherits 非空对应 ok+license_inherits；其余 25 条 ok。调用方实跑快照
   两测（test_route_decisions_unchanged / test_distill_decisions_unchanged）
   确认零变化。
2. validate_entry 绕过：share_alike=False 且 inherits 非空放行是对的
   （MOOSE 即此情况）；license_verified isinstance(bool) 正确；int 漏洞即 N1。
3. all() 语义：36 条 task 无重复，与旧 `in` 判断等价；`if not entries` 先于
   all()，空列表不误放行。
4. 遗漏消费方：全仓（排除 .md）无 src/app/cli/scripts 残留，仅快照 JSON 与
   测试文件。
5. reason 子串：_reason_key 依赖的全部关键词保留；unverified 分支新文案含
   "unverified 语义"，正确落入 blocked:unverified。
