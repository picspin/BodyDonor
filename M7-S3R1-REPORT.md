# M7-S3 收口轮第 1 轮报告：W4 真修 + 来源默认值 + 报告措辞（①②⑤）

日期：2026-11-15。基线：S3F HEAD `525939b`（后续三项已完成，全量 657 绿）。
分支：`feat/m7-s3-closeout`（worktree `~/work/BodyDonor-S3R`）。
Ponytail full 激活（transcript `hook_success` + `PONYTAIL MODE ACTIVE`）。

## 1. 提交列表

| 提交 | 内容 |
|------|------|
| `ecd7abc` | fix：W4 真修——DICOM Modality 缺失标签得 Python None 非字符串 `"None"`（①） |
| `e79007d` | fix：来源默认值退役——缺 source.json 记 `provenance_missing`，不默认 total（②） |
| `4472ef4` | docs：报告措辞修正——collect-only 数字只写 collected（⑤） |

## 2. ① W4 真修（`ecd7abc`）

`ingest.py:364-366`：旧写法 `str(getattr(first_ds, "Modality", None) or None) or None`
缺失标签时 `str(None)` 得字符串 `"None"`——不是 Python None，注释声称"缺失标签 ->
None"是错的。改为用户给定形式：

```python
raw = getattr(first_ds, "Modality", None)
modality = (str(raw).strip() or None) if raw is not None else None
```

**测试**（先写失败→修→跑绿）：`tests/test_ingest.py` 新增
`TestDicomModalityMissing` 五类用例（parametrize 4 值 + 显式 None 断言）：

| Modality 标签 | 期望 SeriesRecord.modality |
|---|---|
| 正常 `CT` | `"CT"` |
| 空串 `""` | `None` |
| 空白 `"  "` | `None` |
| None | `None` |
| 标签缺失（`del ds.Modality`） | `None`（`rec.modality is None` 断言——修复点） |

修前 4 F（`-None`/`  -None`/`None-None`/missing_tag_is_none），修后全绿。

**grep 全仓同类 `str(getattr(...)` 模式**：其余均为 UID/描述类字符串字段（缺省
`""`）或带 `"UNKNOWN"` 缺省的 BurnedInAnnotation——语义本就是"缺失得空串/UNKNOWN"，
非 Modality 的 None 语义，无同款 bug，不动（deid.py:76、ingest.py:323/347/420/469/705、
nni_runner.py:293、export_seg.py:286/290、burned_ocr.py:71/76）。export_seg.py:290
的 `str(getattr(ds, "Modality", "")) != SEG_MODALITY` 是 SEG 对象校验（缺省 `""` 不等于
`"SEG"` 即拒绝），安全。

**边界**：本轮只修 DICOM 空值问题；未据此外推 NIfTI 的 B1（未动）。

## 3. ② 来源默认值（`e79007d`）

`distill_data.py read_teacher_provenance`（:247 起）：

- **缺 source.json** → 返回 `{"provenance_missing": True}`，不再默认
  `{"source": "total", "task": "total"}`——"来源未知"不得冒充"来源已知"。
- **补录路径**：source.json 存在且声明 `backfill: true` + `backfill_basis` 时实测
  读回并过门禁（历史目录显式补录来源并记录依据，不删不重做）。
- **写路径不动**：`write_teacher_source_json` 已写实来源（推理成功后调用）。

**下游逐项核对**（grep `{"source": "total"` / `prov.get("task"` / `默认 total`）：

