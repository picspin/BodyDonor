# M2-REPORT — BodyDonor M2 交付报告

> 日期：2026-11-07。commits：`3e90a19` 本体映射 → `6de1727` M1-fix 遗留 (a)(c) →
> `57faa68` TUI → `92c6ae2` niivue 审阅页 → `eaee36b` TotalSegmentator worker（本地 main，未推）。
> 依据：proposal.md §3/§4.4/§9.1/§10/§11 M2 行、M1-FIX-REPORT §3 遗留 1–3、工作指令 M2。
> 非交互运行，每步本地测试通过后继续；全部测试通过 + ruff 全绿后提交。

---

## 1. 各任务交付清单

### 1.1 TotalSegmentator worker（Q7 隔离）

- **独立 venv `.venv-totalseg`**（uv 管理）：`totalsegmentator==2.10.0` +
  `torch==2.5.1+cu124` + `torchvision==0.20.1+cu124`（**关键 blocker 处理**：先装的
  torch 2.15 默认 cu132 与驱动 CUDA 12.5 不兼容——"NVIDIA driver too old (found
  version 12050)"，重装 cu124 build 解决；torchvision 也须配 0.20.1，否则
  "operator torchvision::nms does not exist"；Quadro RTX 5000 Turing 不支持
  bf16 已知，TS 推理用 fp16 AMP）。
  主 `.venv` **不含** totalsegmentator/torch（隔离验证进单测
  `test_main_venv_not_shared`）。
- **分发层**（`src/bodydonor/segmentation.py`）：subprocess 调用独立 venv 的
  `TotalSegmentator` CLI，import 链隔离。
  - `check_worker_task`：白名单一票否决（`license_registry.check_task`）——
    `brain_aneurysm` BLOCKED（CC BY-NC 4.0）、不在白名单任务拒绝；任何代码路径
    不许绕过（单测锁定，CI 白名单负测试继续通过）。
  - `<30 层` 防线复查：`segment_series` 在 Series.num_slices 上复查
    guard_min_slices 语义（定位像残片 3D 卷积报错）。
  - `merge_ts_output`：TS 每类一个 NIfTI 合并为多类标签图，**每类过
    `assert_affine_match`**（不一致 raise，不静默重采样）。
  - `segment_series`：原图（Series.storage_key，RAS+ NIfTI）→ TS → 多类 mask
    落盘 `{case_key}/{series_id}/mask.nii.gz`（与现有存储约定一致）→ MaskVersion
    记录（version 递增，`source=teacher`，`created_by=totalsegmentator-{task}`）。
    落盘/读回过 affine 守卫。
  - 显存峰值：TS 子进程运行期间 `nvidia-smi --query-compute-apps` 轮询实测
    （MiB）；不可用时返回 None（不猜数字）。
- **入口**：CLI `bodydonor segment <series_id> --task total -d gpu`（rich.table
  报告：case/mask/类别数/耗时/显存峰值）+ API `POST /segments`（422 白名单/防线，
  404 unknown series）。
- **教师白名单现状**（config/settings.yaml + LICENSES-THIRD-PARTY.md §6）：商用
  蒸馏只允许 `total`（commercial_distill_tasks）；`brain_aneurysm` 禁用；
  nnInteractive 修正产物不进商用蒸馏训练集（`ontology.mask_allowed_in_distill_training`
  强制点 + 白名单负测试）。

### 1.2 本体映射（静态查表 + 三态回退）

- **版本化静态查表 `ontology/v1/map.yaml`**：38 条目，字段
  `nl_label / synonyms / snomed_ct / uberon / totalseg_class / status`；
  **中英双语同义词进同一张表**（synonyms 字段，Q1）；git 管、评审可 diff。
- **映射服务**（`src/bodydonor/ontology.py`）：对每个 NL 标签返回三态——
  - `resolved`：唯一命中 totalseg_class（含 SNOMED/Uberon）
  - `ambiguous`：多候选（附同义词与 SNOMED 概念 ID），交回用户点选；
    **用户选择写回 `ontology/v1/overrides.yaml`**（与主表分离，git diff 只见
    用户决策），下次 resolve 直接 resolved
  - `unresolved`：零命中
