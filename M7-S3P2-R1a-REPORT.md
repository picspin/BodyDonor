# S3P2-R1a 轮次报告：② run_total 签名层 fail-closed

基线 `4207899`（S3P HEAD），分支 `feat/m7-s3-postmerge-2`。本报告所有数字均为实测。

## 提交列表（5 个）

| SHA | 类型 | 内容 |
|---|---|---|
| `2a04e93` | fix | run_total modality 必填（keyword-only）+ resolve_modality 三档 fail-closed 解析 |
| `188b708` | fix | 三调用方迁移 resolve_modality，脚本不再硬编码 CT |
| `7f06ac4` | fix | qc_coldstart run_qc 入参也走数据集对照表，脚本零 CT 字面量 |
| `fb0a295` | test | resolve_modality 四档测试 + run_total TypeError 钉住 |
| `302a213` | fix | 审查 W——segment_series 将 resolve_modality 的 ValueError 转型 SegmentWorkerError |

## diffstat（实测 `git diff 4207899..HEAD --stat`）

```
 eval/evaluate_holdout.py                 |  11 ++-
 scripts/qc_coldstart.py                  |  10 +--
 scripts/run_distill_teacher_inference.py |   5 +-
 src/bodydonor/segmentation.py            |  77 ++++++++++++++++++--
 tests/test_evaluate_holdout.py           |  13 ++--
 tests/test_segmentation.py               | 117 +++++++++++++++++++++++++++++++
 6 files changed, 215 insertions(+), 18 deletions(-)
```

## ② 逐项证据

### 1. run_total modality 必填

- 签名：`src/bodydonor/segmentation.py:222-230`——`modality: str` 为 keyword-only 必填（`*` 后无默认值），其余默认值（task/device/settings/job_id）不动。
- 不传即 TypeError：`tests/test_segmentation.py:279` `test_run_total_modality_required`。

### 2. resolve_modality 三档 fail-closed（`src/bodydonor/segmentation.py:191`）

- a 档 `.modality` 属性（strip+upper，`MODALITY_ALIASES` 归一 PT→PET、DX/CR→X-RAY）：:196-199。
- b 档 DICOM 标签（`_dicom_modality` :174，ingest.py W4 口径：缺失/None/空串/字面 "None" 一律当缺失；`dcmread` 异常捕获返回 None）：:201-205。
- c 档数据集对照表 `DATASET_MODALITY` :167（`{"amos": "CT", "ts_public": "CT", "ct_lite": "CT"}`，每项依据注释在表上方；本轮唯一「数据集即 CT」的地方）：:208-216（`.source` 属性或裸数据集名字符串）。
- d 档 raise `ValueError("modality 无来源…")` :218-220——不猜、不默认 CT。

### 3. 调用方迁移（4 处生产 + 1 处测试 fake）

| 调用点 | 迁移 |
|---|---|
| `segment_series`（segmentation.py:422, :477 run_total） | 查 `row` 后 `resolve_modality(row)`，同值传 :425 `check_worker_task` 与 :477 `run_total`；顺带闭合 S3P 未决 #4（NULL 模态不再退化为仅 task 门控） |
| `ts_teacher_inference`（eval/evaluate_holdout.py:68 签名， :87 run_total） | 加必填 `modality`；主循环 :299 `resolve_modality(e)`（HoldoutEntry.source 走 c 档） |
| `ts_teacher_mask`（scripts/qc_coldstart.py:32 签名， :51 run_total） | 加必填 `modality`；主循环 :161 `resolve_modality("ct_lite")`（c 档裸名） |
| `run_distill_teacher_inference.py:82` | `modality=resolve_modality(c)`（DistillCase.source 走 c 档） |
| tests/test_evaluate_holdout.py:119 fake | `fake_run_total(image_path, out_dir, **kwargs)` 签名未变（**kwargs 兼容），测试改传 `resolve_modality(HoldoutEntry(...))` 并断言 `captured["modality"] == "CT"` |

三个调用方脚本内已无字面量 `"CT"`（grep 实测 EXIT=1 零命中）。

