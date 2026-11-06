# M1-REPORT — BodyDonor M1 交付报告

> 日期：2026-11-06。commit：`5fec096`（M1，本地 main，未推）。
> 依据：proposal.md（v2）§10 M1 / §11 启动指令 v2、GRILL-REVIEW.md R3/R5/R6、M0-FIX-REPORT.md §5 遗留项。
> 只做 M1，不做 M2。非交互运行，每步本地测试通过后继续。

---

## 1. 做了什么

### 1.1 Import Bundle：ingest(path_or_stream) -> [Series]（任务 1）

**`src/bodydonor/ingest.py`**：单一入口，三种输入自动分派：

| 输入形态 | 支持 | 实现 |
|---|---|---|
| 单个 NIfTI（.nii/.nii.gz） | ✅ | sitk 直读，单元素列表（无 Series 概念），几何校验警告 |
| DICOM 文件夹 | ✅ | 递归/非递归扫描，按 `SeriesInstanceUID` 拆分多 Series |
| DICOM zip / 多序列批量 zip | ✅ | `safe_open_zip` 三限预检 → 内存分派 → 同上拆分；NIfTI zip 亦支持 |

不做（v1.1 Ingest 插件）：文件夹监听、Orthanc DICOMweb、PACS C-FIND。

`ImportReport` 携带：Series 数、被跳过原因（逐文件）、每个 Series 的模态/层数/层厚/方向/HU 范围/烧录声明/De-ID 统计。`ingest()` 接受 `Path | BinaryIO | bytes`（API 上传通道传流）。

### 1.2 Series 选择器（任务 2，GRILL-REVIEW R3）

**`src/bodydonor/series_selector.py`**：规则过滤 ≠ 硬删，全部可勾回——

- 排除 `ImageType[0] == 'LOCALIZER'`（定位像）
- **DERIVED 不一刀切排除**：排序置后 + `flags` 标注（MPR 重建恰恰是 DERIVED）
- 层数 < 30 排除
- 非轴位排除但保留（`exclude_reason` 标注"可勾回"），`select(user_overrides=[...])` 勾回

交付形态：CLI（`bodydonor import` 报告的"选择器"列 + `--include` 参数）与 API（`GET /ingest/{id}/selector` + `POST /ingest/{id}/commit?include=`）。Web UI 勾选在 M2。

### 1.3 导入报告（自检门，任务 3）

导入后先出报告再落库（`pipeline.scan` 与 `pipeline.commit` 分离）。CLI 用 rich.table 打印
（Series 列表 + 元数据表），API 返回同构 JSON（`outcome.to_dict()`）。样例见 §3。

### 1.4 De-ID（任务 4）

**`src/bodydonor/deid.py`**，落在 Ingest 第一步（每个文件读取后立即处理）：

- **像素烧录检测**：`BurnedInAnnotation (0028,0301)` == `YES` → **拒收**（不是隔离留存）；
  缺失按 `UNKNOWN` 处理并在报告标警告（上传页声明为唯一防线）
- **PS3.15 基本档头去标识**：PatientName/PatientID/AccessionNumber/InstitutionName 等
  20 个关键字段原地剥离；剥离前统计进报告（`deid_removed_count`）
- 拒收时不改写原数据

### 1.5 NIfTI 几何校验（任务 5）

**`src/bodydonor/nifti_geom.py`**（nibabel 视角）+ ingest 内 sitk 视角双路：

- affine 各向异性：spacing max/min > 3× 警告
- 非正交方向：affine 列向量两两 |cos| > 0.02 警告
- 非毫米 spacing：xyzt_units 非 mm / 缺失警告

全部是**警告**（不拒收），导入报告可见。实测 AMOS 真实数据触发各向异性警告（0.57mm×5mm = 8.8×）。

### 1.6 100 MB 三处强制 + 解压保护三限（任务 6）

- **后端流式计数**（三处之一）：`extract_guard.read_upload_stream()` 分块读流累加，
  超 `max_upload_mb` 即中断（`UploadLimitError` → API 413）。仅 cloud_demo（on_prem 为 None 不限）。
  另两处：前端预检与反代 `client_max_body_size` 属 M5 云端 demo 交付，本里程碑仅后端。
