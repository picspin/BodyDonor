# M7-S3P 合并后修复轮 R4 报告：⑤ M-2 统一（research_use 与 research_distill 合并为单一字段）

基线 R3 HEAD `f3b063c`；分支 `feat/m7-s3-postmerge`。只动本 worktree。

## 一、做法与范围

合并为单一字段，以 `research_distill` 为准，删 `research_use`。本轮只删字段、不改任何条目的判定值（`total` CT-only 与既有 `research_distill` 值均不动）；`restriction_basis` 与 `output_license_inherits` 未被波及。

### 合并前映射关系（36 条目实测，快照 tests/data/m2_decision_table.json）

| 原 research_use | 合并后 research_distill | 条目数 | 判定变化 |
|---|---|---|---|
| allowed (25) | allowed (25) | TS 18 双证据组 + total + 5 个 _mr 族 + mri_core | 无变化（恒等映射） |
| restricted (4) | conditional (4) | nninteractive、synthseg、moose、flexray | 无变化：两字段并存时蒸馏判定已唯一读 research_distill=conditional；research_use=restricted 的旧语义（研究路由说明 + 挡 unverified）不涉及这 4 条（非 unverified） |
| unverified (7) | unverified (7) | nnunet_v2/framework、teeth、trunk_cavities、liver_lesions、vertebrae_pp、vertebrae_pp_refined、vertebrae_body | 无变化：原判定层 research_use=unverified 挡研究蒸馏，合并后 research_distill=unverified 同值承担 |

**无映射无法唯一确定的条目**：36 条一一对应，零冲突，无需保守等效处理，无需用户裁定项。

### 行为保持铁律：36/36 判定逐条一致

先写决策表测试（tests/test_research_field_decision_table.py），合并前快照 36 条目两项实际判定——研究路由（LicenseRegistry.check_task，modality 取条目首模态）与研究蒸馏（ontology.mask_allowed_in_distill_training，source=teacher）的 `[bool, reason-class]`。快照值由合并前实测生成（临时脚本从活体注册表 dump，不手填期望值）。合并后同一测试断言逐条不变：

- `test_route_decisions_unchanged`：36/36 route 判定一致（route 从不读 research_use——判定只读 blocked_tasks + ready + 模态）。
- `test_distill_decisions_unchanged`：36/36 distill 判定一致。
- reason 只断言分类（`blocked:modality` / `blocked:not-ready` / `blocked:no-entry` / `blocked:unverified` / `blocked:nni-correction` / `ok` / `ok+license_inherits`）不锁逐字文本，测试口径不随合并改。

## 二、迁移清单（grep 结果）

合并前 grep 全仓 `research_use`（src/scripts/tests/docs）21 文件；迁移后代码读值处清零，仅剩迁移说明注释与快照数据：

| 位置 | 迁移动作 |
|---|---|
| `src/bodydonor/license_registry.py` | 删字段行 13 处（条目内）+ `_REGISTRY_FIELDS` 去掉 `research_use` + `validate_entry` 删枚举校验（3 行）；模块 docstring 改判据描述 |
| `src/bodydonor/ontology.py` | 判定层删 `research_use` 双检（`uses` 集合 + `research_use=unverified` 拒绝分支 5 行）；docstring 改「M-2 统一：原 research_use 挡 unverified 语义由本字段同值承担」 |
| `src/bodydonor/distill_data.py` | 注释 4 处 `research_use` → `research_distill` |
| `scripts/prepare_distill_dataset.py` | help 文本 1 处 |
| `tests/test_teacher_registry.py` | fixture dict 4 处删 `research_use` 行 + 断言 3 处（lung_vessels/teeth/vertebrae_body）改读 `research_distill`（期望值 allowed/unverified 不变——恒等映射，等价于原断言语义） |
| `tests/test_distill_license_gate.py` | docstring 2 处 |
| `LICENSES-THIRD-PARTY.md` §1.2 | 差异注记 1 处，注明 M-2 统一 |

app.py / cli.py / tui.py / review_session.py / agent_context.py / static/：grep 零命中，无需迁移（未注册表条目无 research_use 键，快照 JSON 的 research_use 原值键仅作合并前记录，测试不读）。