- **逃生通道**（SegVol/SAT 类真文本提示模型）：**默认关闭**（config
  `experimental_text_prompt.enabled=false`），须显式开启（`--experimental-text-prompt`
  或 config）；结果打 `experimental` 标记；**`mask_allowed_in_distill_training`
  强制点拒收 experimental mask 与非 total 教师 mask**（单测锁定）。M2 只落地
  开关/标记/隔离逻辑，不真正接 SegVol（其权重许可仍待核实——占位结果不打 mask）。
- **入口**：CLI `bodydonor ontology resolve/disambiguate/escape` + API
  `/ontology/resolve|disambiguate|escape-hatch|summary`。

### 1.3 niivue 审阅（§9.1）

- **审阅页 `src/bodydonor/static/review.html`**（单文件 HTML，FastAPI 静态托管）：
  - 三正交面（axial/coronal/sagittal）+ 第 4 视口体绘制（RENDER）
  - mask 逐类开关与换色（seg-meta 端点列 label 体素数；swatch + color input）
  - 窗宽窗位滑条；逐 slice 滚动滑条 + 滚轮同步回读（mask 随 volumes 重绘）；
    十字定位/重置
  - 人工修正（M2 粒度）：**整类删除**（类别开关勾掉 -> 保存时体素清零）+
    **画笔粗修**（niivue drawBitmap -> save 时非零体素从 mask 删除）
  - 保存 -> `POST /review/{series_id}/save` -> 新 MaskVersion（`source=manual`，
    version 递增）；写回前 **affine 守卫强制**（mask/drawing 与原图不一致 422，
    不静默重采样）
- **不做（非目标，§9.1）**：4D/时间序列、DTI、三维任意切面自由角、多人实时
  协同标注、DICOM 原生渲染。
- **niivue 加载方式**：**本地 vendored**（`static/niivue.umd.js`，0.53.1 稳定版
  UMD bundle）。理由：① 院内可离线部署（"像素永不离院"形态不该依赖 CDN）；
  ② 版本锁定（0.53.1 的 MIP/体绘制 API 与 1.x 不兼容，升级是显式决策）；
  ③ npm scoped 包 `@niivue/niivue@0.53.1` dist 产物直接可用。
- **MIP 说明（最小合理选择）**：niivue 0.53.1 无原生 MIP slice type（SLICE_TYPE
  只有 AXIAL/CORONAL/SAGITTAL/MULTIPLANAR/RENDER）。M2 第 4 视口用体绘制
  （RENDER）+ 正交方位相机（`setRenderAzimuthElevation(0,0)`）近似 MIP 视觉；
  M3+ 换 vendored 新版或自实现 MIP shader（记入遗留）。

### 1.4 TUI（`bodydonor tui`，Textual）

- **`src/bodydonor/tui.py`**：Textual 应用，**只做一件事**——HTTP 轮询本地
  FastAPI（`/monitor/gpu` + `/monitor/pipeline`，2s 周期），展示：
  - GPU 显存条（终端块字符 ▓░，放射科剂量条的终端对应物）+ 型号 + 利用率
  - 进程内队列（qsize + active_jobs）
  - 流水线 ①→⑧ 状态机（proposal §6 8 阶段；状态色 idle/running/done/blocked）
  - `i` 触发本地磁盘导入（**复用 pipeline.scan+commit 同一入口**，走磁盘路径
    不走 HTTP 上传，proposal §4.4 TUI 通道）；CLI 批处理与交互式 TUI 互不耦合。
- **后端 `/monitor` 端点**：`/monitor/gpu`（nvidia-smi 快照，不可用时
  `available=false` + reason，不猜数字）；`/monitor/pipeline`（8 阶段注册表 +
  InProcessQueue 状态，`pending_ids()` 只读）；`POST /monitor/pipeline/{stage}`
  worker 更新阶段状态（TUI 只读不写）。
- **依赖**：textual 8.2.8 + pytest-asyncio 进主 venv（pyproject + uv.lock pin）。
- **冒烟**：TestClient 端点测试 + Textual `run_test()` 非交互渲染（mock 后端
  渲染出显存条与 8 阶段）+ **实测对运行中后端轮询**（真卡 735/16384 MiB 渲染）。
  交互式长驻不进 CI（工作指令 §3.4）。

### 1.5 M1-fix 遗留（必须落地项）

