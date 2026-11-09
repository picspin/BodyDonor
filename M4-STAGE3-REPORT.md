# M4 阶段 3 报告 — nnInteractive 接线为交互修正后端（v2，命名纠正后）

> 日期：2026-11-09。分支 main，起点 HEAD 12595b4（领先远端 22 提交）。
> 铁律全程遵守：未 push、未建 PR、未动 eval/holdout.txt；训练占卡期间（PID 45196
> 持有 `~/data/BodyDonor/.gpu.lock`，job=nnunet-stage2b-45196）未跑任何 GPU
> 推理/未加载模型到 CUDA。
> 本报告为 v2 重写：第一轮草稿（v1）的 torch 安装结论、来源命名均有实测出入，
> 按 2026-11-09 下午实测重写（v1 错误与纠正见 §1.1 与 §13）。

## 1. 独立 venv 与版本实测

### 1.1 v1 草稿错误纠正（实测为准）

v1 草稿写"torch 2.5.1+cu124 已装、与另两个 venv 对齐"——**错**。实测时间线
（`logs/m4s3-venv-install.log` 全程为证）：

1. `=== install start 2026-11-09T14:48:34+08:00 ===`
2. torch-2.5.1+cu124 wheel（908.2 MB）下载完成（0:26:18，779.0 kB/s），
   但随后装 **nvidia-cuda-nvrtc-cu12==12.4.127** 时 `pypi.nvidia.com` DNS
   解析失败（`Temporary failure in name resolution`），pip 重试 4 次均失败，
   **`error: connection-failed`，torch==2.5.1 安装失败**（日志第 26 行）。
3. 安装脚本无 `set -e`，失败后仍回显了 `=== torch done 2026-11-09T15:24:22+08:00 ===`
   ——**该回显不能作为安装成功的证据**（v1 草稿正是被它误导）。
4. 之后 pip list 只剩 pip，证实 torch 从未装上。
5. `pip install nnInteractive==2.6.0` 成功（16:45:57），torch 被 resolver 顶到
   **2.15.0+cu132**（`torch!=2.9.*,>=2.1.2` 约束下 resolver 取最新，不是上游要求；
   从 nnInteractive-2.6.0 wheel METADATA 核实 `Requires-Dist: torch!=2.9.*,>=2.1.2`，
   2.5.1 满足要求）。
6. torch 装回 2.5.1（§1.2）：cu124 index 依赖的 `pypi.nvidia.com` DNS 仍失败，
   走普通 PyPI fallback（PyPI 的 linux torch 2.5.1 wheel 同为 +cu124 build）。

### 1.2 版本实测（pip list + import 实测，不以 pip 回显为准）

| 项 | 值（实测） |
|---|---|
| venv | `.venv-nninteractive`（uv 管理，CPython 3.12，`.venv-nninteractive/bin/python`） |
| torch | **2.5.1**（PyPI linux wheel，+cu124 build；`import torch` 实测见 §8） |
| torchvision | 0.20.1（与 torch 2.5.1 匹配；2.15.0 的 torchvision 硬 pin torch==2.15.0，必须同降） |
| nnInteractive | 2.6.0 |
| nninteractive-client | 2.6.0 |
| nnunetv2 | 2.8.1（nnInteractive 依赖，与 .venv-nnunet 的 nnUNet 栈同大版本） |
| 关键依赖 | numpy 2.5.4 / SimpleITK 2.5.7 / nibabel 5.4.2 / scipy 1.18.1 / huggingface_hub 2.4.0 |
| nnInteractive 安装耗时 | 14:48:34 起含失败重试 + torch 908.2 MB 下载 26:18 + nnInteractive 及依赖装包，至 16:45:57 全栈装完（日志 `logs/m4s3-venv-install.log`） |
| torch 装回耗时 | 16:51:46 → 17:53:27（61.7 分钟：torch-2.5.1 wheel 906.4 MB + 全套 cu124 依赖 cudnn 664.8 MB / cublas / cufft / cusparse / nccl / triton 等 PyPI 下载 + 装包 + import 三连校验，日志 `logs/m4s3-torch-downgrade.log`） |
| .gitignore | `.venv-totalseg/`、`.venv-nninteractive/` 显式排除（`git check-ignore` 三者全过） |

隔离纪律实测：主 .venv 无 torch/nnInteractive；`.venv-totalseg` 未被污染
（pip list 仅 TotalSegmentator 2.10.0 + torch 2.5.1+cu124，安装前后一致）。

