---
name: verify-loop
description: Full on-prem verification chain for BodyDonor M7 changes — starts FastAPI on a spare port, imports synthetic DICOM, runs segmentation→QC→review tiering→browser draft→save→MaskVersion→SEG export→voxel-diff-0→DecisionTrace, asserts git status stays clean. Run before `git commit` after fast checks pass. Heavier than pytest; NOT for CI or ad-hoc runs.
---

# verify-loop — 提交前完整链路验证

BodyDonor 院内 RESEARCH-ONLY。改动 M7 系列（verify-loop/审查/校准
相关）后、`git commit` 前，跑一次完整链路：从合成 DICOM 导入一路
到 SEG 导出体素差 = 0，确认端到端不回归。

## 触发条件

- `pytest` 全绿之后、`git commit` 之前
- 只覆盖 M7 链路相关改动（app 端点、qc、review、export、trace）
- **不是** CI-every-run（链路分钟级，CI 只跑 pytest）
- **不是** ad-hoc 开发中改库后的临时验证（那走 pytest /
  `scripts/cpu_smoke_verify.sh`）

## 用法

```
<skill 触发后>：
1. 先跑 fast checks：ruff + pytest（已有流程，本 skill 不重复）
2. 跑链路脚本（Phase B 交付，路径见下）
3. 链路红 → 不提交，先修
```

## 链路 12 步（顺序固定，缺一即不完整）

设计依据与精确范围见同目录 `DESIGN.md`。

1. 启动 FastAPI（独立端口，**非 8000**——8000 属另一并行轮次的
   uvicorn）
2. 合成 DICOM 导入：`tests/factories.py make_dicom_folder` 造数据 →
   `POST /ingest/scan` → `POST /ingest/{id}/commit`
3. TS 分段或 mock：`POST /segments`（device=cpu；mock 形态 Phase B
   拍板）
4. QC 评分：`POST /qc/series/{series_id}` → QCReport 落库
5. 分档：`POST /qc/review-tasks`（generate_review_tasks 三档）
6. CDP 打开 `review.html` 并确认渲染（`GET /review`）
7. POST 保存：`/review/{series_id}/save` → 新 MaskVersion
8. 核对 MaskVersion 落 DB（version 递增）
9. 导出 SEG：`export_seg.export_and_verify`
10. `verify_seg` 体素差 = 0
11. DecisionTrace 两行在（qc_scoring + gate_decision）
12. git-status-clean 断言 + 日志无 ERROR（范围见 DESIGN.md 决定 3）

## 脚本（Phase B 交付，当前不存在 — TODO Phase B）

| 脚本（意图路径） | 状态 |
|---|---|
| `.claude/skills/verify-loop/scripts/run_chain.sh` | TODO Phase B |
| `.claude/skills/verify-loop/scripts/chain_steps.py` | TODO Phase B |
| `.claude/skills/verify-loop/scripts/cdp_open_review.py` | TODO Phase B |

Phase B 交付前本 skill 不可运行。脚本按仓内 scripts/ 惯例写
（argparse + 模块 docstring 记用法与断言；CDP 先例
`scripts/bench_review_combined.py`：Edge headless + websocket，
端口 9333 惯例）。

## 红绿判据

- 12 步任一失败 = 红，不提交
- Step 10 体素差非 0 = 红
- Step 11 trace 行缺失 = 红
- Step 12 git status 非白名单内出现新文件 = 红（链路泄漏）
- 日志出现 ERROR = 红