- **(a) SEG 真实源实例引用（存储回填）**：
  - `ingest.extract_source_instances`：从 DICOM 头提取逐层 SOP Instance UID +
    几何三要素（IOP/IPP/PixelSpacing/SliceThickness），按法向**降序**排布
    （DICOM 帧序惯例，与 export_seg 合成路径方向一致）。只取头字段，不含像素。
  - `Series.source_instances_json` 新列（JSON）：commit 时 `_persist_source_instances`
    持久化（幂等），`pipeline._migrate_legacy_schema` 补旧库列。
  - `load_source_instances` 读回 + `source_info_from_persisted` 转
    `export_seg.SourceSeriesInfo` —— **export_seg 用真实源实例引用**
    （ReferencedSOPInstanceUID 全部为真实源 UID，单测断言）；合成源实例降级为
    无 DICOM 来源（NIfTI 输入）时的 fallback（docstring + 单测写明）。
  - **单测：真实引用路径 roundtrip 逐体素 diff=0**（SEG -> 重读 -> 与源 mask
    逐体素对比）。
- **(b) `assert_affine_match` 强制接入**：M2 所有新增 nibabel mask 读写路径——
  TS worker 落盘/读回（`merge_ts_output` 每类 + `segment_series` 合并后）、
  niivue 修正写回（`review_save` mask/drawing）——全部过守卫；不一致 raise
  （API 422）；单测覆盖（平移不一致/方向不一致/篡改后 422）。
- **(c) OCR 精度验证**：见 §2 实测验证表。

---

## 2. 实测验证表（每项写清在真机上怎么验的）

| 验证 | 方式 | 结果 |
|---|---|---|
| **OCR 低分辨率** | 单测：512/256/128/96/64/48px 合成烧录图（PIL 黑底白字 "DOSE REPORT / Patient ID"），rapidocr CPU 实扫 | **≥40px 检出**（n_texts 2→1 递减）；**≤36px 漏检**（字号过小 OCR 不可读）——精度边界如实断言固化（`test_miss_boundary_below_40px_documented`），不调阈值 |
| **OCR 斜排/旋转** | 单测：15/30/45/60/90° 旋转 512px + 256px+30° 组合，rapidocr 实扫 | **全检出**（15–90° 无漏检；组合场景检出） |
| **上传页防线** | UNKNOWN 场景 Tag 检查 + 声明勾选不变（deid.py / M1 行为） | OCR 边界 ≤36px 由上传声明兜底（Tag 检查仍是第一道关） |
| **SEG 真实引用 roundtrip** | 单测：5 层合成 DICOM -> extract_source_instances -> commit 落库 -> 读回 -> SourceSeriesInfo -> SEG -> verify_seg 逐体素 diff | **diff=0**，5 帧，ReferencedSOPInstanceUID 全部为真实源 SOP UID（非合成） |
| **SEG 合成 fallback** | 单测：空 source_instances（NIfTI 输入）-> export_seg 合成路径 | 不回归：diff=0 |
| **affine 守卫（worker）** | 单测：类 mask 与原图平移不一致 -> merge_ts_output | `AffineMismatchError`（不静默重采样） |
| **affine 守卫（审阅）** | 单测 + API：篡改 mask affine 后 POST save | API **422**，错误带 affine 详情（img/mask 矩阵） |
| **审阅页保存 roundtrip** | **CDP Edge headless（Windows 侧 msedge.exe，CDP websocket）实测**：加载 /review?series_id=... -> 浏览器触发 POST save -> 新 MaskVersion 读回逐体素 | **v1 manual，label-1 全删（0 体素）label-2 保留** |
| **审阅页渲染** | **CDP Edge headless 实测**：4 canvas、WebGL2 上下文、niivue 加载、2 类别开关、"已加载"状态 | 全部 PASS |
| **TUI 真实轮询** | **实测**：启动 uvicorn 后端（真卡在跑），TUI Textual run_test 对运行中后端一次真实 HTTP 轮询 | 显存条 ▓ 渲染 735/16384 MiB、8 阶段状态机、连接状态 |
| **GPU CUDA 就绪** | 实测：torch 2.5.1+cu124 `torch.zeros(3, device='cuda:0')` | **CUDA tensor OK（Quadro RTX 5000）** |
| **TS 真卡推理** | **实测**：`TotalSegmentator -i amos_0004 -o out -t total -d gpu`（独立 venv，512×512×78 真实病例） | **跑通：117 类 mask 落盘，墙钟 118.8s，exit 0**（§3 基线） |
| **主 venv 隔离** | 单测：主 venv site-packages 无 totalsegmentator | PASS（Q7 隔离验证） |
| **显存峰值实测** | TS 推理期间 `nvidia-smi --query-gpu=memory.used` 1s 轮询（113 样本，峰值−基线） | **峰值 5472 MiB / 推理增量 4641 MiB**（§3） |
| **浏览器帧耗时红线** | **实测**：Edge headless + CDP（Edg/156.0.4314.58，Windows 侧 exe）加载 WebGL2 基准页——3D 纹理 512×512×300（CT 代理）+ 每"帧" `texSubImage3D` 单切片 + `drawArrays`（即 niivue 滚动重绘的真实 GPU 路径代理负载），100 帧 | **mean 0.65ms / p95 1.00ms**——远低于 <100ms/帧红线（**PASS**） |