## 2. 命名纠正（v1→v2，用户 2026-11-09 决定）

**决定出处**：用户 2026-11-09 补充（stage3-prompt.md）+ M5-PLAN.md §4 三态。
提案 M2 的 `MaskVersion(source=human)` 写法作废。

三态语义：

| source | 含义 |
|---|---|
| `teacher` | TotalSegmentator 原始预测 |
| `nninteractive_guided` | **规范来源名（v2）**：人工提示引导的模型输出，带提示记录 |
| `human` | 纯画笔修正 / 逐体素精修——**nnInteractive 产物绝不用这个名** |

改动文件：

| 文件 | 改动 |
|---|---|
| `src/bodydonor/nninteractive_backend.py` | `CORRECTION_SOURCE = "nninteractive_guided"`；新增 `CORRECTION_SOURCE_LEGACY_ALIASES = ("manual-correction-nninteractive", "nninteractive")`；sidecar source 字段、模块 docstring、help 文案同步 |
| `src/bodydonor/ontology.py` | `mask_allowed_in_distill_training` 拒绝三值：规范名 + 两个 legacy 别名 |
| `src/bodydonor/distill_data.py` | `REJECTED_SOURCES` 新增 `nninteractive_guided` key，**保留**两个 legacy key |
| `src/bodydonor/app.py` | meta 端点 correction_source fallback 字符串 + 端点 docstring |
| `src/bodydonor/cli.py` | `nninteractive-correct` docstring + 表格文案 |
| `tests/test_nninteractive_backend.py` | 门禁测试：规范名拒绝 + 两个 legacy 名拒绝（三值覆盖） |
| `tests/test_ontology.py` | 两用例适配：`test_nninteractive_guided_blocked_from_training` + `test_nninteractive_legacy_names_blocked_from_training` |

**legacy 别名保留拒绝的理由**：`manual-correction-nninteractive`（v1，阶段 2a
已有 sidecar 用过此名）与 `nninteractive` 是历史字符串，永不再由新代码写出，
但门禁拒绝清单保留——删掉拒绝会让历史 sidecar 来源通过蒸馏门禁，是安全倒退
（test_provenance_reads_declared_nninteractive 锁定：历史 sidecar 读出的 legacy
名仍被 check_distill_source 拒绝）。

## 3. provenance（可追溯）

每个修正产物的 sidecar JSON 必含 `provenance` 块：

```json
{
  "source": "nninteractive_guided",
  "license": "CC BY-NC-SA 4.0",
  "provenance": {
    "sessionId": "<每次修正运行新生成的 uuid4>",
    "baselineTeacherVersion": <该 series 基线 teacher mask 的 MaskVersion 版本号 | null>,
    "baselineTeacherVersionReason": "<null 时注明原因，不猜数字>",
    "prompts": [<全量提示点/框坐标序列，可重放>],
    "acceptedClasses": [<target_label>]
  }
}
```

- **sessionId**：每次修正运行新生成 `uuid.uuid4()`（`correct_image` 内生成，
  与 job_id 不同——job_id 可由调用方指定复用，sessionId 每次运行唯一）。
- **baselineTeacherVersion**：`_lookup_baseline_teacher_version` 只读查询
  （SELECT MaskVersion WHERE series_id ORDER BY version DESC LIMIT 1，DB 只读
  不写不锁）；series_id 从原图路径推出（storage_key 倒数第二段，截断 60 字符
  与列宽一致）。查不到（无 DB / 无行 / DB 异常）写 `null` +
  `baselineTeacherVersionReason` 注明原因（不猜数字）。
- **prompts**：全量提示序列原样落盘（runner 吃同一列表，可重放）。
- **acceptedClasses**：`[target_label]`。

sidecar 完整字段见 §2 v1 清单 + `provenance` 块。

**MaskVersion.source 列宽问题（留给 M5-e）**：`MaskVersion.source` 列是
String(16)，`nninteractive_guided` 共 20 字符塞不进去——本阶段不建 MaskVersion
行、不改列宽，provenance 只落 sidecar JSON。M5-e 已知输入：若要把 nnInteractive
修正产物登记进 MaskVersion（例如为了 QC 评分挂接），须先改列宽（String(24)+
）或改用来源映射表。

## 4. 接线文件清单

