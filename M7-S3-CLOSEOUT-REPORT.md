# M7-S3 收口轮定稿报告（S3 收口最后一轮：S3R5）

- 日期：2026-11-16。基线：S3R4 HEAD `95871cb`（审查 findings 已修复，7 文件 133 passed）。
- 分支：`feat/m7-s3-closeout`（worktree `~/work/BodyDonor-S3R`）。Ponytail full 激活。
- 本轮两件事：(a) W-2 接线收尾（第 4 轮留下的唯一未接线项）；(b) 全量重跑带 JUnit XML + 本定稿报告。做完本轮 S3 收口轮结束。

## 1. 分支与提交列表（S3R1–S3R5）

S3R1（基线 `525939b` → `cb4f8fd`）：

- `ecd7abc` fix(m7-s3r1): W4 真修——DICOM Modality 缺失标签得 None 非字符串（str(None) bug）
- `e79007d` fix(m7-s3r1): 来源默认值退役——缺 source.json 记 provenance_missing，不默认 total
- `4472ef4` docs(m7-s3r1): 报告措辞修正——collect-only 数字只写 collected 不写 passed/全绿（⑤）
- `cb4f8fd` docs(m7-s3r1): S3R1 轮次报告——W4 真修 + provenance_missing + 措辞修正汇总

S3R2：

- `c164a84` feat(m7-s3r2): ④ 许可判定结构化——注册表新增 research_distill/restriction_basis/output_license_inherits
- `d91443f` fix(m7-s3r2): ⑥ X-ray 导出修正——DX/CR 保留源 SOP Class，SEG 自身模态与源影像模态分开核对
- `2e6001a` chore(m7-s3r2): ruff F401 清理——test_export_seg 未用 import pydicom
- `6c9e09f` docs(m7-s3r2): S3R2 轮次报告——③验证走通 + ④结构化 + ⑥导出修正汇总
- `f71511f` docs(m7-s3r2): 报告补记实测数字——全量 671 passed / 0 F/E / 0 skipped（JUnit XML 存档）

S3R3（审查轮，只审不改，清单见 `M7-S3R-REVIEW.md`）：

审查轮无代码提交。

S3R4（修复轮）：

- `993e753` fix(m7-s3r4): W-1/W-2 X-ray SOP Class 归一化 + verify_seg 源影像模态核对
- `1390efd` fix(m7-s3r4): W-3/W-4/M-3 蒸馏门禁 fail-closed——total 缺 task 拒绝 + prepare_dataset 层测试
- `d69888a` fix(m7-s3r4): W-5/W-6/M-1/M-2/M-4/M-7 注册表与 ontology 判定收口
- `12c1faa` docs(m7-s3r4): W-7 nnInteractive 修正路径门控缺口明示（不加运行时门禁）
- `188be7c` docs(m7-s3r4): W-7/M-5/M-6 报告修正——门控覆盖表述 + 行号注明 + TS 族计数
- `95871cb` docs(m7-s3r4): S3R3 审查清单入版本库 + S3R4 轮次报告

S3R5（本轮，`e874b2d`）：

- `e874b2d` feat(m7-s3r5): W-2 接线收尾——export_and_verify 透传源模态核对 + 定稿报告

- S3R5-W2：W-2 接线收尾——`export_and_verify` 透传 `expected_source_modality`（先失败测试 → 接线 → 跑绿）+ `_normalize_modality` 助手（"X-ray" 别名在导出与核对两侧同语义）
- S3R5-REPORT：R4 报告计数更正（ontology 合计 31→26）+ 本定稿报告

## 2. S3R5 本轮改动

### (a) W-2 接线收尾

