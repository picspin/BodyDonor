# M7-S3P2-R1b 轮次报告：③ SOPClassUID 原样回填，删"默认 CT"设计

基线：R1a HEAD `82f5d35`。本轮 3 个提交（`b2422f0` 实现 + `a9e53ef` 审查 W1 修复 + `363f310` 审查 W2 修复）。

## 提交列表（实测）

| 提交 | 内容 | diffstat |
|------|------|----------|
| `b2422f0` | ③ SOPClassUID 原样回填，删默认 CT 设计 | 5 文件，+305/−25（export_seg.py +89/−x、ingest.py +14/−x、pipeline.py +3、test_export_seg.py +8/−x、test_sop_class_uid.py +216 新增） |
| `a9e53ef` | 审查 W1——真实实例缺 SOP Class UID 时 raise | 2 文件，+29/−3 |
| `363f310` | 审查 W2——混合 SOP Class 序列导出前断言 | 2 文件，+40 |

## ③ 逐项证据（文件:行号均亲读）

### 1. 先补提取

- `src/bodydonor/ingest.py:474`：`extract_source_instances` 循环内加 `"sop_class_uid": str(getattr(ds, "SOPClassUID", "") or "")`；docstring（:462-466）同步：空串=头缺失/旧持久化 JSON 无此键。
- `src/bodydonor/pipeline.py:336`：`source_info_from_persisted` 透传 `inst.get("sop_class_uid", "")`——旧 JSON 无键为空串，按"无原样 UID"走 raise 分支（向后兼容，不写迁移脚本）。
- `SourceSeriesInfo.source_instances` docstring（`export_seg.py:56-57`）注明新可选键。

### 2. 单点解析

- `src/bodydonor/export_seg.py:96` `resolve_source_sop_class_uid(source, inst)`——唯一解析点：
  - (a) 实例自带非空 `sop_class_uid` → 原样用（逐字 round-trip）；
  - (b) 合成源（`inst is None`）→ `_SYNTHETIC_SOP_CLASS_UIDS`（:88，DX/CR/MR/CT 四模态 fallback）；
  - (c) 真实实例缺 UID（W1 修复后）或未映射模态 → `SegExportError`，绝不默认 CT。
- `_build_source_images` :259 改调 helper，`meta.MediaStorageSOPClassUID`/`ds.SOPClassUID` 取 helper 返回值。旧映射 + 默认 CT（原 :223-226）已删。
- `_normalize_modality` 未动（模态归一，非 SOP Class 默认）。

### 3. verify_seg / export_and_verify

- `verify_seg` 加 `expected_source_sop_class_uid` 参数（:318）：非空时逐字比对 `ReferencedSOPClassUID`（:390-394），给出时**取代**反查模态核对（:380 分支结构）——反查表对 Enhanced MR/PET 各档必误报，只能原样比对。旧 `expected_source_modality` **保留**（既有直调测试依赖，决定见提交信息）。
- `export_and_verify`（:448-456）改传该参数，取值与 `_build_source_images` 同一 helper——建成与期望永不分叉。

### 4. 清查

`grep -rn "1.2.840.10008.5.1.4.1.1.2" src/ scripts/`：生产代码仅剩两处：`export_seg.py:92` 合成源 fallback 表（仅合成源用，允许）与 `:418` 反查表（verify_seg 模态反查用，允许）。`tests/factories.py`、`tests/test_burned_ocr.py` 里的 `…1.2` 是 fixture 数据（未动）。导出链路无其他"默认 CT"回退——已查，无。

## 测试（实测数字）

- `tests/test_sop_class_uid.py`（新增，22 项）：全部通过
- `tests/test_export_seg.py`（15 项）+ `tests/test_source_instances.py`（10 项）+ `tests/test_m7_s3f_modality.py`（15 项）
- **62 passed, 0 failed**（`-p no:warnings` 实测）；ruff 全仓 `All checks passed!`

派遣要求覆盖：