| 文件 | 角色 |
|---|---|
| `src/bodydonor/nni_runner.py`（新，bfd2cca） | 在 `.venv-nninteractive/bin/python` 内执行的 JSON 任务 runner。照抄 ts_runner.py 契约：stdin 任务 JSON `{image_nii, prompts, target_label, device, output_dir, job_id, model_dir}` → stdout 纯 JSON 结果 `{status, exit_code, mask_path, wall_seconds, load_seconds, inference_seconds, peak_vram_mb, error, license, n_target_voxels}`，日志走 stderr。prompt 结构校验（runner 不信任 stdin）；原图不预处理（原样 float 进 set_image）；显存峰值 = torch.cuda.reset_peak_memory_stats() + max_memory_allocated()（分配器口径，主值；CPU 模式 null + reason 注明，不猜数字）；修正 mask 过 nifti_geom.assert_affine_match 守卫 |
| `src/bodydonor/nninteractive_backend.py`（新，bfd2cca + a93fabb） | 主 .venv 模块（**不 import torch/nnInteractive**）。组装 runner 命令 + 子进程（stdin/stdout JSON）+ 主进程 nvidia-smi 轮询交叉核对（全局口径 = 轮询峰值 − 基线）+ 修正产物落盘 + sidecar JSON（含 provenance）。GPU 锁短会话模式；`GpuBusyError`（NnInteractiveError 子类）承载 fast-fail；双保险（锁 + nvidia-smi 实占 >50% 拒绝） |
| `src/bodydonor/cli.py`（bfd2cca + a93fabb） | 新命令 `bodydonor nninteractive-correct`：`--point x,y,z`（可重复，正点）/ `--negative-point`（负点）/ `--bbox x0,x1,y0,y1,z0,z1`（2D bbox，单切片维 [d,d+1]）/ `--target-label` / `--output-dir` / `--device auto|cpu|cuda` / `--job-id` / `--json`。GPU 忙退出码 2 |
| `src/bodydonor/app.py`（bfd2cca + a93fabb） | POST `/review/{series_id}/nninteractive-correct`（body `{point:{x,y,z}, negative_point?, target_label?, device?, job_id?}`）+ meta 端点 license 字段（§6）。GPU 忙 409，不排队不重试。与 CLI 同一后端函数 correct_image |
| `src/bodydonor/ontology.py`（bdaa579 + a93fabb） | `mask_allowed_in_distill_training` 补 nnInteractive 来源拒绝（三值，§2） |
| `src/bodydonor/distill_data.py`（a93fabb） | `REJECTED_SOURCES` 三值（§2） |
| `src/bodydonor/static/review.html`（cf8c4fb） | 页脚 license 展示位（§6） |
| `tests/test_nninteractive_backend.py`（新，9dfb112） | 许可门禁三值 / 锁互斥 / CPU runner 契约 / API 端点（§8） |
| `tests/test_ontology.py`（a93fabb） | 门禁拒绝两用例适配 v2 命名 |
| `.gitignore`（bfd2cca） | 加 `.venv-totalseg/`、`.venv-nninteractive/` |
| `scripts/install_torch251.sh`（本轮，未提交） | torch 2.5.1 装回脚本（cu124 index → PyPI fallback；torchvision 0.20.1 同装；import 三连校验） |
| `scripts/download_nni_model.sh`（本轮，未提交） | 模型权重下载脚本（§5） |

## 5. 模型权重下载（实测）

| 项 | 值 |
|---|---|
| 下载方式 | `ensure_model_available(get_default_model_id())`（nnInteractive.model_management），`NNINTERACTIVE_MODEL_DIR=~/data/BodyDonor/nninteractive-models` |
| 模型 id | `nnInteractive_v1.0`（manifest default；`models.json` 描述 "Original nnInteractive v1 model checkpoint"） |
| 大小 | 393 MB（`du -sm` 实测落盘目录） |
| 耗时 | 713.6 s（16:57:00 → 17:08:54，HF Hub 5 文件，未认证限速） |
| 落盘路径 | `~/data/BodyDonor/nninteractive-models/models/nnInteractive_v1.0/`（fold_0/checkpoint_final.pth + plans.json + dataset.json + inference_session_class.json + LICENSE） |
| 权重许可 | 模型文件夹自带 LICENSE 实测：**CC BY-NC-SA 4.0**（与 LICENSES-THIRD-PARTY.md §2.2 核实一致） |