## 三、验收实测

- 决策表测试：合并前 4 通过/1 失败（单一来源断言按设计红，未提交）；合并后 **6/6 通过**（快照一致 + 单一来源 + 兜底类恒空）。
- 直接受影响文件：test_research_field_decision_table(6) + test_teacher_registry + test_distill_license_gate + test_ontology + test_m7_s3_modality + test_m7_s3f_modality + test_m7_s3p2_backfill + test_license_whitelist = **93 passed**。
- 首块文件（15 文件 220 tests，含 api/db/distill/export/gpu/holdout/ingest）：**220 passed** 16.5s。
- **全量套件 704 collected：704 passed / 0 failed / 0 error**（分批实测，R3 同法）：
  - 非 local_artifacts 全量：**687 passed / 17 deselected / EXIT=0**（63.9s）；
  - local_artifacts A 批（evaluate_student_holdout + interaction_metrics）：**6 passed** 90.3s EXIT=0；
  - B 批（nninteractive_backend cpu 真跑 + qc_interaction_e2e + review_api risk_queue）：**7 passed** 201.2s EXIT=0；
  - C 批四个慢速 30 例 Dice（m6c 排序 / m6d 门控 / m6e 419 对回归 / m6f trace 30 例回填）：**4 passed** 2229.4s EXIT=0。
- ruff：全绿（I001 一次 --fix 修复）。
- 条目数：合并前后 36 不变，(teacher_id, task) 唯一。

## 四、提交

| hash | 内容 |
|---|---|
| 9e5d6a2 | test: 决策表测试——36 条目合并前实测快照 + 判定保持断言 |
| 246f6d3 | refactor: M-2 统一——合并为单一字段（7 文件 +24/−45） |
| 73bfc9f | style: 决策表测试 import 排序（ruff I001） |
| 9a97790 | fix: 审查 W——reason 分类补 nni-correction 分支 + 兜底类恒空断言 |
| 8a0aa29 | docs: R4 轮次报告 |

## 五、审查 findings 清单（deep-reviewer，无 Bash 只读）

| # | severity | 位置 | 问题 | 处置 |
|---|---|---|---|---|
| W1 | warning | tests/test_research_field_decision_table.py:39 | `_reason_key` 兜底类 `blocked:other` 吞掉 nninteractive 硬拒绝类别（该分支消息不含任何已识别关键词，措辞变化会被掩盖） | **已修 9a97790**：补「修正产物不进蒸馏」分支 → `blocked:nni-correction`；新增兜底类恒空断言；快照重分类该条目（合并前基线 f3b063c 分支文本已核实含关键词，重分类对合并前行为忠实；布尔值与字段原值不动） |

其余方向（悬空引用、判定语义回退、validate_entry 自洽、快照质量、测试口径漂移、docs 一致性、条目数唯一性）核查无问题。审查者勘误本任务书一处计数：映射条目数是 allowed→allowed **25** 条（非 19），nnunet_v2 已计入 unverified 7 条，合计 36。

复现步骤（W1 修前）：
```
cat tests/data/m2_decision_table.json | grep -c 'blocked:other'   # 修前 1（nninteractive distill）
# 修前：把 ontology.py interactive_correction 分支消息改成另一段不含关键词的文字，
# pytest tests/test_research_field_decision_table.py 仍通过——类别未锁定
```

## 六、未决

1. **validate_entry 不拒绝多余键**（审查者提及，非回退）：若有人把 research_use 重新加回条目，validate_entry 不会拒绝（字段校验只查缺失/非法值，不查未声明键）。如需硬守卫可在后续轮加 `unknown_keys` 检查——未做，避免扩大本轮范围（`test_research_distill_is_single_decision_source` 已断言字段清零作软守卫）。
2. **test_m6c_priority 等 4 个 M6 文件单跑在 100–300s 超时**：慢点在 `test_ranking_consistency_30_holdout_top20` 等 local_artifacts 测试（30 例真实 nibabel 读 + 逐类 Dice）——非本轮改动引入（该测试不读 research_use/research_distill），本轮已分批以足额时限实测 4 passed（2229.4s）。