- **解压保护三限**：`safe_open_zip()` 预检——压缩比 >100×（逐成员，防平均稀释）、
  嵌套 zip >3 层（逐层真实展开计数，到限即停）、文件数 >10k。
  配置集中在 `extract_limits`（config diff，非代码分叉）。
- **zip 炸弹三 case 负测试各一条**（`tests/test_zip_bomb.py`）：高压缩比（1 MiB 零字节
  → >1000×）、嵌套 zip（真实构造 4 层 >3）、超多文件（10001 >10k）；各带边界对照
  （10k 整放行、3 层整放行）。

### 1.7 试用限制计数（任务 7，M0-fix 遗留项 1）

**`src/bodydonor/usage_counter.py`**，导入路径维护：

- **UsageLedger 为真相源**：逐 Case 双行流水（年 `2026` + 月 `2026-11`），
  `(fingerprint_hash, period, case_id)` 唯一约束 → **幂等**（重复导入同一 Series 不重复计）
- **LicenseState 缓存列**（trial_yearly_used/monthly_used）由写路径从流水重算同步（M0-fix 遗留项 1 闭环）
- **无 License 试用限制**：每年 200 例 / 每月 20 例，先到者为准；预检（`check_trial_limits`）
  在写库前，超限拦截：CLI exit 1（"落库失败：试用限制超限…"）、API 403（明确提示）
- 计数以 Case 为单位（一个 Case 一次导入计 1 例；同 Case 多 Series 不重复计）
- 写路径走 `db.write_lock` 单一写入纪律（M0-fix）

### 1.8 QC 数据集下载与许可登记（任务 8）

见 §5。AMOS22 40-case 子集已下载并验证，署名义务已履行；TotalSegmentator dataset
全量未下载（记录原因），许可登记进 `LICENSES-THIRD-PARTY.md` §5.1。

### 1.9 配置与数据目录（任务 9）

- `max_upload_mb` 与 `deployment_mode: cloud_demo|on_prem` 保持生效（cloud_demo 413 实测）
- 数据目录 `~/data/BodyDonor/`（WSL2 原生 FS，`/mnt/c` 拒绝逻辑沿用 M0）

### 1.10 顺带修复

- **pipeline 兜底建表 + M0 旧库列迁移**：CLI 直连路径无 FastAPI lifespan，
  `commit()` 前幂等 `init_db`；M0 旧库 cases/series 缺 M0-fix 实体列时逐列
  `ALTER TABLE ADD COLUMN`（幂等，正式迁移仍走 Alembic）
- pyproject `python-multipart` 加入 ingest extras（FastAPI 文件上传必需）

---

## 2. 三种输入实测结果（CLI 实录）

| 输入 | 命令 | 结果 |
|---|---|---|
| 单个 NIfTI（AMOS `amos_0001.nii.gz`，768×768×90，43 MB） | `bodydonor import <path> --dry-run` | Series 1，CT，90 层，5.0mm，axial，HU -1024..1373，触发各向异性警告 8.8× |
| DICOM 文件夹（2 series×40 层 + LOCALIZER×2） | `bodydonor import <dir> --dry-run` | Series 3：LOCALIZER 被排除，2×40 层选中，层厚 1.00，axial |
| 多序列 zip（同上打包） | `bodydonor import <zip> --dry-run` | 同文件夹结果（input_kind=dicom_zip），Series 3 |
| 落库（三种各一次） | `bodydonor import <path>` | Case 各 1；计数 年 1→2/200 · 月 1→2/20 |
| 幂等复验 | 重复 import 同一文件夹 | 计数不变（年 2 · 月 2），Case/Series 不重复建 |

## 3. 导入报告样例（rich.table 实录）

```
              导入报告（自检门）— /tmp/acceptance/dicom_study
┏━━━━━━━━━━━━━┳━━━━━━┳━━━━━━┳━━━━━━━━━┳━━━━━━━┳━━━━━━━━━━━━━┳━━━━━━┳━━━━━━━━┓
┃ Series      ┃ 模态 ┃ 层数 ┃ 层厚 mm ┃ 方向  ┃     HU 范围 ┃ 烧录 ┃ 选择器 ┃
┡━━━━━━━━━━━━━╇━━━━━━╇━━━━━━╇━━━━━━━━━╇━━━━━━━╇━━━━━━━━━━━━━╇━━━━━━╇━━━━━━━━┩
│ 1.2.826.0.1 │ CT   │    2 │    1.00 │ axial │ -1023..975  │ no   │ 选中   │
│ 1.2.826.0.1 │ CT   │   40 │    1.00 │ axial │ -1023..975  │ no   │ 选中   │
│ 1.2.826.0.1 │ CT   │   40 │    1.00 │ axial │ -1023..975  │ no   │ 排除：LOC… │
└─────────────┴──────┴──────┴─────────┴───────┴─────────────┴──────┴────────┘
┌────────────┬──────────────┐
│ 输入形态   │ dicom_folder │
│ 扫描文件数 │ 82           │
│ Series 数  │ 3            │
│ 被跳过数   │ 0            │
└────────────┴──────────────┘
```

