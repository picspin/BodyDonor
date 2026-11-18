# M7-S3 合并后修复轮 R1 报告：① b-U3 + ② MR 导出补键

基线 `origin/main a8a5904`（PR #3 合并后），worktree `~/work/BodyDonor-S3P`，分支 `feat/m7-s3-postmerge`。两项独立提交，无 push/merge/PR。

## 任务 ① b-U3：ts_teacher_inference 显式传 modality=CT

**缺陷**：`eval/evaluate_holdout.py` 的 `run_total(...)` 调用未传 modality，`check_worker_task` → `LicenseRegistry.check_task` 在 `modality=None` 时跳过模态过滤，对 MR 序列可能放行 CT-only 任务（注册表 `task="total"` 仅 `modalities=("CT",)`）。

**修复**（提交 `7d0988d`）：
- `eval/evaluate_holdout.py:84-87`：`run_total` 调用补 `modality="CT"`（hold-out 是 CT 数据：CT-Lite + AMOS22 CT；与 `scripts/qc_coldstart.py:52`、`scripts/run_distill_teacher_inference.py:81` 同做法）。
- `tests/test_evaluate_holdout.py`：新增 `test_ts_teacher_inference_passes_modality_ct`——先写先红（`assert None == 'CT'` 坐实缺陷），修后绿。测试 monkeypatch `bodydonor.segmentation.run_total` 捕获 kwargs 钉住透传（fake runner 返回 `peak_vram_mb=None`，fake lock 提供 `release()`——`ts_teacher_inference` 用 `lock.release()` 非上下文管理器）。

**调用点扫描**（run_total / check_task / check_worker_task，只读核实）：
- `eval/evaluate_holdout.py:84` — 本轮已修。
- `scripts/qc_coldstart.py:50-52` — 已传 `modality="CT"`。
- `scripts/run_distill_teacher_inference.py:79-81` — 已传 `modality="CT"`。
- `src/bodydonor/segmentation.py:183`（run_total 内部）— 透传入参 modality。
- `src/bodydonor/segmentation.py:361`（segment_series）— 从 `row.modality` 取值传入（S3 B1）。
- 未发现其他漏传调用点。

## 任务 ② MR 源导出补键

**缺陷**：`src/bodydonor/export_seg.py` 的 `_build_source_images` SOP Class 映射只有 DX/CR 两键，MR 源落默认 CT Storage（`1.2.840.10008.5.1.4.1.1.2`）；S3R5 W-2 接线（`export_and_verify` 透传 `expected_source_modality`）后导出链路报模态不符——原 S3R5 测试把此钉为"已知未决项"。

**修复**（提交 `8e7980b`）：
- `export_seg.py:220-224`：`_build_source_images` 映射补 `"MR": "1.2.840.10008.5.1.4.1.1.4"`（MR Image Storage；pydicom UID 字典核实 `UID('1.2.840.10008.5.1.4.1.1.4').name == 'MR Image Storage'`，非记忆值）。
- `export_seg.py:347-352`：`verify_seg` 源模态反查映射补同 UID → `"MR"`（与正向映射互逆）。
- DX/CR/默认 CT 键不变；不改其他导出逻辑。
- `tests/test_export_seg.py`：
  - 新增 `test_mr_source_gets_mr_storage`：`_build_source_images` MR 源写 MR Image Storage（先红：`'1.2.840.10008.5.1.4.1.1.2' != '1.2.840.10008.5.1.4.1.1.4'`）。
  - 改写 `test_export_and_verify_checks_source_modality`：原钉"MR 落 CT Storage 报错"（未决项行为）改为两项——MR 源走 `export_and_verify` 全链路通过 + `verify_seg` 期望 CT 时报"源影像"不符（模态不符仍报错覆盖保留）。中途修过一处测试自身错误：`verify_seg` 返回带 errors 的报告不 raise（只有 `export_and_verify` 才 raise SegVerifyError），断言改为与 DX 测试同款的 `not rep2.ok + any("源影像" in e)`。

## 审查 findings

deep-reviewer 两分片对抗性审查（只读，每分片约 29k tokens）：无 Blocker。2 nit 全修（提交 `7546197`），1 Should 列入未决。

| # | 严重度 | 位置 | 问题 | 处置 |
|---|---|---|---|---|
| 1 | nit | `export_seg.py:76` | `_normalize_modality` 只处理 "X-ray"→CR，不做大小写归一；ingest 头标签只 strip 不 upper，源模态 "mr" 正向映射落默认 CT Storage（反向校验大声失败但 MR 永远无法通过） | **已修**：非别名值 `strip().upper()`（与 `license_registry.check_task` W8 同口径）+ `test_lowercase_mr_source_normalized` 钉住 |
| 2 | nit | `tests/test_export_seg.py` | `export_and_verify` 模态不符 raise 分支仅靠 verify_seg 直调间接覆盖，缺直接测试 | **已修**：`test_export_and_verify_raises_on_modality_mismatch`——monkeypatch export_seg 写出 CT 源 SEG 而声称 MR，断言 SegVerifyError |
| 3 | Should | `segmentation.py:361` | `check_worker_task(task, settings, modality=row.modality)` 在 `row.modality=None`（DICOM 标签缺失/空白，ingest 写 None）时退化为仅 task 门控，模态未知 series 仍可能派发 CT-only `total`；`:412` 的 run_total 也不传 modality，二次门控同样失效 | **超本任务范围，列入未决** |

已核实无问题项（摘）：
- ① 修复堵住 eval 路径 MR 放行；三条调用路径（eval/qc_coldstart/distill）均显式传 modality；monkeypatch 能拦住函数内 import；module 对象一致（sys.modules 共享）；语义与数据一致；测试断言有效（删参数即红）。`cli.py:245` 的 check_task 仅诊断不派发，无需改。
- ② 两处映射互逆且 UID 逐字一致，无第三处同族映射漏补；合成源实例字段对 MR/DX 同构（highdicom Segmentation 不读 PixelData，实跑已验证 13→15→17 tests 绿）；DX/CR/默认 CT 键与既有测试行为不变；SegVerifyError import 移除后无悬空引用。

## 未决项

- **`segmentation.py:361` NULL 模态缺口（Should）**：`row.modality=None` 时 `check_worker_task` 退化为仅 task 门控，模态未知 series 仍可能派发 CT-only 任务。属于既有设计残余缺口，需后续决定：NULL 模态 series 拒绝派发，还是显式降级并记录。
- **`_normalize_modality` 大写依赖真实数据**：生产 export 联调层（`source_info_from_persisted`）尚未接线，modality 实际取值待接线后验证。

## 测试数字

- `tests/test_export_seg.py`：collected 15 / passed 15
- `tests/test_m7_s3f_modality.py` + `tests/test_source_instances.py`（同批）：合计 38 / passed 38
- 受影响 6 文件合计（上两批 + `test_evaluate_holdout.py` + `test_evaluate_student_holdout.py` + `test_holdout.py`）：collected 74 / passed 74
- `tests/test_evaluate_holdout.py`：collected 7 / passed 7
- ruff（src/ eval/ tests/ scripts/）：全绿

## 提交

| 提交 | 内容 |
|---|---|
| `8e7980b` | ② MR 源导出补键——SOP Class 映射补 MR Image Storage |
| `7d0988d` | ① b-U3——ts_teacher_inference 显式传 modality=CT |
| `7546197` | 审查修复——_normalize_modality 大小写归一 + raise 分支直测 |