## 6. license 展示位置（后端字段 + review.html）

后端（已做，cf8c4fb）：
- `GET /review/{series_id}/meta` 返回 `license` 字段：
  `{teacher_weights: "Apache-2.0（TotalSegmentator total 任务，任意用途）",
  nninteractive_weights: "CC BY-NC-SA 4.0（非商业 + ShareAlike）：仅交互式修正辅助；修正产物不进商用蒸馏训练集",
  reference: "LICENSES-THIRD-PARTY.md §2.2/§7/§8"}`。
- 该 series 有 nnInteractive 修正产物（sidecar 在 series 目录）时，meta 额外返回
  `correction_source`（v2 fallback 字符串）与 `correction_license_notice`。

review.html（已做，cf8c4fb）：
- 页脚一行 `#license-line`：静态文案 + meta.license 动态（`showLicense()`）。
- 有修正产物时红色高亮 `.ni-warning`（"⚠ 本序列含 nnInteractive 修正产物
  （CC BY-NC-SA 4.0）：不进商用蒸馏训练集"）。

未做 / 缺口：审阅页正文无 nnInteractive 修正入口按钮（交互修正走 CLI 与 API，
页面接线后置）；JS 无测试框架，页面渲染未在浏览器实测（meta 字段由
tests/test_review_api.py 覆盖）。

## 7. 上游 API 实测与出入

`nnInteractiveInferenceSession(device=..., use_torch_compile=False,
do_autozoom=True)` / `initialize_from_trained_model_folder` / `set_image`
(1,x,y,z) float / `set_target_buffer(uint8 zeros)` / `add_point_interaction
((x,y,z), include_interaction=True|False)` / `add_bbox_interaction`。

CPU 实测出入（三处，均已修复，commit fd81522）：

1. **`from bodydonor.nifti_geom import ...` 不可用**：nni venv 无 bodydonor
   包（Q7 隔离），affine 守卫内联进 runner（同语义）。
2. **stdout 污染**：`initialize_from_trained_model_folder` 往 stdout 打
   "session initialized"——库日志必须重定向 stderr（`_stdout_to_stderr`），
   stdout 只留结果 JSON。
3. **model folder 语义**：`initialize_from_trained_model_folder` 要模型文件夹
   本身（`.../models/nnInteractive_v1.0`），不是 model root——
   `_resolve_model_folder` 解析（root → default model id → 落盘布局）。
4. **session.license 实测**：模型文件夹自带 LICENSE = CC BY-NC-SA 4.0，
   runner 读 session.license 实测一致。

GPU 路径实测出入待 GPU 真机验证（顺延，§8.3）补记。

## 8. 真机验证

### 8.1 锁互斥 live 验证（GPU 占卡时能做，已做 ✅）

- 真实锁文件 `~/data/BodyDonor/.gpu.lock`（持有者 pid=45196 task=nnunet
  job=nnunet-stage2b-45196，reason=batch；训练实测 9076 MiB / 93%，进程
  etime 2h36m 存活）：`gpu_lock.acquire(path, reason="interactive",
  task="nninteractive", job_id="live-check", wait=0)` **立即返回 None
  （elapsed=0.000s）** + fast-fail 日志（"[gpu_lock] 交互会话 fast-fail：
  有批量推理任务在跑（pid=45196 task=nnunet job=nnunet-stage2b-45196）"）。
  **只读式验证：真实锁文件未删未改**（验证后 lock_status 复核 held=True、
  job_id 不变）。stale 回收先行核查：recover_stale_lock 对活进程不动作。
- 临时锁路径单测级互斥：tests/test_nninteractive_backend.py::TestLockMutex
  （假持有者 fast-fail、释放后可抢、CPU 模式不占锁、release_on_idle 从锁文件
  acquired_at 起算语义）——14 passed（§10）。

### 8.2 CPU 小输入验证（GPU 占卡时能做，已做 ✅）

- 输入：真实 case amos_0004（512×512×78，`~/data/BodyDonor/downloads/
  amos22_subset/train/imagesTr/amos_0004.nii.gz`，16.2 MB）裁 48³ patch
  （真实数据，非合成；中心 (318,255,54) 肝区——实测 HU 100 软组织，非空气；
  初选 (128,128,64) 实测 HU -959 是肺/空气区，已修 commit 9d3a0c3）。
