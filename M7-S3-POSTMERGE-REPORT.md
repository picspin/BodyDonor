# M7-S3 合并后修复轮定稿报告

- 基线：`origin/main a8a5904`（PR #3 合并后）。分支：`feat/m7-s3-postmerge`（worktree `~/work/BodyDonor-S3P`）。
- 本轮五项：① b-U3、② MR 导出补键、③ source.json 回填、④ 注册表门控放宽、⑤ M-2 统一。⑥ 蒸馏门控残余随下一切片走，本轮未动。S2 待 rebase 未动。
- 提交：20 个（见附录 B）。diffstat（`origin/main..HEAD`，素子亲读）：**20 文件 +1671/−60**。
- 不 push、不 merge、不开 PR。

## 一、五项逐项证据

### ① b-U3：eval 调用补 modality

- `eval/evaluate_holdout.py:84` 的 `run_total` 补 `modality="CT"`（hold-out 是 CT 数据，与 `qc_coldstart.py` 同做法）。
- 测试 `test_ts_teacher_inference_passes_modality_ct` 先写先红（`assert None == 'CT'` 坐实缺陷）后绿；调用点全仓扫描：其余两处已传 CT，无其他漏传。
- 协调方复核：调用点亲读在位；受影响 6 文件逐文件 collect 求和 **74 collected**，协调方自跑 **74 passed / 2.91s**；ruff 全绿。

### ② MR 源导出补键

- `src/bodydonor/export_seg.py` 的 `_build_source_images` 映射补 `"MR": "1.2.840.10008.5.1.4.1.1.4"`（MR Image Storage，pydicom UID 字典核实，非记忆值）；`verify_seg` 源模态反查映射补同 UID，互逆；DX/CR/默认 CT 键不变。
- 审查修复（提交 `7546197`）：`_normalize_modality` 非别名值 `strip().upper()`（与 `check_task` W8 同口径，小写 "mr" 不再落默认 CT）；`export_and_verify` 模态不符 raise 分支直测。
- 测试：MR 源走 `export_and_verify` 全链路通过 + 期望 CT 时报"源影像"不符（模态不符仍报错保留）；原钉"MR 落 CT Storage 报错"的测试已按新行为改写（见 R1 报告）。

### ③ source.json 回填（45 目录）

- 脚本 `scripts/backfill_source_json.py`（230 行，`--dry-run` 先演练，幂等，已有 source.json 不重做；原子写 tmp + `os.replace`）。
- 实写 **21 份文件、覆盖 45 目录**（AMOS 20 每目录一份 + ct_lite 库级一份覆盖 25 case）；**0 保持缺失**。数据目录最终核对：40/40 AMOS 有 source.json、ct_lite cases=25。不进 git。
- 判定依据（三档，每档有产生脚本 + mtime 窗口 + 独立旁证；判据为全目录 mtime 一致性，117 个 nii 逐一在窗口内才通过）：M3 门禁评估 15 个 AMOS hold-out、M4 训练侧 5 个 AMOS、M3 冷启动 25 个 ct_lite。来源按证据推断为 TS total（非任务清单实测，见未决 2）。
- 验收：`read_teacher_provenance` 对 65 目录（训练池 35 + hold-out 30）实测全 `provenance_missing=False`；`prepare_dataset(dry_run=True)` 预检 PASS（n_cases=35：AMOS 25 + ct_lite 10）。
- 协调方复核：脚本全文亲读（fail-closed 分支、库级全过才写、原子写均在位）；AMOS 40/40、amos_0004 清单内容、ct_lite 25 case 清单实测；`--dry-run` 现报 0 可回填 0 未决（幂等）；受影响 3 文件协调方自跑 **90 passed / 0.82s**；ruff 全绿。
- 过程中的纠错：第一版脚本把 ct_lite 写成每 case 一份，与 `read_teacher_provenance` 读取契约错一层，验收实测暴露后改库级一份，错位文件已删重写（见 R2 报告 §3）。

### ④ 注册表门控放宽（不收紧）