- `src/bodydonor/export_seg.py` `export_and_verify`：`verify_seg` 调用新增 `expected_source_modality=_normalize_modality(source.modality)`——导出链路默认核对源影像模态（S3R4 报告未决问题 1 的原定后续）。
- `_normalize_modality` 助手（一行语义，W-1 同源）：`"X-ray" → CR`——`_build_source_images` 内部别名归一化与核对侧期望值同源，避免别名源被误报不符。
- 测试先行（deep-reviewer 三约束）：`tests/test_export_seg.py` 新增 `test_export_and_verify_checks_source_modality`——先写失败测试实跑（第一次 `NameError: SegVerifyError` 未导入，修正导入后 `DID NOT RAISE SegVerifyError`，退出码 1），再接线，再实跑通过（37 passed，3 个 seg 相关文件）。
- **接线后无既有测试变红**：37 passed / 0 F / 0 E（`test_export_seg.py` 12 + `test_source_instances.py` 10 + `test_m7_s3f_modality.py` 15，-p no:warnings -rN，pytest 9.2.0）。CT 源（SOP Class=CT）核对通过；DX 源核对通过（SOP Class 映射反查 DX）；无调用方数据模态不一致需要逐项写明。
- **现状边界如实记录**：MR 源（合成源实例 SOP Class 落默认 CT Storage，`_build_source_images` 映射只有 DX/CR 两键）在接线后核对会报"源影像模态 CT != 期望 MR"——本测试用例即以此边界钉住（MR 源报错是核对生效的表现，不是缺陷；若 MR 源需走导出链路，映射需补 MR 键，属后续工作，本轮不扩范围）。

### (b) R4 报告计数更正

- `M7-S3R4-REPORT.md` "测试数字"：`test_ontology.py + test_api_ontology.py（合计 31）` → **合计 26**（本轮实测 `--collect-only`：test_ontology.py 20 + test_api_ontology.py 6），注明实测口径；受影响合计 133 不变。

## 3. 六项验收证据逐项对照

| # | 验收项 | 证据 |
|---|---|---|
| ① | W4 五类用例 | `ecd7abc` + `tests/test_ingest.py` `TestDicomModalityMissing`（parametrize 4 值 + 显式 None 断言）：正常 CT→"CT"、空串→None、空白→None、None→None、标签缺失→None（`rec.modality is None` 断言）。S3R1 报告 §2 逐值表；本轮无改动，仍钉住 |
| ② | provenance_missing | `e79007d`：`read_teacher_provenance` 缺 source.json → `{"provenance_missing": True}`，无默认 total；`prepare_dataset` 预检拒绝（S3R4 W-3 补 prepare_dataset 层测试 `test_prepare_dataset_rejects_missing_source_json`）；补录路径 backfill 依据实测读回。本轮无改动 |
| ③ | total 研究推理实测 + 缺权重技术原因 | **协调方独立复跑日志 `s3r-verify-total.log`（VM `~/workspace/bodydonor/`）**：amos_0004（series nifti-b63b1d93，CT 78 层）task=total → OK，**n_classes=117，elapsed_s=119.99（≈120.0s），peak_memory_mi_b=8580.3**（另有 nvidia_smi 口径 8281.0），GPU 锁获得/释放齐全，labels.json 落盘。本轮全量重跑进一步证明测试面绿（§4）。缺权重侧：ready=False 拒绝消息纯技术原因（`checkpoint 未部署`，M-7 补否定断言测试）+ nni FileNotFoundError 实测（S3R2 报告 ③） |
| ④ | 36 条目新字段 + 子串删除 | `c164a84`：36 条目 `research_distill`/`restriction_basis`/`output_license_inherits` 三字段齐全（`test_registry_36_entries_new_fields_complete` 逐项计数 36,36,36）；"蒸馏阻断"子串匹配与 `_restricted_license_label` 查表已删（`test_known_restrictions_substring_no_longer_decides`）；S3R4 W-1/W-5/W-6/M-1 收口（known_restrictions 退役为说明文字、conditional 一致性、interactive_correction teacher 来源拒绝、conditional 必须带 inherits）。本轮无改动 |
| ⑤ | JUnit XML + `__file__` 附录 | 本轮全量跑 JUnit XML `m7-s3r-fulltest-junit.xml`（worktree 根；数字见 §4）+ 附录 A 五条模块路径（全部指向 worktree src，证明测的是本分支源码） |
| ⑥ | DX/CR + SEG/源模态分开核对 | `d91443f`：DX 源 SOP Class=DX Storage（`...1.1.1.1`）、CR 源=CR Storage（`...1.1`）、SEG 自身 Modality 恒 SEG 另行核对；`993e753`（W-2）：`verify_seg` 从 ReferencedSOPClassUID 反查源影像模态（DX/CR/CT 映射，未知 UID 原样带出不猜）；**本轮 S3R5：`export_and_verify` 透传期望模态——导出链路默认核对**，DX 源期望 DX 通过 / 期望 CT 报"源影像模态 CT != 期望 DX"（`test_verify_seg_checks_source_modality` + `test_export_and_verify_checks_source_modality` 双钉住） |

