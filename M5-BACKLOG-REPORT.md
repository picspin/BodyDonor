# M5-BACKLOG-REPORT — BodyDonor M5 遗留 backlog 收尾交付报告

> 日期：2026-11-11。收尾 commits（本地 main，未推，HEAD 之后新 3 个）：
> `fa04f7f` bbox/scribble 后端接线 → `570c831` M0 迁移 stale 补列 →
> `b48717b` gpu_probe --output + M5-REPORT 首部笔误。依据 M5-REPORT §5 遗留。
> 全程未 push、未建 PR、未改 eval/holdout.txt、未改 M4 门禁结果（收尾读库
> 复核：ModelVersion gate_status=passed 保持）。

---

## 1. 各项结果

### 1.1 bbox/scribble 提示后端接线（`fa04f7f`，M5-REPORT §5.2 关闭）

- **runner（nni_runner.py）**：修 `add_bbox_interaction` 缺 `include_interaction`
  的 TypeError——上游签名 `(bbox_coords, include_interaction,
  run_prediction=True, ...)`，include 无默认值（源码核实
  `.venv-nninteractive/.../inference_session.py:1350`），旧调用
  `session.add_bbox_interaction(p["bbox"])` 必然 TypeError，bbox 分支从未真实
  跑通；修复后恒 `include=True`（bbox 提示语义恒为正向）。
  新增 `_scribble_to_mask`：前端点轨迹 → 与原图同形状 uint8 mask（round 取整、
  越界丢弃）——上游 `add_scribble_interaction(scribble_image,
  include_interaction=True, ..., interaction_bbox=None)` 入参是 scribble mask
  图（形状须匹配 `original_image_shape[1:]`），不是点列表（源码核实
  inference_session.py:1536 + `_add_image_interaction` 形状校验）。
  `_validate_prompts` 加 scribble 分支。
- **capability 核实（不猜）**：本 checkpoint（legacy metadata
  `inference_session_class.json`，无 inference_info.json）经
  `_legacy_default_capability()`：scribble=True / bbox2d=True / bbox3d=False
  ——前端轴位拖框（同切片维 [d,d+1]）走 bbox2d 可用；不支持时
  `_check_capability_or_warn` 直接 ValueError。
- **端点（app.review_nninteractive_correct）**：新契约接 `prompts` 列表
  （point/bbox/scribble，沿用 runner JSON 风格，非法 type/形状/空 points →
  422）；旧 `point`/`negative_point` 兼容保留。
- **前端（review.html）**：`m5RunInference` 改发全量 prompts（point ±1 体素
  去重与 merge_prompts 同口径；bbox/scribble 不合并全量发送）；
  bbox/scribble 落点补 `m5ScheduleInference`（原来只有 point 触发）；
  "当前后端仅支持点提示" toast 改为接通后文案。
- **真实小输入验证（无 mock，CPU 48^3 patch = M4 阶段 3 同款，amos_0004
  肝区）**：
  | 提示 | load | inference | n_target_voxels | 备注 |
  |---|---|---|---|---|
  | bbox [[20,28],[20,28],[24,25]] | 3.7s | 18.46s | 72 | 8×8 单切片框 → 模型预测 72 体素 |
  | scribble 9 点轨迹（z=24 短笔画） | 3.41s | 19.43s | 2596 | 稀疏轨迹 → 模型预测 2596 体素区域 |
  两次 sidecar provenance 全量记录（prompts 可重放 + acceptedClasses +
  sessionId uuid4）；n_target_voxels 与落盘 mask 交叉核对一致。
  **scribble 实测支持**（不必降级 toast）。
- 单测：TestScribbleToMask 3 + TestRunnerValidatePromptsScribble 5 + 端点
  prompts 契约 5（含旧契约兼容）+ 前端标记同步 1。

### 1.2 浏览器实测准备（本次只做准备；真实浏览器驱动由 parent 另行做）

- **后端已启动并保持运行**：`nohup uvicorn bodydonor.app:app --host 0.0.0.0
  --port 8000`（pid 222998/223000，日志 /tmp/uvicorn-bodydonor.log）。
- **URL**：`http://100.72.182.77:8000/review?series_id=nifti-b63b1d93`
  （Tailscale，从本机 curl 实测 200；Muse VM 经同一地址可达）。
- **series_id**：`nifti-b63b1d93`（case amos_0004，CT，78 层，has_mask=true，
  70 类 mask；meta/labels.json/seg-meta/image.nii.gz/mask.nii.gz 均 200 实测）。
- **niivue 加载方式**：**本地 bundle**（`/static/niivue.umd.js`，691KB
  vendored，不走 CDN）——review.html:7 `<script src="/static/niivue.umd.js">`。
- 未做浏览器驱动实测：真实渲染与点击行为待 parent 验证（诚实边界同
  M5-REPORT §3.1）。