**浏览器实测方法说明**：本机 WSL2 无 Linux 浏览器；可用浏览器手段是 Windows 侧
Edge（msedge.exe）经 `--remote-debugging-port` 从 WSL2 以 CDP websocket 驱动。
Edge headless 下 `requestAnimationFrame` 不触发（无合成器），帧耗时测量用
`setTimeout` 链 + `texSubImage3D`/`drawArrays` 真实 GPU 调用（帧耗时含 GPU 端，
是滚动重绘的代理负载而非 niivue 完整管线）。**帧耗时实测数字 0.65/1.00ms 是
代理负载的实测值**；niivue 完整管线（多视口 + overlay + 拾取）的单帧耗时尚未
实测，缺口是 overlay 多类渲染与拾取开销（预计远低于红线，M3 联调时补测）。

---

## 3. 首个 `total` 任务 GPU 推理基线（AMOS22 真实病例）

> 只报实测数。测量方式：墙钟计时（TS 子进程起止）+ `nvidia-smi
> --query-gpu=memory.used` 1s 周期轮询（113 个样本取峰值；推理前基线 831 MiB
> 已扣除）。病例为 AMOS22 40 例公开子集真实 CT（CC-BY-4.0）。
> 脚本：`scripts/bench_gpu_baseline.py`（测量方法可复现）。

| 项 | 值 |
|---|---|
| 病例 | amos_0004（AMOS22 子集，train/imagesTr） |
| 体素尺寸 | 512 × 512 × 78（x,y,z） |
| 体素间距 | 0.782 × 0.782 × 5.0 mm |
| 层数 | 78 |
| 任务 | `total`（`-t total -d gpu`，TotalSegmentator 2.10.0） |
| torch / torchvision | 2.5.1+cu124 / 0.20.1+cu124（Quadro RTX 5000 16GB，Turing） |
| **显存峰值** | **5472 MiB**（全局采样峰值；减推理前基线 831 MiB = 推理增量 **4641 MiB**，113 个样本） |
| **墙钟耗时** | **118.8 s**（含 5 个 dataset part 逐一推理 + 合并；三次实测 117.3 / 120.7 / 118.8 s 一致） |
| **mask 类别数** | **117 类**（TS 2.10 total 任务全部输出类：aorta、liver、femur_left/right 等） |

**端到端验证**：`bodydonor segment nifti-b63b1d93 --task total -d gpu`（ingest 落库
的 AMOS case → worker 分发层 → 独立 venv TS → 多类 mask 落盘 → MaskVersion
记录，affine 守卫强制）。实测：exit 0，mask.nii.gz 487.4K 落盘
（512×512×78 uint16，69 类非空 / 1996904 非零体素），MaskVersion
`mv-592eca01`（version 1，source=teacher，created_by=totalsegmentator-total），
耗时 115.74s（含合并落盘，与基线一致）。

**显存测量方法说明**：WSL2 的 `nvidia-smi --query-compute-apps` 对部分进程返回
`[N/A]`（PID 过滤不可靠），基线脚本改用 `--query-gpu=memory.used` 全局值轮询：
峰值 5472 MiB 是推理期间全局显存最高点，推理增量 = 峰值 − 推理前基线（831 MiB
桌面残留）= 4641 MiB。两个数字都是实测；16 GB 卡余量充足。

---

## 4. 测试结果

| 项 | 结果 |
|---|---|
| pytest | **209 passed**（153 基线 + 56 新增），`uv run --extra dev --extra ingest pytest tests/`（CI 同构） |
| ruff | `ruff check .` **All checks passed!** |
| 白名单负测试 | `test_license_whitelist.py` 全部继续通过（brain_aneurysm BLOCKED 等） |
| zip 炸弹负测试 | `test_zip_bomb.py` 全部继续通过 |

