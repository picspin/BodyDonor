# M1-FIX-REPORT — BodyDonor M1 补丁（依据 M1-REPORT 评审意见）

> 日期：2026-11-07。commit：`4058f71`（M1-fix，本地 main，未推）。
> 依据：用户对 M1-REPORT 的评审意见（四点）。只做这四件事，不做 M2。
> 非交互运行，每步本地测试通过后继续；全部测试通过 + ruff 全绿后提交。

---

## 1. 做了什么

### 1.1 highdicom DICOM SEG 导出与校验（评审点 1，原提案 M1 任务 7，M1 漏了）

**`src/bodydonor/export_seg.py`**（新增）：mask（NIfTI）→ DICOM SEG，highdicom 生成
（BINARY 类型、AUTOMATIC 算法标识、源 series 引用、帧数与 mask 层数一一对应）。

- `SourceSeriesInfo`：源序列最小几何集（Series/Study UID、rows/cols、PixelSpacing、
  SliceThickness、ImageOrientationPatient、逐层 SOP 实例）。真实联调时传 ingest 读到的
  源实例列表；无实例信息时按源几何合成（合成源 Patient 标识无 PHI，帧序按 DICOM
  帧序惯例沿法向反向排布——见 §3 方向陷阱）。
- 拒绝静默重采样：mask 面内尺寸或层数与源不一致直接 `SegExportError`。
- **`verify_seg` 三道校验**：① pydicom 重读（SOPClassUID == Segmentation Storage
  `1.2.840.10008.5.1.4.1.1.66.4`、Modality == SEG、NumberOfFrames 对）；② highdicom
  segread 解析 + ReferencedSeriesSequence 源引用对；③ 与源 mask 逐体素 diff，
  **diff 为零才算通过**。
- `export_and_verify`：导出 + 校验一步，任何错误或 diff 非零 → `SegVerifyError`。
- highdicom 进 `ingest` extras（uv add，highdicom==0.28.2，带 pillow/pyjpegls）。
- roundtrip 测试（`tests/test_export_seg.py`，7 条）：NIfTI mask → SEG → 重读 →
  逐体素 diff=0；SOP Class/Modality/帧数断言；面内尺寸不一致 raise；层数不一致
  raise；篡改 mask 后 verify 抓到 diff>0；expected_series_uid 不匹配报错。

### 1.2 像素级 OCR 烧录扫描 + 选择器 bug 修复（评审点 2）

**（a）OCR 第二道关（`src/bodydonor/burned_ocr.py`，新增）**：

- **只扫两类目标序列**（按用户策略：普通螺旋 CT 轴位断层几乎不会有像素烧录，
  烧录文字 99% 出现在 Dose Report / Secondary Capture）：
  - Secondary Capture 家族：5 个 SOP Class UID（SC / 多帧 SC ×4）
  - Dose Report：SeriesDescription / StudyDescription / ImageType 含
    `dose report|dose summary|radiation dose|dose screen|protocol screen|ct dose|dosesheet`
    （大小写不敏感正则）
  - 普通轴位序列（CT SOP Class、无关键词）→ `is_ocr_target == False`，不扫。
- Tag 检查（`BurnedInAnnotation == YES`）仍为**第一道关**（deid.py 不变）；OCR 为
  **第二道关**，任一不通过即拒收：OCR 检出疑似烧录文字（敏感词正则：
  `patient|patient id|accession|dob|birth|dose report|ctdi|dlp|kvp|mas` 等）→
  文件级 skipped（不改写原数据），不进 series、不进下游。
- OCR 引擎：**rapidocr-onnxruntime**（pip 可装、onnxruntime CPU 离线推理、无 apt
  系统包），懒加载（首次调用才 import + 初始化，非目标序列零开销）。
- 单测（`tests/test_burned_ocr.py`，10 条）：合成烧录文字图（PIL 画 "DOSE REPORT /
  Patient ID: PID-12345"）→ SC DICOM → 检出（detected=True + 敏感词命中）；空白 SC
  未检出；轴位不扫；ingest 端到端剂量报告序列拒收（2 层 → skipped=2）+ 轴位对照。

**（b）选择器 bug 修复（M1-REPORT §3 实录）**：

- **根因**：`apply_rules` 就地排序 candidates（LOCALIZER/薄层排最后），但
  `report.series` 保持扫描序；CLI `_print_import_report` 用 `zip(candidates, series)`
  **位置对应** → 2 层 LOCALIZER 行显示了 40 层 candidate 的"选中"。
  复现路径：让 localizer 目录先被扫描（`a_localizer < series1`），表格第一行
  （2 层）标"选中"、第三行（40 层）标"排除：LOC…"——与 §3 实录一致。
- **修复**：CLI 报告改为按 **series_id** 从 `cand_map` 取排除状态（不 zip）；
  `ingest.apply_series_rules` docstring 写明禁止位置对应。