- 流程：加载模型（nnInteractive_v1.0，CPU）→ 点提示（patch 中心 24,24,24）→
  出 mask → 保存。20 分钟上限，超时即停如实记录。
- 实测结果（exit 0，三次运行数字一致量级）：
  - load 7.61–8.37 s / inference 41.72–48.85 s / wall 总计 60.3–67.7 s
    （第 1 次 8.37 / 45.25 / 65.3；第 2 次 7.96 / 48.85 / 67.7；
    第 3 次 7.61 / 41.72 / 60.3，2026-11-09 18:22:58）
  - n_target_voxels = 4055（target_label=1，三次运行相同）
  - CPU 内存峰值 **7129 MiB**（第 3 次实测，`resource.getrusage(RUSAGE_CHILDREN).ru_maxrss`，
    即加载 torch + 400MB 模型的 runner 子进程峰值；第 1/2 次报告的 54 MiB 是错的——
    当时取的是 `RUSAGE_SELF`（父进程，未加载 torch/模型），已在母代理复核时发现并修正，
    修正脚本 scripts/cpu_smoke_verify.sh，见 §13 补记）
  - 产物：`/tmp/nni-cpu-verify/amos_0004/correction_mask.nii.gz`（1.9K）+
    `correction_source.json`（1.5K）
  - source = nninteractive_guided；license = CC BY-NC-SA 4.0
    （license_from_session 实测一致）
  - provenance：sessionId 每次运行新生成 uuid4（两次不同，实测）；
    baselineTeacherVersion = null + 原因（"series nni-patch-zwz4ydrp 无
    MaskVersion 记录（基线版本查不到）"——tmp patch 无 DB 行）；prompts
    全量落盘；acceptedClasses = [1]
  - model_id = nnInteractive_v1.0（runner _resolve_model_folder 实测）
- 途中失败如实记录（logs/m4s3-cpu-verify.log）：attempt 1 ModuleNotFoundError
  （bodydonor import）、attempt 2 stdout 污染 + FileNotFoundError（model
  folder 语义）——三处修复后 attempt 3/4 通过（fd81522）。

### 8.3 GPU 真机验证（顺延）

**训练占卡（PID 45196，9076 MiB / 93%，预计 2026-11-10 21:53 前后结束），
绝不在训练占卡时跑 GPU 推理**。顺延到训练结束后，在 amos_0004 上跑一次完整
GPU 交互修正（模型加载耗时 / 单次推理耗时 / 显存峰值 torch 主值 + nvidia-smi
交叉核对 / 修正产物路径与来源标签），届时命令（可直接跑，点坐标已按
combined_mask 的 liver（index 44，249670 体素）bbox 中心实测填入）：

```bash
# 训练结束后先确认锁已释放（应报 GPU 锁空闲或持有者已死可回收）：
cat ~/data/BodyDonor/.gpu.lock 2>/dev/null || echo "GPU 锁空闲"

# GPU 交互修正（点提示 = liver bbox 中心，amos_0004 原图体素坐标 512×512×78）：
~/work/BodyDonor/.venv/bin/bodydonor nninteractive-correct \
  ~/data/BodyDonor/downloads/amos22_subset/train/imagesTr/amos_0004.nii.gz \
  --point 318,255,66 \
  --target-label 44 \
  --output-dir /tmp/nni-gpu-verify/amos_0004 \
  --device cuda \
  --job-id nni-gpu-verify-amos0004 \
  --json
# 跑完交叉核对显存峰值：
/usr/lib/wsl/lib/nvidia-smi --query-gpu=memory.used --format=csv,noheader
# sidecar 复核（来源标签 + provenance）：
cat /tmp/nni-gpu-verify/amos_0004/correction_source.json
```

（target_label=44 = liver：`storage/amos_0004/nifti-b63b1d93/labels.json` index 44
= liver，combined_mask.nii.gz 实测 44 有 249670 体素、bbox 中心 (318,255,66)，
z=66 在 78 层范围内。）

## 9. 遗留

1. **server 模式未接**（nninteractive-server / RemoteInferenceSession）：本地
   后端优先、单机单卡 + 锁互斥已够用；server 常驻会话与短会话锁模式冲突
   （常驻 = 一直持锁或闲置放锁被别的任务抢走权重，两者都不可接受）。后置。
