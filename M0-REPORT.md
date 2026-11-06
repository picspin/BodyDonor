# M0-REPORT — BodyDonor M0 里程碑交付报告

> 日期：2026-11-06。依据：proposal.md（v2，R1–R12 + Q1–Q10 拍板）§11 启动指令 v2 与 §10 M0。
> 只做 M0，不做 M1。非交互运行，每步本地测试通过后继续。

---

## 1. 做了什么

### 1.1 仓库

- 本地 `git init`（main 分支）。**未创建 GitHub 远程仓库、未 push**（proposal §12 第 1 条：远程 picsum/BodyDonor 待用户确认）。
- `.gitignore` 排除 `.venv/`、`bodydonor.db`、`__pycache__`；`uv.lock` 进仓（版本 pin，Q7）。

### 1.2 第三方许可证核查（先于推理代码，proposal §12 第 2 条）

6 项 checklist 全部经网络读取官方原文核实，产出 `LICENSES-THIRD-PARTY.md`
（逐条 verbatim 引用 + URL + 访问日期 2026-11-06）。核心结论：

| # | 判定项 | 结论 |
|---|---|---|
| 1 | TS `total` 基础任务权重 | **Apache-2.0，"Openly available for any usage"（含商用）**——README 原文 |
| 2 | 高级任务授权边界 | 18 个任务需 license（非商业免费，`commercial_models` 字典逐字核实）；`brain_aneurysm` CC BY-NC 4.0 且**无商业授权渠道 → 禁用** |
| 3 | 商业授权持有主体 | University Hospital Basel（作者方），jakob.wasserthal@usb.ch；非商业免费授权走 backend.totalsegmentator.com/license-academic/ |
| 4 | nnInteractive | 代码 Apache-2.0；**权重 CC BY-NC-SA 4.0**（README License 节 + HF tag 双重核实）——产物不进商用蒸馏训练集 |
| 5 | nnU-Net v2 | Apache-2.0；蒸馏传染分析：**安全路径 = 教师只用 `total` 任务原始 mask + 纯人工修正** |
| 6 | SegVol / SAT | SegVol 代码 MIT；`BAAI/SegVol` HF 权重**无 license 标签 → 待核实**（实验通道本就不进训练集，风险被流程隔离）；SAT 代码 Apache-2.0 |

**落地物**：

- `config/settings.yaml`：`teacher.allowed_tasks`（31 个 open 任务）、`commercial_distill_tasks`（仅 `total`）、
  `blocked_tasks`（`brain_aneurysm`）。
- CI 白名单负测试（`tests/test_license_whitelist.py`，9 条）+ CLI 负测试（`bodydonor license check` 退出码）。
- 顺带核实 QC 数据集：TS dataset v1/v2、AMOS22 均 Zenodo CC-BY-4.0（M1 下载登记时履行署名义务）。

### 1.3 环境（Q7）

- **独立 uv venv + lock**：`.venv`（Python 3.12.16）+ `uv.lock`（37 包）。
- **逐个验证 import 链**：fastapi 0.162.0、uvicorn 0.54.0、pydantic 2.14.3、pyyaml 6.0.3、typer 0.28.0、
  rich 15.0.0、pydicom 3.0.2、SimpleITK 2.5.7、nibabel 5.4.2、httpx 0.28.1、pytest 9.2.0 + 本仓 7 个模块——全部通过。
- 不共享环境：TotalSegmentator / nnInteractive / nnU-Net v2 的依赖冲突隔离在 M2/M4 各自 venv（Q7 方案），M0 未安装。

### 1.4 后端骨架

- **FastAPI**（`src/bodydonor/app.py`）：`/health`、`/status`、`/license`、`/limits`、`/queue/enqueue`、`/queue/size`、
  `/agent-context/preview`。lifespan 初始化 DB。已实测启动 + 请求。
- **SQLite（默认）**（`src/bodydonor/db.py`）：cases / series / jobs 三表；仅接受 `sqlite:///`（PG 后置）。
- **接口抽象**（`src/bodydonor/storage.py`）：`StorageBackend`（默认 `LocalFsStorage`，含路径穿越拒绝）、
  `QueueBackend`（默认 `InProcessQueue`，线程安全，ack/nack/requeue）——MinIO/Redis/PG 为后置可替换 profile。
- **docker-compose 只含 PG**（可选 `--profile pg`），默认组合不需要任何容器。

### 1.5 CLI 命令树（Typer + rich.table，proposal §4.4）

- `bodydonor import <path> [-r] [--dry-run]`：扫描 NIfTI/DICOM 序列，rich.table 导入自检报告（M0 只扫描，落库在 M1）。
- `bodydonor license status`：许可核查状态表（与 LICENSES-THIRD-PARTY.md 同步）。
- `bodydonor license check <task>`：单任务白名单判定，exit 0/1（CI 用）。
- `bodydonor status`：部署形态、限制、队列/存储后端、白名单、逃生通道开关。

### 1.6 CI 最小集（`.github/workflows/ci.yml`）

- `lint` job：ruff（已本地通过，All checks passed）。
- `test` job：`uv sync --locked` + pytest 34 条 + CLI 白名单负测试（exit code 断言）。
- **GPU 推理不进 CI**（proposal §11）。
- CI 就绪但**不可验证**：远程仓库未建，workflow 无处运行；本地等价命令已全部实测通过。