新增测试分布：本体映射 24（三态/写回/隔离/API）+ OCR 精度 12（分辨率/旋转/漏检
边界）+ SEG 源实例 9（提取/持久化/真实引用 roundtrip）+ TUI 6（端点/冒烟/
显存条）+ 审阅 12（页面/静态/meta/image/mask/seg-meta/save/affine 篡改）+
worker 11（白名单/merge 守卫/venv 隔离/API）。

---

## 5. 非目标清单（niivue 不做项，§9.1）

- 4D/时间序列
- DTI（弥散张量成像）
- 三维任意切面自由角（保持三正交面 + 第 4 视口体绘制）
- 多人实时协同标注
- DICOM 原生渲染（niivue 吃 NIfTI，无需 DICOMweb 后端——弃 OHIF 的实质理由）

---

## 6. 遗留问题

1. **niivue MIP 原生支持**：0.53.1 无原生 MIP slice type，第 4 视口为体绘制
   （RENDER）+ 正交方位近似。M3+ 换 vendored 新版（0.69/1.x 有原生 MIP/更完整
   的 API）或自实现 MIP shader；升级是显式决策（API 不兼容）。
2. **niivue 完整管线帧耗时**：§2 红线实测为 WebGL 代理负载（texSubImage3D 单
   切片），niivue 完整管线（多视口 + overlay + 拾取）单帧耗时 M3 联调补测；
   代理负载 0.65ms 远低于红线，缺口预计不构成风险。
3. **逐类 mask 分通道渲染**：M2 用单 overlay（多类整数标签图）+ 类别级透明度
   开关；每类独立颜色（niivue 逐类配色）需 mask 分通道（M3+，与 QC 引擎一起）。
4. **审阅页保存与 TS mask 的衔接**：TS 落盘 mask 后审阅页从 MaskVersion 最新
   版读（source=teacher）；多类 label 名（totalseg_class 名单）进 seg-meta 端点
   需 TS 输出的 classes.json（TS 2.10 输出内含），M3 接入。
5. **M1-REPORT §8 遗留不变**：TotalSegmentator dataset 全量下载、100 MB 三处
   强制的另两处、多指纹部署语义、GitHub 远程未建、BSL LICENSE 占位。
6. **本体映射 v1 编码人工核对**：SNOMED/Uberon 编码取常用临床概念，M5 引全量
   图谱后机校。
7. **TUI 队列状态源**：`/monitor/pipeline` 为进程内内存态（单进程 M2 足够）；
   多进程 worker 部署时切 SQLite jobs 表（M0 SCHEMA 已有，M3 落库）。

---

## 7. 交付物清单

```
src/bodydonor/segmentation.py   新增：TS worker 分发层（白名单/防线/合并/落盘/显存实测）
src/bodydonor/ontology.py       新增：本体映射服务（三态/写回/逃生通道隔离）
ontology/v1/map.yaml            新增：38 条目静态查表（中英同义词 + SNOMED/Uberon）
src/bodydonor/tui.py            新增：Textual 监控面（GPU 条/队列/①→⑧）
src/bodydonor/static/review.html        新增：niivue 审阅页（单文件）
src/bodydonor/static/niivue.umd.js      新增：本地 vendored niivue 0.53.1
src/bodydonor/ingest.py         源实例提取（extract_source_instances，法向降序）
src/bodydonor/pipeline.py       源实例持久化 + 读回（_persist/load_source_instances）
src/bodydonor/models.py         Series.source_instances_json 新列
src/bodydonor/app.py            /ontology/* /monitor/* /review/* /segments 端点
src/bodydonor/cli.py            bodydonor ontology/segment/tui 子命令
src/bodydonor/storage.py        InProcessQueue.pending_ids()（监控只读）
scripts/bench_browser_frames.py 新增：WebGL 帧耗时基准（CDP Edge）
scripts/bench_gpu_baseline.py   新增：GPU 推理基线测量（显存轮询 + 墙钟）
scripts/dl_weights_resolve.sh   新增：权重下载 DNS 绕过（curl --resolve 硬编码 IP）
.venv-totalseg/                 独立 venv：TS 2.10.0 + torch 2.5.1+cu124 + torchvision 0.20.1+cu124
tests/test_ontology.py          新增 18 条 + test_api_ontology.py 6 条
tests/test_ocr_precision.py     新增 12 条（分辨率/旋转/漏检边界）
tests/test_source_instances.py  新增 9 条（真实引用 roundtrip）
tests/test_tui_smoke.py         新增 6 条（端点/冒烟）
tests/test_review_api.py        新增 12 条（审阅端点 + affine 篡改）
tests/test_segmentation.py      新增 11 条（白名单/守卫/隔离/API）
pyproject.toml + uv.lock        textual / pytest-asyncio / websocket-client
```

