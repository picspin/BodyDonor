# M7-S3 收口轮第 3 轮审查清单（S3R3，deep-reviewer 对抗性审查）

- 日期：2026-11-16。基线：S3F HEAD `525939b`；范围 `git diff 525939b..HEAD`（9 提交，14 文件，+643/−70）。
- 分支：`feat/m7-s3-closeout`（worktree `~/work/BodyDonor-S3R`，工作区干净）。
- 方法：4 个 deep-reviewer 子代理并行分片（只读 Read/Grep/Glob）+ 调用方 Bash 二次复现（子代理无 Bash，每条 finding 均经实跑或读码确认后才入列）。Ponytail full 激活。
- **本轮只审查不修复**；清单不提交（修复轮一并提交）。
- 结论：**Blocker 0 / Warning 7 / Minor 7**。三分结构、字段值抽查、全量测试数字等核心项核对通过（见 §4）。

---

## 1. Blocker

无。

## 2. Warning（7 条）

### W-1 X-ray 内部别名的 SOP Class 与 Modality 自相矛盾

- 位置：`src/bodydonor/export_seg.py:212-215`（SOP Class 映射字典）+ `:227`（Modality 规范化）。
- 复现：`_build_source_images(SourceSeriesInfo(..., modality="X-ray", ...), 32, 32, 1)[0]` → 实测 `Modality='CR'`、`SOPClassUID='1.2.840.10008.5.1.4.1.1.2'`（CT）。同一合成实例 Modality 为 CR、SOP Class 却是 CT。
- 冲突点：映射字典只有 `"DX"`/`"CR"` 两键，内部别名 `"X-ray"` 不在其中落默认 CT；而 Modality 规范化为 CR。`tests/test_m7_s3f_modality.py` 的 `test_xray_internal_alias_maps_to_cr` 只断言 Modality，未断言 SOP Class，测试未钉住。
- 修复方向：先归一化模态再查 SOP Class 映射（"X-ray"→CR→CR Storage）；补 SOP Class 断言。

### W-2 报告 ⑥ 描述的"源影像模态核对"在 verify_seg 未实现

- 位置：`M7-S3R2-REPORT.md:67`（声称）vs `src/bodydonor/export_seg.py:270-351`（实现）。
- 复现：`grep -rn "ReferencedSOPClassUID" src/` → 无命中（仅 tests/test_export_seg.py:184 断言）。verify_seg 只做 SEG 自身 Modality 核对，未读 ReferencedSOPClassUID。
- 冲突点：报告称"源影像模态从 ReferencedSeriesSequence...取回并核对"，代码只做了自身模态核对，报告与实现不符。
- 修复方向：verify_seg 补源模态核对（从 ReferencedSOPClassUID 反查），或修正报告描述。

### W-3 prepare_dataset 层的缺失来源拒绝无测试覆盖

- 位置：`src/bodydonor/distill_data.py:479-485`。
- 复现：`grep -n "来源清单缺失\|provenance_missing" tests/test_distill_language_gate.py`（实为 test_distill_license_gate.py）→ 无 prepare_dataset 层用例让 `prepare_dataset` 遇到缺 source.json case；`test_provenance_missing_case_rejected_by_gate` 只测 `check_distill_source`；`test_prepare_dataset_rejects_partial_ts_out` 调用前已补写 source.json。删掉 `:479` 的 raise 全部测试仍绿（后续 `check_distill_source("")` 同样拒绝）。
- 冲突点：R1 报告声称"`prepare_dataset` 预检拒绝缺 source.json 的 case"，该分支无测试钉住。
- 修复方向：补一条 prepare_dataset 层测试：缺 source.json → `DistillDataError` 且消息含"来源清单缺失"，并断言 dry_run 不落盘。

### W-4 commercial 路径对"source=total 但缺 task"仍放行

- 位置：`src/bodydonor/distill_data.py:136` + `:488`（`prov.get("task", "")`）。
- 复现：实跑 `check_distill_source("total", teacher_task="", experimental=False)` → `(True, 'ok')`。
- 冲突点：补录路径（backfill）允许手写 `{"source": "total", "backfill": true, ...}`，无 task 即过门禁——与"来源必须实测"初衷相悖；补录流程让该情形更易出现（S3F 评审已记录为既有行为，非本轮引入，但补录路径放大了它）。
- 修复方向：`check_distill_source` 对 source=total 要求 teacher_task 非空；或在 read_teacher_provenance/补录校验要求 task 必填；补测试。

### W-5 注册表内文字与字段矛盾（SynthSeg/MOOSE）

- 位置：`src/bodydonor/license_registry.py:162`、`:190`（known_restrictions）vs `:172`、`:199`（research_distill）。
- 复现：读 synthseg 条目：known_restrictions 写"蒸馏阻断待法务复核；不得登记为可蒸馏"，同条目 research_distill=`conditional`；`mask_allowed_in_distill_training(...task='brain_mri_qc')` 实测放行。
- 冲突点：同一条目两个说法互相打架。conditional 放行是用户拍板的语义放宽（"不因商业政策模糊自动禁止"），但注册表旧文字未同步。
- 修复方向：改写 known_restrictions 文字与 conditional 一致（保留法务未决项说明），或与用户确认是否维持 QC-only。