| 要求 | 测试 | 证据 |
|------|------|------|
| Enhanced MR `…4.1` 逐字 round-trip | `test_verbatim_resolve_layer_multiframe[1.2.840.10008.5.1.4.1.1.4.1]` | :103，resolve 层原样通过（多帧 IOD 边界见下） |
| PET 三档 `…128`/`…130`/`…128.1` | `test_verbatim_sop_class_in_seg[1.2.840.10008.5.1.4.1.1.128]`（全链路）+ `test_verbatim_resolve_layer_multiframe`（`…130`/`…128.1`） | :74/:103 |
| 未映射模态 raise | `test_unmapped_synthetic_source_raises`（PT/US/NM/MG/XA 参数化）+ `test_pt_real_instances_without_uid_raises` | :170/:183 |
| DX/CR/MR 合成 fallback | `test_synthetic_fallback_mapped`（DX/CR/MR/CT/X-ray 别名 5 档） | :254 |
| MR 旧测试改写 | `test_export_and_verify_raises_on_modality_mismatch` 期望改为 match="源 SOP Class"（逐字比对先于反查模态）；其余 MR 测试（`test_mr_source_gets_mr_storage`/`test_lowercase_mr_source_normalized`/`test_export_and_verify_checks_source_modality`）未动——行为不变 | test_export_seg.py |

**多帧 IOD 边界（如实记录）**：Enhanced MR（`…4.1`/`…4.4`）与 PET 后两档（`…130`/`…128.1`）是多帧 IOD，highdicom 拒绝其作为单帧 source images（`ValueError: Only one source image should be provided in case images are multi-frame images.`）——首轮实测暴露。这些档位只能在 resolve 层测原样 UID（`test_verbatim_resolve_layer_multiframe`）；全链路 round-trip 用单帧 PET `…128`（`test_verbatim_sop_class_in_seg`）。这是 highdicom 行为非本项目缺陷。

## 审查 findings 与处理

deep-reviewer 实审：0 blocker，2 warning，两条均 double-check 复现属实并已修：

1. **W1（`a9e53ef` 修复）**：`resolve_source_sop_class_uid` 对真实实例（`inst` 非 None）缺 `sop_class_uid` 的情况继续走模态映射——旧持久化 JSON 的 Enhanced MR 源会静默写成 MR Image Storage（`…1.1.4`），`verify_seg` 与同一 resolve 比对验不出。复现实测返回 `1.2.840.10008.5.1.4.1.1.4`。修法：`inst` 非 None 且 UID 空 → SegExportError 提示重跑 ingest 回填；仅 `inst is None`（合成源）走 fallback。失败测试 `test_real_instance_missing_uid_raises`（MR/CT 两档）先红后绿。
2. **W2（`363f310` 修复）**：SEG 逐帧引用各自实例的 SOP Class（实测混合序列引用序列两项 UID 不同），`verify_seg` 只检查 `ReferencedInstanceSequence[0]`——混合 SOP Class 序列帧 1 错配验不出。复现实测引用序列 `[CT, MR]`。修法：`_build_source_images` 导出前断言全序列 resolve 一致，不一致 SegExportError（一个 series 一个 SOP Class 是 DICOM 惯例）。失败测试 `test_mixed_sop_class_sequence_raises` 先红后绿。

审查未覆盖项（reviewer 只读码未实跑，本人已实跑补足）：61 次测试全绿以实测为准。

## 未决清单

1. **旧持久化 JSON 无 `sop_class_uid` 键的真实引用序列**：导出现在会 raise（W1 修复后），需重跑 ingest 回填（R2 已回填的 45 目录的 source.json 无此键——下次 ingest 重跑或补写脚本时带上）。
2. **`expected_source_modality` 与 `expected_source_sop_class_uid` 同时给出时**：后者取代前者核对（设计取舍，已写进 verify_seg docstring）——若将来要两道都核对，需拆分支。
3. **多帧 IOD 源的 SEG 导出**：Enhanced MR/PET 后两档作为源时 highdicom 拒绝——如研究方向扩到这些模态，需多帧 SEG 路径（highdicom 支持，本项目未接）。
4. **R2 库级清单 W2（继承自 R1a 报告）**：库级清单对新增 case fail-open，未在本轮范围。
