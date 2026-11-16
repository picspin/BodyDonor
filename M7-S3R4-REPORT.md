# M7-S3 收口轮第 4 轮报告（S3R4：审查 findings 修复）

- 日期：2026-11-16。基线：S3R2 HEAD `f71511f`。清单：`M7-S3R-REVIEW.md`（S3R3，Blocker 0 / Warning 7 / Minor 7，全部经复现确认）。
- 分支：`feat/m7-s3-closeout`（worktree `~/work/BodyDonor-S3R`）。
- 范围：只修清单列出的项，不扩范围。W-7 按清单**不加新门禁**（改为注释 + 报告表述修正）。

## 提交列表

（见 git log——本轮修复 + 报告一并提交，审查清单 `M7-S3R-REVIEW.md` 入版本库）

## 逐条修复对照

| 编号 | 修复 | 提交 | 测试 |
|---|---|---|---|
| W-1 | `export_seg._build_source_images`：先归一化模态（"X-ray"→CR）再查 SOP Class 映射——别名不再落默认 CT Storage，Modality 与 SOP Class 自洽 | 修复提交 | `test_xray_internal_alias_maps_to_cr` 补 SOP Class 断言（先失败：实测 `SOPClassUID=CT` 而 `Modality=CR`；修后 26 passed） |
| W-2 | `verify_seg` 补源影像模态核对：新参数 `expected_source_modality`，从 `ReferencedInstanceSequence[0].ReferencedSOPClassUID` 反查（DX/CR/CT 映射，未知 UID 原样带出不猜模态），期望非空时核对——与报告 ⑥ 描述一致（选实现，未改报告） | 修复提交 | `test_verify_seg_checks_source_modality`（先失败：`TypeError unexpected keyword`；修后通过——DX 源期望 DX 通过 / 期望 CT 报"源影像模态 CT != 期望 DX"） |
| W-3 | 补 `prepare_dataset` 层测试：缺 source.json → `DistillDataError` 消息含"来源清单缺失"，dry_run 与非 dry_run 都不落盘 | 测试提交 | `test_prepare_dataset_rejects_missing_source_json`（1 passed） |
| W-4 | `check_distill_source`：source=total 缺 teacher_task 即拒绝（fail-closed，新分支在商用白名单检查前）；commercial 放行语义不变（带 task 时仍只放行 total）；补录处同样 fail-closed（同一函数覆盖） | 修复提交 | `test_total_without_task_rejected_fail_closed` + `test_provenance_backfill_without_task_rejected`（先失败 2 条；修后 54 passed） |
| W-5 | SynthSeg/MOOSE 条目 `known_restrictions` 改写与 `research_distill=conditional` 一致：conditional 放行 + 未决项保留（SynthSeg：FreeSurfer 第 3 条第三方权利 + ADNI DUA；MOOSE：NC 训练数据溯源 + "Commercial Version"）；**research_distill 值零改动** | 修复提交 | 全部 54 gate 测试过（known_restrictions 不参与判定，文字改动无行为影响） |
| W-6 | `ontology.mask_allowed_in_distill_training`：source=teacher 且 task=interactive_correction 拒绝（M6 项目政策边界一致：修正产物不进蒸馏）；注册表字段值零改动 | 修复提交 | `test_research_profile_restricted_teacher_derives_sa` 改写为拒绝断言（先失败 `assert not True`；修后 54 passed；nnInteractive 后端 30 passed 158.83s） |
| W-7 | **不加运行时门禁**（按清单）。`nninteractive_backend.correct_image` docstring 明示：注册表门控只覆盖 TS 教师路径，nnInteractive 修正路径未接门控（已知缺口，当前 ready=True 无实害，蒸馏防线在数据侧）；R2 报告 :24 后补"S3R4 W-7 更正"表述（其余无"绕过"措辞残留，grep 核对） | 修复提交 | 无新测试（纯注释/报告） |
| M-1 | `validate_entry`：research_distill=conditional 且 output_license_inherits 空列表 → raise（条款静默丢弃的入口防线） | 修复提交 | `test_validate_entry_conditional_requires_inherits`（先失败；修后 75 passed） |
| M-2 | 注册表模块 docstring 写明判定优先级：蒸馏资格唯一来源 `research_distill`，`research_use` 只作路由/说明（文档澄清，非重构） | 修复提交 | 无新测试 |
| M-3 | `DistillCase` 默认值 `label_source`/`teacher_task`："total"→""（来源未知不冒充 total；prepare_dataset 门禁通过后实测覆盖，调用方 `build_train_pool` 显式传 `label_source="total"` 不受影响） | 修复提交 | 全部 55 gate 测试过 |
| M-4 | 未注册任务拒绝消息："see LICENSES-THIRD-PARTY.md"→"see registry docs"（去掉 LICENSE 子串） | 修复提交 | `test_unknown_task_rejected` 补否定断言（先失败；修后 21 passed） |
| M-5 | `M7-S3R1-REPORT.md:42` 注明"R1 提交时的行号；当前 HEAD 为 export_seg.py:293/297（⑥ 提交后偏移）" | 报告提交 | 无 |
| M-6 | `M7-S3R2-REPORT.md:52` TS 族计数 20→30 更正（TS 族 30 条：allowed 24 / unverified 6），并补全部 36 条目计数（allowed 25 / unverified 7 / conditional 4）——实测核对 | 报告提交 | 无 |
| M-7 | ready=False 拒绝消息纯技术断言落地：`test_mr_task_not_ready_rejected` 补 '许可'/'BLOCKED'/'license' 否定断言 + 新增 `test_lung_vessels_not_ready_message_clean`（R2 报告声称的验证手段落地） | 测试提交 | 21 passed |