- 36 条目逐条审计（依据：LICENSE-CHECK、教师 README、已部署 checkpoint）：**拓宽 1 条**——nnunet_v2 框架 (CT,MR) → (CT,MR,PET)（MR/PET 依据 LICENSE-CHECK §一总表，CT 依据 §七 + Dataset501 实证）；**新增条目 0**。
- 真正修复的门控缺口（模态匹配，非拓宽）：DICOM PET 代码 **PT** 此前对 nninteractive/moose 一律被拒——`MODALITY_ALIASES` 补 PT→PET；投影 X 光 **DX/CR** 此前对 flexray_xray 永拒——补 DX/CR→X-RAY；flexray 登记值大小写修为 `X-RAY`（比对侧 strip().upper()，原 "X-ray" 永不相等）。
- 特别核查：nnInteractive 上游 HF 实测唯一 checkpoint；本地部署同款，协调方实测唯一 `.pth`、411387150 字节、sha256 与注册表逐字一致，无多 checkpoint 需拆分。total_mr 族无 _mr 权重；MOOSE/SynthSeg 未部署。
- 红线：`total` CT-only 未动；无模态删除；`research_distill` 零行改动；依据不足请用户定的条目：无。
- 协调方复核：注册表 diff 全文亲读；6 文件逐文件 collect 求和 **129**，协调方自跑 **129 passed / 2.17s**；ruff 全绿。**报告原文"109"为加总错误，已勘误提交 `f3b063c`**。

### ⑤ M-2 统一（research_use 与 research_distill 合并为单一字段）

- 以 `research_distill` 为准，删 `research_use`；迁移清单：license_registry（条目字段 13 处 + `_REGISTRY_FIELDS` + validate_entry 枚举校验）、ontology（判定层 `research_use` 双检删除，只读 `research_distill`）、distill_data（注释 4 处）、scripts/prepare_distill_dataset.py（help 1 处）、测试、LICENSES-THIRD-PARTY.md（差异注记 1 处）。app/cli/tui/review_session/static 零命中无需迁移。
- 决策表（`tests/test_research_field_decision_table.py` + 快照 `tests/data/m2_decision_table.json`，快照值合并前实测生成不手填）：**36/36 判定逐条一致**。映射：allowed→allowed 25、restricted→conditional 4（nninteractive/synthseg/moose/flexray）、unverified→unverified 7；零冲突，无用户裁定项。原 `research_use=unverified` 挡蒸馏语义由 `research_distill=unverified` 同值承担。
- 协调方复核：ontology 双检删除 diff 亲读；注册表实测 36 条目、分布 allowed 25 / unverified 7 / conditional 4、conditional 4 条确为 brain_mri_qc/flexray_xray/interactive_correction/moose_qc、`research_use` 键 0 残留；grep 代码读值处清零（仅剩迁移注释与快照数据键，旧报告文件历史文字未动）；8 文件逐文件 collect 求和 **148**，协调方自跑 **148 passed / 2.06s**；ruff 全绿。**报告原文"93"漏计了 test_distill_license_gate 的 55 项，已勘误提交 `6a98959`**。

## 二、测试数字（实施方与协调方各一遍）

- 实施方全量：R2 694/694、R3 698/698、R4 **704 collected / 704 passed**（分批：非 local_artifacts 687 passed / 17 deselected EXIT=0；local_artifacts 17 项三批 6+7+4 passed）。
- 协调方独立全量重跑：**704 passed / 0 failed / 0 skipped，exit 0，2566.39s（0:42:46）**；JUnit XML testsuite `tests=704 failures=0 errors=0 skipped=0 time=2566.377`；日志 0 行 FAILED/ERROR。（`pytest tests/ --junitxml=m7-s3p-suzi-fulltest-junit.xml`，跑前核 `__file__` 指向 worktree src）。
- ruff：协调方实测全绿。
- 各轮受影响文件（协调方自跑）：R1 74 passed、R2 90 passed、R3 129 passed、R4 148 passed。
- 报告计数勘误两处（均为协调方复核抓出、已提交）：R3 报告 109→129（`f3b063c`）、R4 报告 93→148（`6a98959`）。R1 报告"同批 38"为 nit 修复前中间数、R2 报告 §5"10 项"为修复前中间数，终态数字以本报告为准。

## 三、未决清单（不写"全部修复"）