③ 的数字口径说明：S3R2 实施方报 128.7s，协调方复跑 120.0s——两次独立实测均 n_classes=117、峰值内存 ≈8.6 GiB（与 M2-ALIGN/M3-REPORT 历史基线吻合）；elapsed 差异是运行时波动，以协调方日志 `s3r-verify-total.log` 为采信证据。

## 4. 测试数字（本轮实测）

- **全量**：`tests/` 全部，`--junitxml=m7-s3r-fulltest-junit.xml`，`-p no:warnings -rN`，串行，GPU 锁空闲（`~/data/BodyDonor/.gpu.lock` 不存在），PYTHONPATH 指 worktree src（附录 A 核实）。
  - **678 passed in 2602.35s (0:43:22)，exit=0**——四者一致：exit code 0；点数（dots 逐行合计）678；尾行 summary `678 passed`；JUnit XML testsuite `tests=678 failures=0 errors=0 skipped=0`（timestamp 2026-11-16T01:19:47+08:00）。
  - 首次后台跑被 10m 时限 kill（进度 31%，JUnit 未生成）——重启后 2h 时限跑完，与 R2 轮教训一致（管道化 pytest 输出会丢，直接写日志文件 + 长时限）。
- **受影响文件计数（逐文件 collect 实测求和，非抄实施方数字）**：

| 文件 | collected |
|---|---|
| tests/test_export_seg.py | 12 |
| tests/test_source_instances.py | 10 |
| tests/test_m7_s3f_modality.py | 15 |
| tests/test_distill_license_gate.py | 55 |
| tests/test_teacher_registry.py | 21 |
| tests/test_m5e.py | 5 |
| tests/test_ontology.py | 20 |
| tests/test_api_ontology.py | 6 |
| **合计（8 文件）** | **144** |

  8 文件合并跑：**144 passed / 0 F / 0 E，exit=0**（-p no:warnings -rN，3.62s）。R4 报告的 7 文件 133 是 7 文件口径（无 test_source_instances.py）；本轮 W-2 接线波及 seg roundtrip 路径，`test_source_instances.py`（10）一并纳入受影响面，合计 144。
- W-2 接线直接相关 3 文件：37 passed（见 §2(a)）。
- ruff：改动文件全绿（`All checks passed!`）。

## 5. 未决问题（如实清单，不写"全部修复"）