- 回归测试（`tests/test_selector_guard.py`，7 条）：2 层序列断言被排除（含扫描顺序
  敏感的复现场景）；排除状态按 series_id 一一对应；2 层不在默认选中列表。
- **commit 路径防线**：`pipeline.guard_min_slices` — 拒绝把 <30 层的序列交给下游
  （M2 的 TotalSegmentator 绝不能收到定位像残片导致 3D 卷积报错），除非用户显式
  `--include` 勾选覆盖（user_overrides 含该 id 才放行）。正常路径 apply_rules 已排除
  <30 层，防线是纵深防御（规则被绕过时兜底）。端到端测试：手工构造 outcome 绕过
  选择器 → commit raise `SliceGuardError`；显式勾选 → 放行且落库。

### 1.3 NIfTI 几何从"仅警告"改为强约束（评审点 3）

**（a）RAS+ 标准方向重排（`nifti_geom.reorient_ras`）**：

- DICOM → NIfTI 落盘路径（`pipeline._save_pixels`）落盘前做
  `nibabel.as_closest_canonical` 重排，重排描述（如 `LPS -> RAS（RAS+ 重排）`）
  写回 `SeriesRecord.reorientation`，CLI 导入报告"重排"行写明方向。
- 测试（`tests/test_nifti_geom_strict.py`）：LAS 方向图 → 重排后 axcodes RAS；
  已 RAS 图原样返回（"无需重排"）；重排后体素值按世界坐标不变（`arr[::-1]`）；
  **commit 落盘文件实测 axcodes == RAS**。

**（b）`assert_affine_match(img, mask)` 守卫（`nifti_geom`）**：

- mask 与原图 affine 不一致（元素级 |diff| > 1e-4 默认容差，浮点写入误差内放行）
  就 `raise AffineMismatchError`，禁止静默重采样。
- 测试：一致（NIfTI float32 写入往返误差内）通过；平移不一致 raise；方向不一致
  （RAS vs LAS）raise；容差边界（1e-5 放行 / 1e-2 拒绝）。
- 说明：当前仓库内 mask 读写只有 ingest 的 pixel_data（sitk，无 nibabel mask 路径），
  M2 的 mask 落盘/读回路径接入时必须过这个守卫（函数已就位 + docstring 写明约束）。

### 1.4 zip 预检改流式落盘解压（评审点 4）

**`ingest._ingest_zip_stream` 重写**：

- 原：`safe_open_zip` 三限预检 → **内存分派**（`zf.read(info)` 整成员进内存）→
  分派。院内大包导入爆内存。
- 新：`safe_open_zip` 三限预检 → **`_streaming_unzip`** 流式落盘解压（`zf.open` +
  1 MiB 块读写目标盘，不整文件进内存）→ 逐文件分派（`sitk.ReadImage` /
  `pydicom.dcmread` 直读盘上文件）。
- 临时目录：`tempfile.TemporaryDirectory(dir=~/data/BodyDonor/tmp)`（**原生盘**，
  不落 /tmp 内存盘；env `BODYDONOR_ZIP_TMP_DIR` 可覆盖，测试隔离用），处理完自动清理。
- 解压保护三限在流式路径**继续生效**：文件数边解压边计数（超限即停即清
  `ExtractLimitError`）；嵌套 zip 成员不展开（跳过记 skipped）；压缩比预检
  （safe_open_zip 逐成员元数据）。另加路径穿越防护（成员名 `..`/绝对路径 →
  跳过记 skipped，不落盘）。
- 测试（`tests/test_zip_streaming.py`，7 条）：
  - **原有 zip 炸弹三 case 负测试全部继续通过**（`test_zip_bomb.py` 7 条未改动）
  - 大包流式解压：**内存不随包大小线性涨**（mock sitk.ReadImage 只测解压+分派段，
    2/16/32 MiB 包 peak 恒定 <4 MiB；内存分派版 peak 会 = 4/32/64 MiB）
  - 临时目录事后干净（rglob 无残留）
  - 路径穿越成员不落盘
  - 默认临时目录在原生盘（`~/data/BodyDonor/tmp`，非 `/tmp` 前缀）

---

## 2. 实测结果