---

## 8. Blockers（单列）

1. **【已解决】torch 2.15 默认 cu132 与驱动 CUDA 12.5 不兼容**
   - 现象：`RuntimeError: The NVIDIA driver on your system is too old (found
     version 12050)`；torch.cuda.is_available() = False
   - 已试：确认驱动 556.18 / CUDA 12.5 → 查 TS 依赖约束（torch>=2.1.2）→
     重装 `torch==2.5.1+cu124`（cu124 build 向后兼容 12.5 驱动）
   - 结果：CUDA tensor OK（Quadro RTX 5000）
2. **【已解决】torchvision 与降级后的 torch 不匹配**
   - 现象：TS 运行时 `RuntimeError: operator torchvision::nms does not exist`
     （torchvision 还是 torch 2.15 配套版本，torch 已降 2.5.1）
   - 已试：`pip install torchvision==0.20.1 --index-url .../whl/cu124`
     （0.20.1 是 torch 2.5.1 配套版本）
   - 结果：`torch 2.5.1+cu124 | torchvision 0.20.1+cu124 | cuda True`
3. **【已解决】Tailscale MagicDNS 转发持续故障，GitHub 资产域解析失败**
   - 现象：`release-assets.githubusercontent.com` /
     `objects.githubusercontent.com` 解析失败（Errno -3 / -2）持续 ~1.5h；
     /etc/resolv.conf 指向 100.100.100.100（Tailscale MagicDNS），转发外部
     域间歇性失败；恰好断的是权重下载域。官方 `totalseg_download_weights`
     连续 3 次失败（Dataset291 拿下后 292/293/... 逐个 DNS 失败）
   - 已试：① 重试循环（DNS 恢复窗口内逐 part 拿，只拿下 part1）；
     ② **`curl --resolve` 硬编码 GitHub 边缘 IP 绕过 DNS**（github.com
     20.205.243.166 + 资产域 185.199.108.133，/etc/hosts 已有 github.com 映射
     佐证该 IP 可用），逐 part 下载 zip → 复现官方 `download_url_and_unpack`
     解压逻辑（`zipfile.extractall(config_dir)`）→ checkpoint 校验
   - 结果：total 任务 6 个 part 全部落位（291 器官 / 292 椎体 / 293 心脏 /
     294 肌肉 / 295 肋骨 / 298 body_6mm），1.4 GB，6 个 fold_0
     checkpoint；脚本 `scripts/dl_weights_resolve.sh`（方法可复现）
4. **【已解决】WSL2 nvidia-smi --query-compute-apps 返回 [N/A]**
   - 现象：显存采样按 PID 过滤拿不到样本（TS 多进程主 PID 不出现在
     compute-apps，或全程 `[N/A]`），两次基线 vram_samples_n=0
   - 已试：改用 `--query-gpu=memory.used` 全局值 1s 轮询（峰值 − 推理前
     基线 = 推理增量）
   - 结果：113 个样本，峰值 5472 MiB / 增量 4641 MiB（见 §3）

---

## 9. 工作方式记录（提案精神的最小合理选择）

- **审阅页第 4 视口**：0.53.1 无原生 MIP → 体绘制（RENDER）+ 正交方位相机
  （§1.3 写明，M3+ 升级）。
- **消歧写回文件**：`overrides.yaml` 与主表分离（git diff 主表 = 领域变更评审，
  git diff overrides = 用户决策记录）。
- **TUI 轮询周期**：2s（nvidia-smi 快照 ~100ms，2s 足够"实时"且不刷屏）。
- **OCR 精度边界固化**：≤36px 漏检写进断言（`test_miss_boundary_below_40px_documented`），
  精度边界是测试资产不是可调阈值。
- **源实例排序**：法向降序（DICOM 帧序惯例，帧 0 = 法向距离最大），与
  export_seg 合成路径方向一致（roundtrip diff=0 验证）。