### W-6 nnInteractive 边界只挡 source，不挡 teacher 任务

- 位置：`src/bodydonor/ontology.py:655-661` + `license_registry.py:146`。
- 复现：实跑 `mask_allowed_in_distill_training({'experimental':False,'source':'teacher','task':'interactive_correction'})` → `(True, 'license_inherits: CC-BY-NC-SA 4.0 …')`。
- 冲突点：restriction_basis 写"项目政策：修正产物不进蒸馏"，该边界只在 source=`nninteractive_guided` 拒绝（ontology.py:613-623）生效；teacher 来源的 interactive_correction 任务照放。研究流水线（distill_data.py:129 只对 source==total 走注册表）本身摸不到，仅直接 API 会命中。
- 修复方向：对 interactive_correction 的 teacher 来源也拒绝，或把该条目 research_distill 调整为与边界一致。

### W-7 nnInteractive 修正路径不经注册表门控

- 位置：`src/bodydonor/nninteractive_backend.py`（correct_image :172、_run_locked :270）。
- 复现：`grep -n "check_task\|check_worker_task\|LicenseRegistry" src/bodydonor/nninteractive_backend.py` → 无命中；调用方 cli.py:469 与 app.py:1132。
- 冲突点：TS 路径有 check_worker_task 一票否决，nnInteractive 路径没有——blocked/ready 对该路径失效，与 S3R2 报告"调用链无绕过"表述不一致。当前 interactive_correction ready=True 故无实害，但属门控缺口。
- 修复方向：correct_image 入口补 LicenseRegistry.check_task("interactive_correction")，或报告明示"门控只覆盖 TS 教师"。

## 3. Minor（7 条）

### M-1 conditional 且 inherits 空列表时条款被静默丢弃

- 位置：`src/bodydonor/ontology.py:657-662`。
- 复现（读码）：conditional 且 output_license_inherits=[] → 落 `"ok"`，条款丢失。当前 36 条无此情况（已核对），潜在缺口。
- 修复方向：validate_entry 要求 research_distill=conditional 时 output_license_inherits 非空。

### M-2 判定有两个真相源（research_use 与 research_distill）

- 位置：`src/bodydonor/ontology.py:635`。
- 复现（读码）：判定先用 research_use=="unverified" 阻断，再用 research_distill 判定。当前 36 条两字段在 unverified 上一致，无实际冲突。
- 修复方向：长期统一 research_distill 单一来源，research_use 只作说明。

### M-3 DistillCase dataclass 默认值仍是 "total"

- 位置：`src/bodydonor/distill_data.py:229-230`。
- 复现（读码）：`label_source: str = "total"`、`teacher_task: str = "total"` 是构造默认值，非读路径缺省；prepare_dataset 会在门禁通过后覆盖。将来直接构造 DistillCase 会静默落 total。
- 默认值改 `""` 或去掉默认值。

### M-4 未注册任务拒绝消息带许可文档引用

- 位置：`src/bodydonor/license_registry.py:550-553`。
- 复现：实跑 `check_task('never_registered')` → `"task 'never_registered' not in teacher registry (register as a registry entry, see LICENSES-THIRD-PARTY.md)"`——含 LICENSE 子串。ready=False 消息本身干净；分层承诺只对 ready=False 成立。
- 修复方向：改"see registry docs"或去掉文档引用。

### M-5 R1 报告行号已过时（⑥ 提交偏移）

- 位置：`M7-S3R1-REPORT.md:86`（"export_seg.py:286/290"）。
- 复现：当前源码对应位置是 :293/:297（d91443f 在文件前部新增 7 行）。R1 提交时行号正确，HEAD 上已对不上。
- 修复方向：注明"R1 提交时的行号"或更新为当前行号。

### M-6 R2 报告 TS 族条目计数错误（20 应为 24）

- 位置：`M7-S3R2-REPORT.md:51`。
- 复现：实跑计数 TEACHER_REGISTRY 中 teacher_id=totalsegmentator：TS 族 30 条，allowed 24 / unverified 6；全部 36 条 allowed 25 / unverified 7 / conditional 4。报告写"TS 族（20 条目）allowed"错。
- 修复方向：20→24，并核对 TS 族总数表述。

### M-7 R2 报告声称的"许可措辞否定断言"测试不存在

- 位置：`M7-S3R2-REPORT.md:33`（声称）vs tests/ 实况。
- 复现：grep tests/ 无"断言 '许可'/'BLOCKED'/'license' 不在消息内"的测试；test_teacher_registry.py 只有 `"BLOCKED" in msg` 正向断言。声称的验证手段未落地。
- 修复方向：补该断言测试，或修正报告描述。

## 4. 核对通过项

