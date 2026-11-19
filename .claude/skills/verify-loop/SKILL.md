---
name: verify-loop
description: Full on-prem verification chain for BodyDonor M7 changes — starts FastAPI on a spare port, imports synthetic NIfTI (DICOM-zip commit stores no pixels — pre-existing, chain uses NIfTI-direct), runs segmentation→QC→review tiering→browser draft→save→MaskVersion→SEG export→voxel-diff-0→DecisionTrace, asserts git status stays clean. Run before `git commit` after fast checks pass. Heavier than pytest; NOT for CI or ad-hoc runs.
---

# verify-loop — 提交前完整链路验证

BodyDonor 院内 RESEARCH-ONLY。改动 M7 系列（verify-loop/审查/校准
相关）后、`git commit` 前，跑一次完整链路：从合成 NIfTI 导入一路
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

设计依据与精确范围见同目录 `DESIGN.md`（Phase B 决定：分段走真实
CPU 路径不 mock，实测 87s；渲染确认 = hdr-status"已加载"+ 4 canvas +
console 无 error；Edge profile 走 wslpath -w 转 Windows 形路径）。

1. 启动 FastAPI（独立端口 8765，**非 8000**——8000 属另一并行轮次的
   uvicorn；scratch DB/data 目录全在仓外 tmp）
2. 合成 NIfTI 导入（zip 上传，modality=CT）→ `POST /ingest/scan` →
   `POST /ingest/{id}/commit`（NIfTI-direct 路径：DICOM zip 的 commit
   不落像素是既有行为，非本轮回归）
3. **真实 CPU 分段**：`POST /segments?device=cpu`（TS 权重在盘，
   实测 87s/117 类；不建 mock——保真即链路价值）
4. QC 评分：`POST /qc/series/{series_id}` → QCReport 落库
5. 分档：`POST /qc/review-tasks`（generate_review_tasks 三档）
6. CDP 打开 `review.html` 并确认渲染：hdr-status=="已加载" + 4 canvas
   非空 + console 无 error（Edge 缺席机器降级 HTTP-only 冒烟，
   输出明标 degraded）
7. POST 保存：`/review/{series_id}/save`（deleted_classes 整类删除）
   → 新 MaskVersion
8. 核对 MaskVersion 落 DB（version 递增：teacher v1 + save v2）
9. 导出 SEG：`export_seg.export_and_verify`（DICOM 来源走真实引用，
   NIfTI-direct 走合成 fallback）
10. `verify_seg` 体素差 = 0（export_and_verify 内含，复核报告 ok）
11. DecisionTrace 两档各 ≥1（`pipeline_stage` 列按 series_id 过滤：
    qc_scoring + gate_decision）
12. git-status-clean 断言（白名单见 DESIGN.md 决定 3）+ 日志无 ERROR

## 脚本（已交付）

| 脚本 | 用法 |
|---|---|
| `scripts/run_chain.sh` | 入口：起 FastAPI(8765) → 跑 12 步 → 日志无 ERROR 断言 → 收尾。`--keep-server` 调试；`--skip-cdp` 仅调试不许正式用 |
| `scripts/chain_app.py` | uvicorn 入口：绑定 scratch settings.yaml（fail-closed，未设 VERIFY_LOOP_TMP 拒启） |
| `scripts/chain_steps.py` | Step 2–12 实现 + 逐步断言；`--steps` 单步跑、`--series-id` 续跑 |
| `scripts/cdp_open_review.py` | Step 6：Edge headless=new + CDP（9333）；`--app --series-id --timeout` |

依赖：主 checkout venv（`/home/gogvf/work/BodyDonor/.venv`）+
`PYTHONPATH=<worktree>/src`（run_chain.sh 已组装）；TS 独立 venv
`~/work/BodyDonor/.venv-totalseg`；websocket-client（CDP）。全部临时
产物在仓外 tmp（DESIGN.md 决定 3），链路结束自动清理。

## 红绿判据

- 12 步任一失败 = 红，不提交
- Step 10 体素差非 0 = 红
- Step 11 trace 两档缺一 = 红
- Step 12 git status 白名单外新文件 = 红（链路泄漏）
- 日志出现 ERROR = 红
- Step 6 CDP 渲染失败 = 红；降级 HTTP 冒烟通过 = 绿（degraded 事实写进输出）