| 位置 | 处理 |
|---|---|
| `prepare_dataset` 预检（:480-486） | `provenance_missing` 即 `DistillDataError` 拒绝——不进训练清单；先补录来源再跑 |
| `check_distill_source`（:94-141） | 无兜底 total——空 source 落 `'' 不在蒸馏训练白名单` 拒绝（测试锁定） |
| manifest `provenance_convention`（:600-603） | 文本同步：缺失即来源未知，预检拒绝，补录记 backfill 依据 |
| 过时 `provenance_file` 字段（:544） | 删除（读回语义改为 `provenance_missing`） |
| `mask_allowed_in_distill_training_strict`（:144-166） | 透传 check_distill_source，无默认 total |
| 可靠性统计入口（`teacher_reliability`，interaction_metrics.py:88） | 不读 provenance（按显式高风险类 + 低频计数判），无需改 |
| eval/*（ts_teacher_inference 等） | 写路径 task="total" 硬编码（非读缺省），不在本轮范围 |
| ontology.py:644 `mask_meta.get("task", "")` | 空串即阻断（研究路径 fail-closed 已有测试），非默认 total |

**测试**（先写失败→修→跑绿；47 passed）：

- `test_provenance_defaults_total_without_source_json`：缺 source.json →
  `provenance_missing is True` 且无 `source`/`task` 键。
- `test_provenance_missing_case_rejected_by_gate`：缺失来源过不了门禁
  （`'' 不在蒸馏训练白名单`）。
- `test_provenance_backfill_records_basis`：补录声明 backfill/backfill_basis
  实测读回且过门禁。
- 3 个既有测试更新：`_make_case_fixture` 写 source.json（与
  `run_distill_teacher_inference` 成功路径同形——fixture 模拟成功产出的 ts_out）；
  `test_prepare_dataset_rejects_partial_ts_out` 的部分目录补写来源（缺的是蒸馏类
  非来源）；`test_write_teacher_source_json_roundtrip` 断言跟随新读回语义。

## 4. ⑤ 报告措辞（`4472ef4`）

| 文件:行 | 修前 | 修后 |
|---|---|---|
| M7-S3-FOLLOWUP-REPORT.md:23 | 45 例全绿（collect-only 实测） | 45 collected（collect-only 实测；全量跑见 S3 收口最后一轮） |
| M7-S3-FOLLOWUP-REPORT.md:50 | "88 passed" 改 **71** | 71 tests collected（注明 collect-only 只写 collected） |
| M7-S3F2-REPORT.md:124 | 71 passed / 0 F/E，exit 0 | 71 tests collected（本轮不重复宣称 passed，全量跑在收口最后一轮） |

**核对不动**：M6 各报告（`569/571 collected 全绿 0 F/E`、`555 collected 两批全绿`、
`509 collected 全绿`）均为**全量实跑**（exit 0 / 分批重跑实录），非 collect-only
数字，合规。M4-STAGE2B-REPORT.md:149 `71 passed` 为 gpu_lock/distill/holdout 子集
真实复跑（6.54s），合规。本轮未跑全量（全量 + JUnit XML 在最后一轮）。

## 5. 测试与 ruff

- 受影响文件全跑：`test_distill_license_gate.py`（47）、`test_ingest.py`、
  `test_zip_streaming.py`、`test_zip_bomb.py`、`test_api_ingest.py`、
  `test_m7_s3f_modality.py`、`test_m5e.py` 共 **116 passed / 0 F / 0 E**（8.14s）。
- `bodydonor.__file__` 核实指向 worktree src
  （`/home/gogvf/work/BodyDonor-S3R/src/bodydonor/__init__.py`）。
- ruff：`test_ingest.py`、`src/bodydonor/ingest.py`、`test_distill_license_gate.py`、
  `src/bodydonor/distill_data.py` 全绿。
- 跳过项：无（本批测试均为合成 fixture，无 skipif 静默跳过）。
- GPU：未碰（本轮无真实推理测试）。

## 6. 红线核对

- 不加新合规门禁：provenance_missing 是既有准入检查（check_distill_source）的
  输入收紧，非新门禁。
- commercial 放行语义不变：`check_distill_source` 只放行 total 不变
  （`test_commercial_profile_regression_only_total` 仍绿）。
- 未改任何 ready 值、未重核许可原文、未发明新科研禁令、未宣称临床性能。
- 未提交真实 hold-out JSON；新测试全合成 fixture。

## 7. 未决问题

1. **历史 ts_out 目录补录**：真实数据
   `~/data/BodyDonor/downloads/amos22_subset/` 40 个 AMOS case 中 20 个有
   source.json（新一轮推理产出）、20 个缺失（M2/M3 旧产出）；CT-Lite
   `ts_out/` 25 个 case 全部缺失。这些目录现在过不了 `prepare_dataset` 预检
   （按设计——须先补录）。补录操作本身不在本轮（属数据侧操作），补录时写
   backfill_basis 说明依据。
2. `read_teacher_provenance` 的 `provenance_file` 键已删——若有仓外脚本读该键
   会 KeyError（仓内 grep 无引用，CI 测试已跟随）。
3. ③④⑥（ready 三分 / 许可判定结构化 / X-ray 导出）为后两轮，本轮未动。
4. 全量 pytest + JUnit XML + `__file__` 存档在最后一轮。