NIfTI 报告（含几何警告）：

```
┃ Series         ┃ 模态 ┃ 层数 ┃ 层厚 mm ┃ 方向  ┃ HU 范围     ┃ 烧录 ┃ 选择器 ┃
│ nifti-3fd14fa6 │ CT   │   90 │    5.00 │ axial │ -1024..1373 │ ?    │ 选中   │
│   几何：nifti-3fd14fa6 │ affine 各向异性：spacing (0.5703125, 0.5703125, 5.0) max/min = 8.8x (> 3x)
```

## 4. 试用计数验证

| 场景 | 验证方式 | 结果 |
|---|---|---|
| 计数写路径 | 单测（`test_record_usage_counts_once_per_case`） | case-1 计 1；重复导入 recorded=False 不重复计；case-2 后年计数 2 |
| 双行流水 | 单测（`test_yearly_and_monthly_periods`） | periods == {'2026', '2026-11'} |
| 月度超限 | 单测：预填 20 例后 `check_trial_limits` | raise TrialLimitError，消息含"月" |
| 年度超限 | 单测：预填 200 条年度记录 | raise TrialLimitError，消息含年 period |
| License 豁免 | 单测：license_id 置非空 | enforced=False |
| API 403 | 单测：20 例满后 commit | 403 + "试用限制超限" |
| CLI 实测 | 重复导入同一 DICOM folder | 计数 年 2/200 · 月 2/20 不变（`bodydonor license usage` 可见） |

## 5. 数据集下载情况（任务 8）

| 数据集 | 官方源 | 实际下载 | 原因/用途 |
|---|---|---|---|
| **AMOS22** | Zenodo 7262581（amos22.zip 24.2 GB，cc-by-4.0 已核实） | **40-case 子集**（HF 镜像 `MedOtter/amos22-ct-dataset`，CC-BY-4.0 已核实，per-case NIfTI；1.3 GB，imagesTr+labelsTr 各 40/40 全部验证可读） | **全量对笔记本负担过大**（24.2 GB，Zenodo 单流 218 KB/s 需 >30h；HF 1 MB/s 需 6h+）。M1 仅需满足 hold-out 下限（≥30 cases，AMOS 每 case 含全部 15 类精标 → 每类出现 40 次 ≥10）。全量延后至 M3/M4 蒸馏训练前 |
| **TotalSegmentator dataset** | Zenodo 6802614 v1（28.4 GB）/ 10047292 v2（cc-by-4.0 已核实） | **未全量下载**（记录原因：M1 无 QC 冷启动需求；候选镜像 `YongchengYAO/TotalSegmentator-CT-Lite` 1228 CT CC-BY-4.0 已核实备选） | QC 冷启动是 M3 前置项，届时执行 |

- 存放：`~/data/BodyDonor/downloads/amos22_subset/`（WSL2 原生 FS）
- **署名义务已履行**：`LICENSES-THIRD-PARTY.md` §5.1 记录两数据集的完整引用
  （作者/标题/arXiv/DOI/许可/镜像链）+ 用途声明（hold-out 不进训练）
- 许可链核实：Zenodo API 原文 `cc-by-4.0`（M0 已核）+ HF API tags 逐条复核（M1）

## 6. 显存与耗时基线（本机：WSL2，M1 无 GPU 推理）

| 阶段 | 输入 | 耗时 |
|---|---|---|
| NIfTI ingest | AMOS 真实 768×768×90（43 MB） | 0.67 s |
| NIfTI ingest | 512×512×300 上界级（126 MB） | 0.70 s |
| DICOM 文件夹 ingest | 82 slices（64×64 合成） | 0.04 s |
| DICOM zip ingest | 578 KB zip（82 slices） | 0.04 s |
| 全套测试 | 96 条 | 8.6 s |