### 1.3 M0 迁移 stale 修复（`570c831`，M5-REPORT §5.5/§3.6 关闭）

- 新迁移 `b8e21c4a7f05`（down_revision=e7f3a1c9d204）：series 表补
  `source_instances_json`（JSON, nullable=True，与 models.py:107 一致）；
  不改写已应用的 M0 迁移 hash；列已存在时跳过（PRAGMA 查询，幂等）。
- 验证（subprocess 实测）：
  - 全新临时库 `alembic upgrade head` → rc=0，version=b8e21c4a7f05，三段
    迁移链（3c17abaf19f7 → e7f3a1c9d204 → b8e21c4a7f05）顺序执行，列在。
  - 开发库（bodydonor.db）：upgrade 前 current=e7f3a1c9d204、列已在
    （create_all 建成）；upgrade 后 version=b8e21c4a7f05、列在（no-op 补列
    路径）、5 行 series 保留、ModelVersion gate_status 仍 passed。

### 1.4 M5-REPORT 首部笔误（`b48717b`）

- "M5 补丁 6 个提交" → 7 个（`git log c2b2901~1..d3baaa9` 实数 7：
  c2b2901 / a431478 / dcd5cd2 / 539ffe6 / 0a292dd / 9353f6e / d3baaa9），
  修正注记写明实数方式与日期。

### 1.5 gpu-probe.json 纯 JSON 化（`b48717b`）

- `scripts/gpu_probe.py` 加 `--output <path>`：json.dump 直写决策文件
  （不走 shell 重定向——gpu_lock 日志走 stderr，`> file 2>&1` 会把
  `[gpu_lock]` 行混进 JSON，旧文件头两行即此坑）；`_emit` 统一三路输出。
- `~/m5-logs/gpu-probe.json` 重写为纯 JSON：只去掉头两行 `[gpu_lock]` 日志，
  **数据逐字段不变**（重写前逐字段校验：backend=cuda / candidate=Quadro
  RTX 5000 驱动 556.18 算力 7.5 16384MiB / cuda_check torch 2.5.1+cu124
  CUDA 12.4 elapsed 0.789s peak 16.1MiB wall 2.696s / reason）；旧文件留档
  `.json.with-locklog`（不入仓）。
- `--output` 实跑验证（真机，持 GPU 锁）：rc=0、json.load 通过、
  backend=cuda Quadro RTX 5000、[gpu_lock] 日志只在 stderr、旧文件未覆盖
  （/tmp/gpu-probe-verify.json 536B 与重写后旧文件同大小，内容各自独立）。
- 单测 +2：--output 纯 JSON 落盘（离线 sample 模式）+ 缺 sample 退出 2，
  test_gpu_probe 10 项全绿。

### 1.6 baselineTeacherVersion 前端 null 核查（无修改，解析无问题）

- 直查（dev DB，series nifti-b63b1d93 有 9 个 MaskVersion v1..v9）：
  `_lookup_baseline_teacher_version` → **(9, '')**——解析到最新 MaskVersion
  版本号，无原因注记。
- 端到端（TestClient + dev DB 快照备份）：写合成修正产物（16 体素，真实
  amos_0004 image/mask affine）→ submit-nni **不带 baselineTeacherVersion**
  → 200，新版本 v10（source=nninteractive_guided，n_corrected_voxels=16），
  provenance.baselineTeacherVersion=**9**（响应与 DB provenance 列读回均 9）。
- 验证后清理：删除验证插入的 v10 行 + mask_v10.nii.gz + 修正产物目录，
  dev 库恢复 10 行、gate_status=passed、alembic=b8e21c4a7f05。
- **结论：`9353f6e` 的自动解析正确工作，无修改**。前端仍送 null 是有意的
  （本页 meta 未带基线版本，不在前端猜；后端查最新 MaskVersion 补上，
  查不到记 null + 原因不猜数字）。

---

## 2. 实测验证表