1. **W-7 nnInteractive 修正路径门控缺口（已知未接）**：`nninteractive_backend.correct_image` 无注册表门控（清单明确不加运行时门禁）；当前 interactive_correction ready=True 无实害，蒸馏防线在数据侧（sidecar source + 蒸馏门禁）。若将来 ready=False，需在 correct_image 入口补 check_task 或依赖数据侧防线。
2. **20 AMOS + 25 CT-Lite 真实目录缺 source.json（待数据侧补录）**：本轮实测 `~/data/BodyDonor/downloads/amos22_subset/` 41 个 AMOS 目录中 20 个缺 `source.json`（amos_0001…amos_0033，含 amos_0004）、`ct_lite/ts_out/` 25 个 series 全部缺；`storage/amos_0004/` 亦无。缺省读回记 `provenance_missing`（fail-closed，不默认 total），不阻塞测试（prepare_dataset 仅 tmp_path fixture 调用）；真实目录要进蒸馏流水线需数据侧补录 source.json（backfill 依据）。
3. **b-U3（S3 B1 边界，已知边界）**：`eval/evaluate_holdout.py:84` 的 `run_total(...)` 未传 modality——check_task 退化为仅 task 门控，对 MR 序列可能放行 CT-only 任务（`qc_coldstart.py` 传了 `modality="CT"`）。已知边界非本轮改动，未深入。
4. **CI local_artifacts 跳过（已验证，非未决）**：`.github/workflows/ci.yml:35` 实测为 `uv run pytest -v -m "not local_artifacts"`——依赖本地产物的测试 CI 跳过（S3R3 审查未证实项 4 就此收口）。注意 CI 用 `uv run` 是 CI 环境自身约定，与本仓本地纪律（不用 uv run）不冲突。
5. **M-2 两字段并存（长期统一项）**：`research_use` 与 `research_distill` 两字段并存，判定唯一来源 `research_distill`（`research_use` 只作路由/说明，M-2 文档澄清已落地）；长期统一为单一来源属后续重构，本轮不动。
6. **MR 源导出链路边界（本轮 W-2 接线新如实记录）**：合成源实例 SOP Class 映射只有 DX/CR 两键，MR 源落默认 CT Storage——接线后导出链路对 MR 源会报模态不符（核对生效的表现）。MR 源若需走导出链路，映射需补 MR 键（本轮不扩范围，测试已钉住该边界）。
7. **研究流水线摸不到注册表门控（W-6 修复后的残余边界）**：`distill_data.py` 只对 source=total 走注册表；teacher 来源的 TS 任务不经 registry 判定（数据侧 sidecar + 蒸馏门禁覆盖）。

## 6. 报告措辞与红线核对

- 已修的写已修、已知缺口写已知缺口（§5 未决清单 7 项）；无"全部修复"类绝对表述。
- 红线核对：本轮未加新合规门禁；未改 commercial 放行语义；未改任何 ready 值；未改任何 research_distill 值；未重核许可原文；未发明新科研禁令；未对外宣称临床性能。
- 未提交真实 hold-out JSON（`eval/holdout_results.json` 仍 gitignored，实测 `git check-ignore` 命中）；依赖本地产物的测试保持 `@pytest.mark.local_artifacts`（9 文件 13+ 处标记），CI 跳过（§5.4）。
- 全量跑期间未并行跑其他 pytest；未推远端、未 merge、未开 PR。

## 附录 A：导入模块路径（全量同一 PYTHONPATH 实测）

```
/home/gogvf/work/BodyDonor-S3R/src/bodydonor/__init__.py
/home/gogvf/work/BodyDonor-S3R/src/bodydonor/license_registry.py
/home/gogvf/work/BodyDonor-S3R/src/bodydonor/ontology.py
/home/gogvf/work/BodyDonor-S3R/src/bodydonor/distill_data.py
/home/gogvf/work/BodyDonor-S3R/src/bodydonor/ingest.py
```

五条全部指向 worktree `~/work/BodyDonor-S3R/src`——全量测的是本分支源码（教训沿用：worktree 重跑前先核 PYTHONPATH）。

## 附录 B：协调方独立复跑日志要点（`s3r-verify-total.log`）

```
[gpu_lock] 获得：pid=499527 reason=batch task=total job=seg-nifti-b63b1d93-4e9d0c
series row: [('nifti-b63b1d93', 'amos_0004', 78, 'CT')]
RESULT: SegmentationResult(series_id='nifti-b63b1d93', case_id='amos_0004', task='total',
  mask_key='amos_0004/nifti-b63b1d93/mask.nii.gz', n_classes=117, ..., elapsed_s=119.988…,
  peak_memory_mi_b=8580.3, peak_memory_nvidia_smi_mi_b=8281.0,
  labels_json_key='amos_0004/nifti-b63b1d93/labels.json', ...)
[gpu_lock] 释放：pid=499527
elapsed_s: 166.6
```

（VM `~/workspace/bodydonor/` 原件；class_names 117 类全列见日志原文，含 prostate、vertebrae 全族、rib 全族等。）