| 验证 | 方式 | 结果 |
|---|---|---|
| SEG roundtrip | 单测：NIfTI mask → SEG → 重读 → 逐体素 diff | **diff=0，8 帧、1 segment、源引用对** |
| SEG SOP Class | pydicom 重读 | `1.2.840.10008.5.1.4.1.1.66.4` + Modality SEG |
| SEG 拒绝静默重采样 | 单测：面内尺寸 16×16 vs 源 32×32 | `SegExportError: 面内尺寸不一致` |
| SEG 篡改检测 | 单测：换 mask 后 verify | `voxel_diff_nonzero > 0`，rep.ok False |
| OCR 检出 | 单测：合成 "DOSE REPORT / Patient ID: PID-12345" 512×512 | detected=True，texts 命中 `DOSE REPORT`、`Patient ID: PID-12345`、`CTDIvol 12.5 mGy`、`DLP: 650 mGy*cm` |
| OCR 目标识别 | 单测 | SC UID → True；SeriesDescription=Dose Report（CT SOP）→ True；轴位 CT → False |
| ingest 端到端 OCR 拒收 | 单测 + CLI 实录复验：2 层剂量报告序列（Tag UNKNOWN） | n_series=0，skipped=2，reason="OCR 检出疑似烧录文字（['DOSE REPORT', ...]），拒收" |
| 选择器回归 | 单测：localizer 先扫 + 2×40 层 | 2 层序列 excluded=True（层数/LOCALIZER 理由），40 层选中 |
| CLI 报告复验 | `bodydonor import <dir> --dry-run` 实录 | 2 层行显示 `排除：LOC…`，40 层行显示 `选中`——**无错位** |
| commit 防线 | 单测：绕过选择器构造 outcome 直接 commit 10 层序列 | `SliceGuardError`；显式 `user_overrides` → 放行落库 |
| RAS+ 重排 | 单测：commit 落盘 NIfTI nib 读回 | axcodes == **(R, A, S)**，报告"重排"行写明 |
| affine 守卫 | 单测：平移/方向不一致 + 容差边界 | 一致通过；不一致 `AffineMismatchError` |
| zip 流式内存 | 单测：mock sitk 只测解压+分派段，2/16/32 MiB 包 | peak 恒定 **<4 MiB**（内存分派版 = 4/32/64 MiB 随包线性涨） |
| 临时目录清理 | 单测 + 实测 `~/data/BodyDonor/tmp` | rglob 无残留；测试跑完实测 **0 条目** |
| zip 炸弹三 case | `test_zip_bomb.py` 原测试未改动 | **7 条全部继续通过** |
| 全套测试 | `.venv/bin/python -m pytest tests/` + `uv run --extra dev --extra ingest pytest tests/`（CI 同构） | **135 passed**（96 基线 + 39 新增），1 warning（fastapi testclient 弃用提示，与本补丁无关） |
| ruff | `ruff check .` | **All checks passed!** |

## 3. 遗留问题

1. **SEG 源实例真实引用**：`SourceSeriesInfo.source_instances` 支持逐层 SOP 引用，
   但 M1 无 DICOM 落盘持久层（ingest 后 DICOM 头即弃），当前导出走合成源实例。
   M2 接入 PACS 联调时从存储回填真实实例（几何三要素已就位）。
2. **OCR 引擎精度边界**：rapidocr 对低分辨率/斜排烧录文字可能漏检（单测用
   512×512 水平清晰文字）；M2 Web UI 的上传页声明仍保留为 UNKNOWN 场景防线。
3. **assert_affine_match 接入点**：守卫函数与测试就位，但当前仓库无 nibabel mask
   读写路径（ingest 的 pixel_data 走 sitk）；M2 mask 落盘/读回时强制接入。
4. **zip 嵌套 ≤3 层成员**：流式路径对嵌套 zip 成员保持"跳过记 skipped"策略
   （不递归解包），与 M1 行为一致；如需支持 ≤3 层嵌套包，M2 再议。
5. **M1-REPORT §8 遗留不变**：TotalSegmentator/AMOS 全量下载、100 MB 三处强制的
   另两处、多指纹部署语义、GitHub 远程未建、BSL LICENSE 占位等，均不在本补丁范围。

## 4. 交付物清单

```
src/bodydonor/export_seg.py      新增：DICOM SEG 导出 + 三道校验 + 逐体素 diff
src/bodydonor/burned_ocr.py      新增：像素级 OCR 烧录扫描（只扫 Dose Report/SC 序列）
src/bodydonor/ingest.py          zip 流式落盘解压 + OCR 第二道关接入 + reorientation 字段
src/bodydonor/nifti_geom.py      reorient_ras（RAS+ 重排）+ assert_affine_match（硬报错守卫）
src/bodydonor/pipeline.py        guard_min_slices（commit 防线）+ 落盘前 RAS+ 重排
src/bodydonor/cli.py             报告按 series_id 取排除状态（修 zip 错位）+ 重排/OCR 展示
tests/test_export_seg.py         新增 7 条：SEG roundtrip/SOP Class/几何不一致/篡改检测
tests/test_burned_ocr.py         新增 10 条：OCR 目标识别/检出/轴位不扫/ingest 端到端拒收
tests/test_selector_guard.py     新增 7 条：2 层排除回归 + guard_min_slices 防线
tests/test_nifti_geom_strict.py  新增 8 条：RAS+ 重排 + assert_affine_match + 落盘方向
tests/test_zip_streaming.py      新增 7 条：流式解压内存恒定/临时目录清理/路径穿越
pyproject.toml + uv.lock         highdicom（ingest）+ rapidocr-onnxruntime（dev）
proposal.md                      M1-fix 决策注记（评审时已更新）
```

本地 main 共 7 个 commit（`516a03e` M0 → … → `8211dba` pytest pythonpath → `4058f71` M1-fix），未推。