### 1.7 配置化限制（proposal §4.3 / §12 第 6 条）

- `deployment_mode: cloud_demo | on_prem`（`config/settings.yaml`，代码不分叉）。
- `max_upload_mb`：仅 cloud_demo 生效（100），on_prem 为 null——有测试锁定（`test_cloud_demo_upload_limit`）。
- 解压保护（M1 ingest 用）：压缩比 100×、嵌套 3 层、文件数 10k。

### 1.8 AgentContext（R2 落地）

`src/bodydonor/agent_context.py`：白名单字段构造器（case/series ID、技术元数据、QC 分数、任务结构、映射表），
bytes/bytearray 在类型上被拒收，序列化出口跑 PHI 关键词 + base64/二进制自检，命中即 raise。CI 有 9 条负测试。

## 2. 许可证核查结论（摘要，全文见 LICENSES-THIRD-PARTY.md）

1. **商业安全路径只有一条**：教师用 TotalSegmentator `total` 任务（Apache-2.0 权重）蒸馏，学生可商用。
2. nnInteractive 权重 CC BY-NC-SA 4.0：仅交互式标注辅助；**其修正产物不进商用蒸馏训练集**（ShareAlike 传染）。
3. TS 18 个 licensed 任务：非商业免费；**商用蒸馏传染性无书面结论前，其伪标签不进商用训练集**（推定仅科研）。
4. `brain_aneurysm` 禁用（无商业授权渠道，连付费路径都不存在）。
5. **待核实**（已标注，未编造）：`BAAI/SegVol` HF 权重许可标签缺失；TS licensed 任务 custom license 全文（申请时补录）。

## 3. 测试结果

```
$ uv run pytest
34 passed (0.4s)
  - 许可白名单负测试 9 条（brain_aneurysm 一票否决、licensed 默认拒绝、fail-closed）
  - AgentContext PHI 负测试 9 条（字段拒收、PHI 关键词、base64/二进制）
  - Storage/Queue/Config 15 条（roundtrip、路径穿越拒绝、mnt/c 拒绝、cloud_demo 限制）
  - FastAPI 骨架 6 条

$ uv run ruff check src tests
All checks passed!

CLI 负测试（CI 等价）：
  bodydonor license check total                → exit 0（允许）
  bodydonor license check brain_aneurysm       → exit 1（禁用）
  bodydonor license check heartchambers_highres→ exit 1（licensed 未加白）

API 实测：uvicorn 启动，/health /status /limits /queue/* /agent-context/preview 全部 200。
sqlite bodydonor.db 落盘，~/data/BodyDonor/storage/ 创建。
```

## 4. 显存与耗时基线（M0，proposal §12 第 8 条）

| 项 | 值 |
|---|---|
| GPU | Quadro RTX 5000 16GB（WSL2 透传，driver 556.18） |
| 空闲显存 | 798 / 16384 MiB |
| libcuda | 可加载（驱动就绪） |
| torch | **未安装**（M0 无 GPU 依赖；TotalSegmentator/torch 在 M2 各自 venv） |
| pytest 全套 | 0.82s（34 条） |
| API /health | ~12 ms/req（TestClient） |
| GPU 推理基线 | **M2 才有**（TotalSegmentator worker 交付时汇报首个 `total` 任务显存/耗时） |

## 5. 遗留问题（按优先级）

1. **GitHub 远程仓库**：未建未推（待用户确认 picsum/BodyDonor）。CI workflow 就绪，push 后即生效。
2. **BSL 1.1 LICENSE 文件**：proposal §14 待定项——Additional Use Grant 需法务把关，M0 未放 LICENSE 文件
   （pyproject 声明 `BSL-1.1` 占位）。产出前建议加占位说明文件。
3. **BAAI/SegVol 权重许可待核实**：逃生通道启用前向 BAAI 确认（M5 前完成即可）。
4. **TS licensed 任务 custom license 全文**：申请 license 时补录 LICENSES-THIRD-PARTY.md。
5. **zip 炸弹三 case 负测试**：M1 ingest 落地时补（`extract_limits` 判定值已在 config 就位，CI 里已留注释位）。
6. **StudentModel/蒸馏传染书面结论**：M4 准入项——本报告 §2.1–2.3 是工程侧保守判定，商用前需法务复核。
7. **uv.lock 与 CI**:锁文件已生成进仓；首次 push 后跑 CI 验证（本地已用 `--locked` 等价验证）。

## 6. 交付物清单

```
LICENSES-THIRD-PARTY.md     许可核查（6 项 checklist，verbatim 引用 + URL + 访问日期）
config/settings.yaml        配置化限制 + teacher.allowed_tasks 白名单
src/bodydonor/app.py        FastAPI 骨架
src/bodydonor/config.py     Settings 强类型配置
src/bodydonor/storage.py    StorageBackend / QueueBackend 抽象 + 默认实现
src/bodydonor/agent_context.py  AgentContext 白名单构造器 + PHI 自检
src/bodydonor/license_registry.py  许可注册表
src/bodydonor/db.py         SQLite（默认）
src/bodydonor/cli.py        CLI 命令树（import / license / status）
tests/                      34 条（白名单负测试、PHI 负测试、storage、API）
.github/workflows/ci.yml    CI 最小集
docker-compose.yml          仅 PG（可选 profile）
pyproject.toml + uv.lock    版本 pin
M0-REPORT.md                本文件
```