- 显存：M1 无 GPU 任务（教师推理在 M2），不适用
- 耗时结论：Ingest 全阶段 <1 s/病例级，非瓶颈；QC 数据集下载是外部瓶颈（见 §5）

## 7. 测试与验收对照

M1 验收标准逐条：

| 标准 | 状态 |
|---|---|
| 三种输入各跑通一次，产出导入报告 | ✅ §2 实录（NIfTI/DICOM 文件夹/多序列 zip） |
| zip 炸弹三 case 负测试通过 | ✅ 3 条正测 + 2 条边界对照（`test_zip_bomb.py`） |
| 试用计数与超限拦截有测试覆盖 | ✅ 6 条（§4） |
| 全部测试通过 | ✅ **96 passed**（46 旧 + 50 新） |
| ruff 全绿 | ✅ `All checks passed!` |

## 8. 遗留问题

1. **TotalSegmentator dataset 全量下载**：QC 冷启动（M3 前置）时执行；届时优先评估
   `YongchengYAO/TotalSegmentator-CT-Lite` 镜像（1228 CT，CC-BY-4.0）vs Zenodo 原始通道。
2. **AMOS22 全量（500 例）**：M3/M4 蒸馏训练需要更多训练数据时再扩；hold-out 只需当前子集。
3. **100 MB 三处强制的另两处**（前端预检、反代 client_max_body_size）：M5 云端 demo 交付
   （proposal §4.3/§5，本里程碑仅后端流式计数）。
4. **多指纹部署语义**：LicenseState 按 fingerprint_hash 一行（M0-fix 遗留项 2），M4/M5 激活流程再定。
5. **指纹占位**：`fingerprint_hash()` 用机器级稳定 ID 占位，真实容错指纹 M4/M5 Ed25519 流程落地。
6. **选择器阈值位置**：MIN_SLICES=30 等暂在 `series_selector.py`，M2 随 Web UI 前移入 config
   （已记 settings.yaml 注释）。
7. M0 遗留不变：GitHub 远程未建、BSL LICENSE 占位、SegVol 权重待核实、jobs 表不在迁移内。

## 9. 交付物清单

```
src/bodydonor/ingest.py          新增：Import Bundle 单一入口（三输入分派 + ImportReport）
src/bodydonor/deid.py            新增：PS3.15 头去标识 + 像素烧录检测（YES 拒收）
src/bodydonor/extract_guard.py   新增：zip 三限预检 + 100MB 流式计数
src/bodydonor/series_selector.py 新增：规则过滤（LOCALIZER/DERIVED/层数/非轴位）+ 勾回
src/bodydonor/usage_counter.py   新增：试用计数（UsageLedger 真相源 + 幂等 + 超限拦截）
src/bodydonor/nifti_geom.py      新增：几何校验（各向异性/非正交/非毫米）
src/bodydonor/pipeline.py        新增：scan（自检门）-> commit（落库+计数）编排 + M0 旧库列迁移
src/bodydonor/cli.py             import 接 ingest（rich.table 报告 + --include/--json）+ license usage
src/bodydonor/app.py             /ingest/scan|commit|selector 端点（413/422/403）+ license/usage 更新
tests/factories.py               新增：合成 DICOM/NIfTI/zip 工厂
tests/test_zip_bomb.py           新增：炸弹三 case 负测试 + 边界对照（7 条）
tests/test_ingest.py             新增：三输入/De-ID/选择器/几何校验（17 条）
tests/test_deid_usage.py         新增：De-ID 模块/试用计数/pipeline 落库（13 条）
tests/test_api_ingest.py         新增：API 端点 + 100MB 413 + 超限 403（13 条）
.github/workflows/ci.yml         M1 测试覆盖注释更新
config/settings.yaml             解压保护/试用限制/数据集注记
pyproject.toml + uv.lock         python-multipart（ingest extras）
LICENSES-THIRD-PARTY.md          §5.1：M1 下载登记 + 署名义务（CC-BY-4.0 Attribution）
```

本地 main 共 4 个 commit（`516a03e` M0 → `25fb6df` M0-fix → `b5aa043` M0-fix 报告 → `5fec096` M1），未推。
