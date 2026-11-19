# verify-loop skill — DESIGN

Phase A 探针结论 + 四项设计决定。探针基线 `origin/main 421ef0c`，
链路触点均已核到 file:line（见文末探针表）。

## 决定 1：触发时机 — 仅 pre-commit

verify-loop 只在 `git commit` 之前由开发者/会话运行，且在快速检查
（ruff + 单测）通过之后。

明确不是：
- **不是 CI-every-run**：链路含 GPU 分段 + 浏览器驱动，分钟级耗时，
  每次 CI 跑会把 PR 周期拖到不可用。CI 仍只跑 pytest 套件。
- **不是 ad-hoc dev-run**：不提供任意时点手动改库后跑链的语义——
  改库走正常 pytest；ad-hoc 验证用现有 `scripts/cpu_smoke_verify.sh`。
- **比 pytest 重**：pytest 是秒~分钟级纯断言；本链起真实服务、写真实
  （临时）数据库、驱动浏览器。两者不互相替代。

谁在何时跑：准备提交 M7 系列改动的会话/开发者，在 `pytest` 绿之后、
`git commit` 之前，跑一次 `verify-loop`。红则不提交。

## 决定 2：完整链路 12 步（顺序固定，缺一即不完整）

1. 启动 FastAPI（独立端口，非 8000——8000 属另一并行轮次的
   uvicorn，PID 237991 不可动）
2. 合成 DICOM 导入（`/ingest/scan` → `/ingest/{id}/commit`，合成
   数据用 `tests/factories.py make_dicom_folder` 造，绝不碰真实
   `~/data/BodyDonor`）
3. TS 分段或 mock（`/segments`，device=cpu；真实分段太慢则用
   mock 路径——Phase B 拍板，探针未见现成 mock 端点）
4. QC 评分（`/qc/series/{series_id}` → QCReport 落库）
5. `generate_review_tasks` 分档（`/qc/review-tasks` POST，三档
   forced/suggested/auto）
6. CDP 打开 `review.html` 草稿并确认渲染（`GET /review` → 静态页
   `src/bodydonor/static/review.html`，niivue 三正交面）
7. POST 保存（`/review/{series_id}/save`，人工修正写回 → 新
   MaskVersion）
8. 核对 MaskVersion 已落 DB（version 递增一行）
9. 导出 SEG（`export_seg.export_and_verify`）
10. `verify_seg` 体素差 = 0（`export_seg.verify_seg`）
11. DecisionTrace 两行在（qc_scoring 一行 + gate_decision 一行，
    `qc_trace.record_qc_trace` / `qc_pipeline` 同事务写入）
12. 日志无 ERROR（FastAPI/uvicorn stderr + 应用日志 grep）

顺序依据：每步消费上一步产物（segment→QCReport→ReviewTask→
保存→新 MaskVersion→SEG→verify→trace），不可重排。

## 决定 3：git-status-clean 断言范围（precise）

- **断言步骤**：在第 12 步（链路末尾）跑，且仅跑一次；第 11 步与
  第 12 步之间无写盘操作，无窗口期问题。
- **免检路径（exempt，白名单）**：
  - `bodydonor.db`（及 `-wal`/`-shm`）——仓库根运行时产物，
    gitignored（`.gitignore:13`）
  - `eval/*.json`——本地评测产物，目录级 gitignored
    （`eval/.gitignore:3`）
  - `tests/data/`——测试资产，白名单例外进仓
  - `logs/`、`__pycache__/`、`*.py[cod]`——运行时产物，gitignored
  - 链路自身的临时目录：FastAPI 进程的 scratch（合成 DICOM、SEG
    导出、mock mask）一律建在 `tempfile.mkdtemp()` 下，不在仓内，
    天然不进 git status。
- **计脏规则**：git status --porcelain 非空即脏，含 `??` 未跟踪。
  测试生成的 temp fixture 因建在仓外 tmp 不计脏；链路如发现仓内
  出现新文件，即视为链路泄漏（bug），判红。
- **探针后仍开放的问题**：工作树里除上述白名单外是否会有 Phase B
  引入的合法新文件（如链路 state 文件），Phase B 落地时若需要须先
  进白名单并说明——本轮不预先发明该条目。

## 决定 4：CI 内假设检验需固定种子（Hypothesis 固定种子）

记录为设计决定：将来 CI 中用 Hypothesis 做性质测试（不变量）时，
必须 `@settings(derandomize=True, database=None)` 或显式
`@seed(...)`——CI 不接受随机种子导致的偶发红。

本轮范围边界：**本轮只建 skill 骨架 + 链路（Phase A），不写四条
Hypothesis 不变量**——属后续工作，届时须遵守本决定。原因：骨架先
定触发与链路语义，不变量依赖 Phase B 链路真实产出（Step 8
MaskVersion、Step 11 trace 行）的稳定形态，先跑通再定性。

## 探针表（链路触点 file:line）