1. **ct_lite 库级清单对未来 case fail-open**（R2 W2）：`read_teacher_provenance` 读 `teacher_out.parent/source.json` 时不校验 case 是否在库级 `cases` 列表内，`ts_out/` 下新增目录会无证据继承 `source=total`。需改契约（读侧校验 cases 成员，或 ct_lite 改每 case 一份放可读位置）——属读侧语义改动，本轮红线未动门禁代码。当前 25 个 case 已回填不受影响。
2. **来源判据为推断非实测**（R2）：mtime 窗口 + 目录布局 + 117 类全量推断，非读取任务清单/下载 manifest 的实测来源。三档证据方向上安全，如需更强审计链需回溯 M3/M4 运行日志（未留存）；未验证 WSL2 时区变化对 mtime 窗口判据的影响。
3. **prepare_dataset dry-run 耗时约 9 分钟**（R2）：dry-run 仍逐 case 加载原图 + 15 类 mask 合并（跳过落盘不跳过合并）。如需纯预检需在 `prepare_dataset` 加 early-exit——属门禁代码改动，未做。
4. **segmentation.py:361 NULL 模态缺口**（R1 审查 Should）：`row.modality=None` 时 `check_worker_task` 退化为仅 task 门控，模态未知 series 仍可派发 CT-only 任务（代码注释自己写明了这点）。需后续决定：NULL 模态 series 拒绝派发，还是显式降级并记录。
5. **export_seg PT 源 SOP Class**（R3 W3，既有缺口）：SOP Class 映射无 PT 键，PET 源 SEG 导出落 CT Image Storage。导出层模态归一化应复用同一别名映射或补 PT 对应 SOP Class——后续处理。
6. **validate_entry 不拒多余键**（R4）：若有人把 `research_use` 重新加回条目，validate_entry 不会拒绝（只查缺失/非法值，不查未声明键）。软守卫测试已断言字段清零；硬守卫可后续加 `unknown_keys` 检查。
7. **nnInteractive v2 API**（R4）：上游已出 API_CHANGES_v2.md（curl 404 未取到内容），未来升级时 checkpoint_id 可能变化，届时重新登记。
8. **_normalize_modality 大写依赖真实数据**（R1）：生产 export 联调层（`source_info_from_persisted`）尚未接线，modality 实际取值待接线后验证。
9. **M6 慢测耗时**（R4）：test_m6c_priority 等 4 个 local_artifacts 测试单跑 100–300s，属耗时非挂起，本轮已分批足额时限实测通过。

## 附录 A：导入模块路径（协调方全量同一 PYTHONPATH 实测）

```
/home/gogvf/work/BodyDonor-S3P/src/bodydonor/__init__.py
/home/gogvf/work/BodyDonor-S3P/src/bodydonor/license_registry.py
/home/gogvf/work/BodyDonor-S3P/src/bodydonor/ontology.py
/home/gogvf/work/BodyDonor-S3P/src/bodydonor/distill_data.py
/home/gogvf/work/BodyDonor-S3P/src/bodydonor/ingest.py
```

五条全部指向 worktree `~/work/BodyDonor-S3P/src`——全量测的是本分支源码。

## 附录 B：提交列表（20 个，`origin/main..HEAD`）

```
docs(m7-s3p): 合并后修复轮定稿报告——五项逐项证据 + 704 全绿 + 未决 9 条（本提交，不列 hash）
6a98959 docs(m7-s3p4): R4 报告受影响文件总数勘误 93→148（逐文件求和）
7a9dd42 docs(m7-s3p4): R4 报告补全量套件实测——704/704 passed 分批 EXIT=0
8a0aa29 docs(m7-s3p4): R4 轮次报告——M-2 统一 36/36 判定一致 + 迁移清单 + 审查 findings
9a97790 fix(m7-s3p4): 审查 W——决策表 reason 分类补 nni-correction 分支 + 兜底类恒空断言
73bfc9f style(m7-s3p4): 决策表测试 import 排序（ruff I001）
246f6d3 refactor(m7-s3p4): M-2 统一——research_use 与 research_distill 合并为单一字段
9e5d6a2 test(m7-s3p4): M-2 决策表测试——36 条目合并前实测快照 + 判定保持断言
f3b063c docs(m7-s3p3): R3 报告受影响测试总数勘误 109→129（逐文件求和）
8a6f655 docs(m7-s3p3): R3 轮次报告——36 条目审计表 + 门控缺口修复 + 审查 findings + 未决
7d945b9 fix(m7-s3p3): 审查修复——W1 nnunet CT 依据补 §七出处 + W2 DX/CR 别名 + N4/N5/N6
a76d55f feat(m7-s3p3): ④ 注册表门控放宽——PT/PET 别名 + X-RAY 大小写修复 + nnunet_v2 PET 拓宽
08fc490 docs(m7-s3p2): 报告补全量套件实测——694/694 passed EXIT=0
58a30ad docs(m7-s3p2): R2 轮次报告——45 目录回填清单 + 审查 findings + 未决
22833bb fix(m7-s3p2): 审查修复——W1 stat 崩溃 + W4 全目录 mtime 一致性 + N5 原子写 + N6/N7
fdb941a feat(m7-s3p2): ③ source.json 回填脚本——历史 ts_out 目录证据判定补录
dfe49a2 docs(m7-s3p1): R1 轮次报告——b-U3 + MR 导出补键逐项证据与审查 findings
7546197 fix(m7-s3p1): 审查修复——_normalize_modality 大小写归一 + raise 分支直测
7d0988d fix(m7-s3p1): ① b-U3——ts_teacher_inference 显式传 modality=CT
8e7980b fix(m7-s3p1): ② MR 源导出补键——SOP Class 映射补 MR Image Storage
```