- **W4 修复（①）**：ingest.py:365-366 新表达式逐输入类推（缺失/None/空串/空白/CT）全部正确；`""` 用例确实钉住旧代码 `str(None)→"None"` bug（旧代码对空串同样产 "None"）。**空 MultiValue 边界**：S3R1 报告未列，实跑 pydicom 归一化为 `''` → 新表达式得 None，实测安全。
- **`str(getattr` 全仓 11 处**（deid.py:76；ingest.py:323/347/420/469/705；nni_runner.py:293；export_seg.py:293/297；burned_ocr.py:71/76）：其余均为 UID/BIA/描述类（缺省 ""/"UNKNOWN" 语义），无同款 bug。R1 报告清单与之一致（export_seg 行号偏移见 M-5）。
- **来源默认值（②）**：read_teacher_provenance 缺文件 → `{"provenance_missing": True}`，无默认 total；读回显式写 `provenance_missing=False` 覆盖伪造键；`provenance_file` 键仓内 0 残留；读路径无默认回 total（写路径 `write_teacher_source_json` 不动，eval/ task="total" 确为写路径硬编码）；`prov.get(` 全部带 ""/False 兜底不 KeyError；manifest provenance_convention 文本与新语义一致。
- **ready 三分（③）**：三分结构真实存在——check_task（license_registry.py:516-554）只读 blocked_tasks/modalities/ready；research_use/research_distill 不参与部署判断，无混层；ready=False 消息逐字一致且纯技术原因；模态不符消息纯技术；diff 对 ready 值零改动；`total` 条目 ready=True + modalities=('CT',)，total 推理不受影响；worker 调用链（run_total→check_worker_task）无绕过 TS checkpoint 加载； Lung_vessels 拒绝消息与报告一致。**ready 计数实证：36 条目，ready True=2 / False=34**；TS ready=False=29（其中 S3F2 三个组登记的 24 个均含在内）——报告"24 个新登记任务 ready=False"指 S3F2 登记口径，与代码相符。
- **许可结构化（④）**：8 条目字段值逐项与 M7-LICENSE-CHECK 一致（SynthSeg/MOOSE conditional 未误标 blocked；nnInteractive conditional；FleXray CC BY-NC 4.0；vertebrae_body/teeth unverified）；三字段行与 M7-PLAN-v3 §2.2.1 逐字一致（枚举、字段名、"known_restrictions 只作说明文字"明文）；known_restrictions 判定参与=0（仅 validate_entry 列表校验与测试）；`_restricted_license_label` 无残留引用；ontology.py 新逻辑（distills 集合、inherits 聚合、混合条目）正确；validate_entry `_REGISTRY_FIELDS` 含三新字段，缺字段检查在前，测试 match="缺字段" 可过；测试临时改内存条目 finally 恢复无泄漏；36 条目计数 (36,36,36) 实证。
- **X-ray（⑥）**：三 UID 逐字符正确（DX `...1.1.1.1`、CR `...1.1.1`、CT `...1.1.2`）；SEG 自身 Modality 恒 SEG 核对存在；test_export_seg.py 三 DX/CR 用例真实断言 SOP Class+Modality。
- **报告事实**：R1 116 passed（无 119——任务清单里提的 119 在两报告中均不存在，疑为任务清单笔误）；R2 全量 671 passed/0 F/E 经 JUnit XML 实证（tests=671 failures=0 errors=0 skipped=0，日志 exit=0）；sha256 `b3ac4421f85457bb` 与 license_registry.py:136 完整值前缀一致；amos_0004 117 类/128.7s/8580.3 MiB 无仓内原始证据（见未证实项）。
- **真实目录缺 source.json 不影响全量测试**：prepare_dataset 仅 test_distill_license_gate.py 调用且全部 tmp_path fixture；真实目录消费者（m6e/m6c `_teacher_volume_and_classes`）直读 ts_out NIfTI 不读 source.json，且 local_artifacts+skipif 双门控（13 处标记，pyproject.toml:62 注册）。
- **红线**：审查全程未改代码/测试/配置；未跑全量 pytest（仅 -I 实跑单函数复现）；未碰 GPU/真实推理；不推远端/merge/PR。

## 5. 未证实项

1. **amos_0004 ③ 验收数字（117 类/128.7s/8580.3 MiB）**：仓内无本次推理的日志或 JSON 产物；8580 MiB 与历史基线（M2-ALIGN/M3-REPORT）吻合但 elapsed 与历史 116.2s 不同。按"未留存原始证据"处理，不作为本轮实测证据采信。
2. **b-U3（S3 B1 边界）**：eval/evaluate_holdout.py:84 的 `run_total(...)` 调用未传 modality（实测读码）；qc_coldstart.py 传了 `modality="CT"`。evaluate_holdout 不传 modality 时 check_task 退化为仅 task 门控——对 MR 序列可能放行 CT-only 任务。已知边界非本轮改动，未深入。
3. **SynthSeg/MOOSE conditional 取舍是否经用户确认**：代码无法回答；若未确认，W-5 的修复方向需要用户拍板。
4. **CI 是否会跑 local_artifacts 用例**：pyproject 注册标记但本轮未验证 CI 配置跳过行为（R2 报告声称"CI 跳过"）。
5. **`save_dicom` 写 Modality="" 后 ingest 是否在 :366 前被其他原因跳过**：未验证（分片 a 未证实项，无功能影响——test_modality_label_values 空串用例已全量绿过 671）。