### 4. 测试覆盖（12 个新测试，tests/test_segmentation.py TestResolveModality）

| 测试 | 行 | 钉住 |
|---|---|---|
| `test_tier_a_attr` | :205 | a 档属性 + PT→PET 别名归一 |
| `test_tier_a_attr_no_alias` | :214 | a 档普通 CT 归一 |
| `test_tier_b_dicom_file` | :222 | b 档 DICOM 文件 Modality=MR |
| `test_tier_b_dicom_dir` | :233 | b 档 DICOM 目录取第一个文件 |
| `test_tier_c_dataset_table` | :245 | c 档 .source=amos → CT |
| `test_tier_c_bare_dataset_name` | :254 | c 档裸名 "ct_lite" → CT |
| `test_tier_d_raise` | :258 | d 档三档皆无 ValueError |
| `test_tier_d_dicom_missing_tag_raises` | :268 | b 档 DICOM 无标签且无 c 档 → raise |
| `test_run_total_modality_required` | :279 | run_total 不传 modality 即 TypeError |
| `test_segment_series_null_modality_raises_worker_error` | :284 | 审查 W1：NULL 模态 Series → SegmentWorkerError |

脚本调用方迁移验证：`tests/test_evaluate_holdout.py:113` 实跑断言 captured modality=CT（fake 路径）。

## 实测数字

- 受影响文件测试：`tests/test_segmentation.py tests/test_evaluate_holdout.py tests/test_teacher_registry.py tests/test_ts_runner.py` → **62 passed, 1 warning, EXIT=0**（其中 test_segmentation.py 21 passed、test_evaluate_holdout.py 7 passed）。
- W1 失败测试先行：修复前实跑 `ValueError: modality 无来源…` 于 segmentation.py:217（EXIT=1）；修复后 `pytest tests/test_segmentation.py -q` → 21 passed EXIT=0。
- ruff：6 个改动文件全绿（`All checks passed!`）。
- pydicom 3.0.2 下 DICOM 测试 3 个全 PASSED（审查 P1 实跑确认）。

## 审查 findings 与处理（deep-reviewer，1 个子代理）

| # | severity | 内容 | 处理 |
|---|---|---|---|
| W1 | warning | NULL 模态 Series 在 `resolve_modality` 抛 ValueError，而 app.py:697 / cli.py:402 只捕获 SegmentWorkerError → 端点 500、CLI traceback | **已修**：失败测试先行实跑复现 ValueError → `segment_series` 内 try/except 转型 `SegmentWorkerError(str(e))`（segmentation.py:424-426）→ 21 passed |
| P1 | plausible | DICOM 测试 pydicom 写法（FileDataset + enforce_file_format=False）在 pydicom 3.x 下可能 dcmread 失败 | **实跑不属实**：3 个 DICOM 测试（含 d 档缺标签）全 PASSED，无需修 |

审查确认无问题的项：三档顺序、MODALITY_ALIASES 归一（与 license_registry.py:558 同口径）、_dicom_modality W4 严格性、S3P 未决 #4 闭合、全仓签名迁移扫描（run_total 4 处、ts_teacher_inference/ts_teacher_mask 各 1 处全传）。

## 未决清单

1. **c 档数据集表是集中事实表，非每 case 实测**：`DATASET_MODALITY` 声明「amos/ts_public/ct_lite 数据集即 CT」。若后续要求每 case 实测来源，需改 `eval/holdout.txt`（case_id\tsource）或数据清单格式——本轮未改清单格式。
2. **`HoldoutEntry` 无 `.modality` 属性**：hold-out 条目永远走 c 档表；若 hold-out 引入 MR 数据，需扩表或加属性。
3. **`_dicom_modality` 只读第一个文件**：目录分支取 `sorted(glob("*"))[0]`，多序列混合目录不校验一致性（segment_series 的 row 场景 storage 已是 NIfTI，不走此分支）。
4. **上一派 R1a 的 b-U3「eval 传 modality=CT」字面量已由本项取代**：S3P 报告中的该条结论对应实现已变更（run_total 不再接受 None）。