2. **GPU 真机验证顺延**：§8.3（训练占卡，预计 2026-11-10 21:53 前后结束；
   届时命令已给全）。
3. **MaskVersion.source 列宽**：String(16) 放不下 20 字符 nninteractive_guided——
   不建行、不改列宽，provenance 只落 sidecar JSON（M5-e 已知输入，§3）。
4. **review.html 无 nnInteractive 修正入口按钮**：本阶段交互修正走 CLI/API；
   页面接线（点选坐标 → 调 API）后置。
5. **API 同步长推理取舍**：单次交互修正秒级~分钟级，本阶段 API 只接线、不做
   异步队列（队列后置；长推理不适合同步时由调用方用 CLI）。
6. **prompt 形式**：API 端点只接 point（正/负），bbox 走 CLI（上游 2D bbox
   限制，3D bbox 须上层切片拆分，后置）。
7. **release_on_idle(600) 语义**：兜底而非主路径——闲置时长从锁文件
   acquired_at 起算，不是"最后一次操作"；本后端选择短会话模式（每次修正
   acquire→跑→存→release），不做长会话 + 刷新 acquired_at。
8. **scripts 已提交**（母代理复核时补交）：scripts/install_torch251.sh、
   scripts/download_nni_model.sh、scripts/cpu_smoke_verify.sh 是本轮运维脚本
   （torch 装回 + 权重下载 + CPU 验证），随 RSS 修正一并提交，复现路径有据可查。
9. **_resolve_model_folder 触发网络**：get_default_model_id 拉 remote manifest
   （无本地缓存时）；已下好模型走 is_model_downloaded 短路（离线可用），
   但 root 解析路径首次触发 HF 请求——离线环境下依赖 models.json 本地缓存
   （首次在线运行已落盘）。

## 10. 验证记录（实测数字）

| 项 | 实测值 |
|---|---|
| 锁 live fast-fail | ✅ acquire(wait=0) 立即 None（0.000s）+ fast-fail 日志（pid=45196）；真实锁未删未改 |
| runner JSON 契约干跑 | ✅ nni venv python 执行 nni_runner.py，stdin→stdout 纯 JSON，坏输入 exit 2 |
| 模型下载 | ✅ nnInteractive_v1.0 / 393 MB / 713.6s / `~/data/BodyDonor/nninteractive-models/models/nnInteractive_v1.0/` / LICENSE=CC BY-NC-SA 4.0 |
| torch 装回 2.5.1 | ✅ pip exit 0（17:53:19）；`import torch: 2.5.1+cu124 cuda_built True`（17:53:27）；`import nnInteractive ok`；`from nnInteractive.model_management import list_models` ok——三连校验全过 |
| CPU patch 推理 | ✅ load 7.61–8.37s / inference 41.72–48.85s / wall 60.3–67.7s / n_target_voxels=4055 / cpu_peak_rss **7129 MiB**（runner 子进程峰值，RUSAGE_CHILDREN；三次运行 exit 0） |
| CPU 修正产物 | ✅ `/tmp/nni-cpu-verify/amos_0004/correction_mask.nii.gz` + sidecar；source=nninteractive_guided；license=CC BY-NC-SA 4.0；provenance={sessionId(uuid4 每次新), baselineTeacherVersion=null+原因, prompts 全量, acceptedClasses=[1]}；model_id=nnInteractive_v1.0 |
| GPU 真机验证 | 顺延（§8.3） |

## 11. 提交清单

| hash | 内容 |
|---|---|
| bdaa579 | feat(m4): ontology 门禁补 nnInteractive 来源缺口（v1 名，后续 a93fabb 改名） |
| cf8c4fb | feat(m4): 审阅侧 license 展示 + nninteractive-correct API 端点 |
| bfd2cca | feat(m4): nnInteractive 接线为交互修正后端——nni_runner + backend + CLI |
| a93fabb | refactor(m4): 来源改名 nninteractive_guided + 修正产物 provenance（命名 v1→v2） |
| 9dfb112 | test(m4): nnInteractive 后端单测——门禁三值/锁互斥/CPU runner 契约/API 端点 |
| 9d3a0c3 | fix(m4): CPU runner 契约测试 patch 中心改肝区——(128,128,64) 是空气区实测 HU -959 |
| fd81522 | fix(m4): nni_runner 三处修复——bodydonor import 解耦 + stdout 污染 + model folder 解析 |
| 本报告 | docs(m4): 本报告（M4-STAGE3-REPORT.md v2；amend 后 hash 以 git log 实测为准——amend 自引用循环，不回填） |