| 验证 | 方式 | 结果 |
|---|---|---|
| **bbox 提示真实推理** | CPU 48^3 patch（amos_0004 肝区）真跑，无 mock | load 3.7s / infer 18.46s / **n_target_voxels 72**；sidecar provenance 全量 |
| **scribble 提示真实推理** | 同款 CPU patch，9 点轨迹真跑 | load 3.41s / infer 19.43s / **n_target_voxels 2596**；scribble 实测支持 |
| **上游 API 签名** | 读 `.venv-nninteractive/.../inference_session.py` 源码（1350/1430/1516/1536 行） | include_interaction 无默认值（bbox）；scribble 入参是 mask 图 |
| **capability 元数据** | 读模型目录 `inference_session_class.json` + `_legacy_default_capability()` | scribble/bbox2d=True、bbox3d=False |
| **端点 prompts 契约** | TestClient mock correct_image（真实推理见上两行） | 4 种 prompt 混发透传、非法 type/形状/空 points → 422、旧 point 契约兼容 |
| **前端标记** | TestClient GET /review 断言 | `prompts: merged.map` 在、`negative_point` 不在、旧"仅支持点提示"toast 不在 |
| **M0 补列迁移（空库）** | 全新临时库 alembic upgrade head（subprocess） | version=b8e21c4a7f05、source_instances_json 列在 |
| **M0 补列迁移（开发库）** | alembic current/upgrade head（subprocess） | e7f3a1c9d204 → b8e21c4a7f05；列在、5 行 series 保留 |
| **后端端点 200** | curl 实测（localhost + Tailscale） | health/review/meta/labels/seg-meta/image/mask/niivue.js 全 200 |
| **Tailscale 可达** | `curl http://100.72.182.77:8000/health` | 200 |
| **baseline 自动解析直查** | `_lookup_baseline_teacher_version`（dev DB 9 个版本） | **(9, '')** |
| **baseline 自动解析端到端** | TestClient + 合成修正产物 + 不带 baseline 提交 | provenance.baselineTeacherVersion=**9**（响应+DB 读回一致） |
| **gpu_probe --output** | 真机实跑（持 GPU 锁）+ json.load | rc=0、纯 JSON、backend=cuda、旧文件未覆盖 |
| **gpu-probe.json 重写** | 重写前逐字段校验 + json.load 复核 | 数据逐字段不变、只去头两行 [gpu_lock] |
| **ModelVersion 门禁不倒退** | 读库复核（两次：迁移后 + 清理后） | gate_status=**passed** 保持 |

---

## 3. 测试结果

| 项 | 结果 |
|---|---|
| pytest 全套 | 见下方收尾自查（后台运行，总结以 /tmp/pytest-final.log 为准） |
| ruff | **全绿**（src tests scripts eval alembic config） |
| 本轮新增 | test_nninteractive_backend +13（ScribbleToMask 3 + ValidatePromptsScribble 5 + 端点契约 5，另有 TestBboxScribbleRealRun 2 项真实推理）；test_review_api +6（prompts 契约 5 + 前端标记 1）；test_gpu_probe +2 |

---

## 4. 遗留问题（如实）

1. **浏览器驱动实测未做**（本任务只做准备）：真实 niivue 渲染、键盘/点击/
   bbox 拖拽/scribble 笔画在画布上的行为须 live browser 验证（parent 另行
   做）；URL 与 series_id 见 §1.2。前端代码与后端契约已就位，但"审阅屏
   已验证可用"仍不可声称。
2. **bbox2d 单切片限制**（上游事实，非缺陷）：本 checkpoint 不支持 3D bbox
   （bbox3d=False），前端跨切片拖框会被上游 ValueError 拒绝（runner 报
   error）；轴位单切片框可用。
3. **逐像素虚线轮廓未做**（M5-REPORT §3.3 不变）：niivue LUT 无轮廓通道，
   需第二 overlay 通道。
4. **本地 main 领先远端 3+ 提交、未推**（收尾 3 个新提交 + 此前 49 个；
   提交后以 `git rev-list --count origin/main..HEAD` 复核）。push 是显式
   决策。
5. **M4/M5 其余遗留不变**：nnInteractive server 模式、qc-calibrate 数值
   重算（抽检 0/20）、hold-out 两源、s0209 漏检、学生 mask 接生产 QC
   （M5-BACKLOG 不扩大范围）。
6. **dev 库验证痕迹已清理**：task6 端到端验证插入的 v10 行与修正产物已删
   （dev 库恢复 10 行 mask_versions）；alembic 版本 b8e21c4a7f05 为本轮
   新迁移（合法变更，非测试痕迹）。
7. **headless 斜杠命令不可用**：本次为 headless 会话，无斜杠命令可用；
   收尾自查逐项人工核对（见 §5）。

---

## 5. 收尾自查（逐项，headless 无斜杠命令故人工核对）

| 自查项 | 结果 |
|---|---|
| 全套 pytest | 后台运行（见 §3；以 /tmp/pytest-final.log 总结为准） |
| ruff check 全项目 | 全绿 |
| eval/holdout.txt 未动 | `git status` 干净（未列修改） |
| M4 门禁结果未动 | 读库复核 gate_status=passed（两次） |
| dev DB 状态 | 10 行 mask_versions、alembic=b8e21c4a7f05、gate_status=passed |
| 后端进程 | nohup uvicorn 存活（pid 222998），结束时未杀 |
| 未 push / 未建 PR | `git log` 只本地提交；无 gh 调用 |
| 铁律自查 | 数字全部实测（真实推理 2 次 + 端到端 1 次 + 迁移 2 库 + curl 8 端点）；nnInteractive 修正产物未进蒸馏训练集（sidecar distill_training=REJECTED 字段未动）；未声称虚线描边修成 |
