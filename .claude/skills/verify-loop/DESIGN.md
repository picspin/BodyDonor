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

## 未验证 / 开放问题（Phase B 前须拍板）

1. **mock 分段路径缺席**：`/segments` 只走真实 `segment_series`
   （CPU 也需 TS 权重 + 分钟级）。Phase B 若走 mock 须自建（如在
   链脚本内 monkeypatch 或独立 mock 端点）——本探针不悄悄绕过，
   留 Phase B 决定。
2. **review.html 渲染确认深度**：niivue 需加载 `/static/
   niivue.umd.js` + NIfTI 端点出图；CDP "确认"的最低标准（页面
   200 + 无 console error？三视口 canvas 非空？）Phase B 定。
3. **Windows 侧 msedge.exe 路径可移植性**：先例脚本写死 Windows
   exe 路径（`/mnt/c/...`）；若本轮机器无 Edge，Phase B 需备选
   （headless chromium 下载或降级为 HTTP-only 冒烟）。
4. **DecisionTrace 两行的精确定义**：链路中 qc_scoring 一行 +
   gate_decision 一行已确认写入点；但 verify-loop 断言"两行"时是
   按 `trace_kind` 过滤还是计数整表，Phase B 按 M6-e 语义
   （同一 series 两条 kind 各 ≥1）定。