| 链路步 | 触点 |
|---|---|
| FastAPI app | `src/bodydonor/app.py`（单文件 app，非 router 拆分） |
| ingest scan/commit | `app.py:260` `/ingest/scan`，`app.py:294` `/ingest/{id}/commit` |
| TS 分段 | `app.py:683` `/segments`（同步，`segmentation.segment_series`） |
| QC 评分 | `app.py:475` `/qc/series/{series_id}`（QCReport 落库） |
| review-tasks 分档 | `app.py:497` POST `/qc/review-tasks` |
| review.html | `app.py:661` `GET /review` → `src/bodydonor/static/review.html`（1053 行）；静态 `app.py:669` `/static/{filename}` |
| POST save | `app.py:983` `/review/{series_id}/save`（写回新 MaskVersion `app.py:1091`） |
| MaskVersion 落库 | `models.py` `class MaskVersion`；save 端点 `app.py:1044-1091` |
| SEG export | `src/bodydonor/export_seg.py:455` `export_and_verify`（内含 `:332` `verify_seg`） |
| verify_seg | `export_seg.py:332` |
| DecisionTrace 模型 | `models.py:208` `class DecisionTrace` |
| 两行写入 | `qc_pipeline.py:171`（qc_scoring 同事务）、gate_decision 在 `generate_review_tasks` 路径（`qc_pipeline.py:504` 前置排名） |
| 合成 DICOM | `tests/factories.py:44` `make_dicom_dataset` / `:101` `save_dicom` / `:107` `make_dicom_folder` |
| CDP 先例 | `scripts/bench_review_combined.py`（Edge headless + CDP websocket，端口 9333 惯例） |

## 探针结论（Phase B 依赖项现状）

- **CDP 工具**：venv 无 `playwright`；有 `websocket-client`
  （`websocket` import ok）。系统无 chromium/chrome（WSL2），先例
  `bench_review_combined.py` 用 **Windows 侧 msedge.exe + CDP
  websocket**（端口 9333 惯例）。Phase B 沿用该先例，不引
  playwright。
- **skill frontmatter 惯例**：仓内无 `.claude/skills/`（本目录为首
  建）。本机 `~/.claude/skills/` 全部为 symlink 到 magpie library，
  frontmatter 标准 `name` + `description`（多行 `>` 或单行均可）。
  本 skill 采用标准 Claude Code frontmatter：`name`、`description`。
- **scripts/ 惯例**：argparse + `#!/usr/bin/env python` + 模块
  docstring 记录用法与验收断言（例 `scripts/replay_m6a_metrics.py`）。
  Phase B 链条脚本照此风格。
- **测试**：`tests/conftest.py` 极薄（settings + tmp_data_dir）；
  `local_artifacts` 标记（`pyproject.toml:62`）= 依赖本机
  `~/data/BodyDonor` 或 `eval/*.json`，CI 跳过。全套运行：
  `PYTHONPATH=<worktree>/src .venv/bin/python -m pytest tests/`。
- **gitignore**：`bodydonor.db`、`eval/*`（目录级）、`tests/data/`
  白名单例外、`logs/`、`data/`。

## Phase B 决定（4 项开放问题逐项拍板）

1. **分段走真实 CPU 路径，不建 mock**。探针实测（2026-11-20）：TS 权重
   在盘（`~/.totalsegmentator/.../checkpoint_final.pth` 157.3MB，与注册表
   weight_integrity sha256 一致），独立 venv `~/work/BodyDonor/.venv-totalseg`
   存在，合成 NIfTI（64×64×40）真实 CPU 分段 **87s 完成、117 类、exit 0**
   （`ts_runner.run_task` 直跑）。链路定位本就"比 pytest 重"（决定 1），
   90s 可接受。**保真度损失：无**——真实 `segment_series` 全路径
   （白名单→GPU 锁→runner→merge→MaskVersion）。不建 mock 端点、不做
   monkeypatch：链脚本不引测试桩，保真即链路价值。测试（tests/）仍走
   mock（沿用 `test_segmentation.py` TestSegmentApi 的既有模式），
   两层分工与仓内现状一致。

2. **review.html 渲染确认 = 页面 200 + `hdr-status`=="已加载" +
   4 canvas 非空 + console 无 error**。依据：页面自身在 loadVolumes
   成功后才 `setHeader("ok","已加载")`（review.html:376）——这是应用
   自带的"niivue 加载 + NIfTI 端点 + mask 端点全通"就绪信号，先例
   `bench_review_combined.py` 同口径。4 canvas = axial/coronal/sagittal/mip
   （review.html 各 setSliceType 后 canvas 必渲染）。console error 经
   CDP `Runtime.consoleAPICalled`（type=error）采集，出现即红。不额外
   做像素级断言（canvas 位图比对脆弱且无判别力增量）。

3. **Edge 在本机存在**（`/mnt/c/Program Files (x86)/Microsoft/Edge/
   Application/msedge.exe` 实测），链路用 Edge headless + CDP（先例
   同法，`Browser.close` 收尾不用 taskkill——不误杀用户会话）。**Edge
   缺席机器的 fallback = HTTP-only 冒烟**：GET /review、
   /static/niivue.umd.js、image.nii.gz、mask.nii.gz 全 200 且非空。
   诚实边界：HTTP-only **不验证** JS 执行、niivue WebGL 渲染、
   console error、前端 fetch 流程——输出明标 `degraded` 字样，链路
   仍绿（后端契约已验证，浏览器渲染属环境依赖），但 degrade 事实
   写进链路输出不隐瞒。

4. **DecisionTrace "两行" = `pipeline_stage` 列（非 trace_kind）**。
   骨架用词纠正：`models.py:208` DecisionTrace 无 trace_kind 列，
   阶段列为 `pipeline_stage`（'qc_scoring' | 'gate_decision'，索引
   `ix_decision_traces_stage`）。M6-e 语义断言：同 series_id 下
   `SELECT pipeline_stage, COUNT(*) ... GROUP BY pipeline_stage` 两档
   **各 ≥1**（gate_decision 行在 priority 路径含 auto 档，全量每 MV
   一条，qc_pipeline.py:454-505）。断言按 stage 过滤、不计数整表
   （整表含其他 series/重放的行）。

## 未验证 / 开放问题（Phase B 前须拍板）