## 测试数字

- 受影响测试文件（7 个）合并跑：**133 passed / 0 failed / 0 error**，exit=0（`-p no:warnings -rN`，pytest 9.2.0）。
  - tests/test_m7_s3f_modality.py（15）+ tests/test_export_seg.py（11）+ tests/test_distill_license_gate.py（55）+ tests/test_teacher_registry.py（21）+ tests/test_m5e.py（5）+ tests/test_ontology.py + tests/test_api_ontology.py（合计 26；S3R5 实测更正：test_ontology.py 20 + test_api_ontology.py 6，此前误写 31）
- tests/test_nninteractive_backend.py（30 passed，158.83s，真实 CPU 推理慢路径）：W-6 改动后实跑通过（W-6 拒绝分支只在直接 API 命中，nni 后端自身测试不依赖该分支）。
- ruff：9 个改动文件全绿（`All checks passed!`，E501 修一处）。
- 未跑全量 + JUnit XML（最后一轮）。

## 失败测试先行记录（deep-reviewer 三条约束）

W-1 / W-2 / W-4 / W-6 / M-1 / M-4 均先写失败测试并实跑确认失败（退出码 1），再修，再实跑确认通过。W-2 的失败形态是 `TypeError`（新参数不存在），W-4/W-6/M-1 是 `assert not True`，W-1 是 SOP Class 断言 False，M-4 是 `'LICENSE' in msg`。

## 未决问题

1. **W-2 选型**：按清单第一选项走实现（`expected_source_modality` 新参数）；调用方 `export_and_verify` 未透传该参数（默认空 = 不核对，行为不变）——后续若要求导出链路强制核对源模态，在 `export_and_verify` 传 `expected_source_modality=source.modality` 即可（本轮不扩范围）。
2. **W-7 门控缺口保留**：nnInteractive 修正路径无注册表门控是已知缺口（清单明确不加运行时门禁）；若 interactive_correction 将来 ready=False，需在 correct_image 入口补 check_task 或依赖数据侧防线（sidecar source + 蒸馏门禁）。
3. **M-3 默认值空串**：`DistillCase` 直接构造不再默认 total；`build_train_pool` 生产路径显式传值不受影响。将来直接构造 DistillCase 且不写 source.json 的 case 会在 prepare_dataset 预检被拒（来源清单缺失），fail-closed 方向正确。
4. **SynthSeg/MOOSE conditional 取舍**（S3R3 未证实项 3）：本轮按清单不改 research_distill 值（conditional 是用户已定方向）；known_restrictions 文字已与之一致。