## 12. pytest 点数与 ruff

- ruff：全绿（改名 + provenance + nni_runner 修复 + 测试改动，src 全量 +
  tests 改动文件）。
- pytest 相关子集（test_ontology.py + test_distill_license_gate.py +
  test_gpu_lock.py + test_review_api.py + test_api.py）：**87 passed**（a93fabb 后实测）。
- test_nninteractive_backend.py：**17 passed**（torch 2.5.1 装回后全量通过，
  含 TestRunnerContractCpu 3 用例——真实 amos_0004 48³ patch CPU 跑通）；
  torch 装回前为 14 passed, 3 deselected。
- **全量 pytest：366 passed, 0 failed**（295.34s，fd81522 后实测，含 CPU runner
  契约；torch 装回前为 363 passed, 3 deselected）。

## 13. v1→v2 报告差异一览

| 项 | v1 草稿 | v2 实测 |
|---|---|---|
| torch 2.5.1 首装 | "已装、对齐" | **失败**（pypi.nvidia.com DNS 解析失败，connection-failed；"torch done" 回显不可信） |
| torch 实际版本 | 2.5.1+cu124 | 首装后 2.15.0+cu132（resolver 取最新）→ 装回 2.5.1（PyPI fallback） |
| torch 装回路径 | — | cu124 index 依赖 pypi.nvidia.com 仍 DNS 失败 → 普通 PyPI（同 +cu124 build） |
| 来源命名 | manual-correction-nninteractive | **nninteractive_guided**（规范名 v2）+ 两个 legacy 别名保留拒绝 |
| CPU 内存峰值 | 54 MiB（`RUSAGE_SELF` 父进程，未加载 torch/模型——错） | **7129 MiB**（`RUSAGE_CHILDREN` runner 子进程峰值，第 3 次 CPU 验证 2026-11-09 18:22:58 实测；修正脚本 cpu_smoke_verify.sh） |
| provenance | 无 | sidecar 必含 {sessionId, baselineTeacherVersion, prompts, acceptedClasses} |
| 安装耗时表述 | "torch wheel 908.2 MB 下载约 25 分钟"（未完成表述） | 以日志时间戳为准：install start 14:48:34、torch wheel 下载 0:26:18（779.0 kB/s）、error 后 15:24:22 "torch done" 回显（不可信）、nnInteractive 全栈 16:45:57 |

## 14. 自查（headless 下 /code-review 斜杠命令不可用，逐项自查）

- 无 mock 数字：本报告所有数字来自实测命令输出（nvidia-smi、du、stat、pip list、
  pytest、lock_status、日志时间戳、resource.getrusage(RUSAGE_CHILDREN)（runner 子进程峰值；
  注意：第 1/2 次 CPU 验证的 54 MiB 取的是 RUSAGE_SELF（父进程），已修正为 7129 MiB，见 §8.2）；CPU 推理数字为
  logs/m4s3-cpu-verify.log 实测（exit 0，两次运行），不预填。
- 无 GPU 违规调用：训练占卡期间未加载模型到 CUDA（`_detect_device` 只查
  `torch.cuda.is_available()`；live 锁验证只读锁文件；模型下载纯网络 + CPU；
  CPU 验证 device="cpu"，权重下载/验证脚本无 CUDA import）。
- 来源标签与 provenance 齐全：CORRECTION_SOURCE=nninteractive_guided（v2 规范名）；
  sidecar provenance 四字段 + null 注明原因机制；sessionId 每次运行唯一
  （单测锁定 + CPU 验证两次运行实测不同 uuid）；model_id 实测
  nnInteractive_v1.0。
- 门禁覆盖：ontology 版（三值拒绝）+ distill_data 严格版（REJECTED_SOURCES 三值）
  + sidecar 常量一致性 + legacy 历史读出仍拒绝，四处测试锁定。
- ruff/pytest：见 §12（全量 366 passed, 0 failed）。
- 未推、未建 PR：git log 确认本地提交（a93fabb / 9dfb112 / 9d3a0c3 / fd81522），
  无 push。
- 未动 eval/holdout.txt：未触碰。
- 未污染 .venv-totalseg / .venv-nnunet / 主 .venv：torch 装回只动
  .venv-nninteractive。
