# 影子走查录屏方案（Shadow Walkthrough Recording Plan）

定位：走查当天的录屏与归档方案；与剧本
（docs/shadow-walkthrough-script.md）配套。医生人选与时间待用户定（剧本文末）。

## 录什么（四个界面，对应剧本第 1–6 步）

| 界面 | 录制内容 | 对应剧本步骤 |
|------|----------|--------------|
| 终端 | `bodydonor import` / `bodydonor segment` 输出（Series ID、层数、耗时） | 第 1–2 步 |
| 审阅屏（浏览器 `http://127.0.0.1:8000/review?series_id=<series_id>`） | niivue 三正交面 + MIP + mask 逐类开关；nnInteractive 提示与提交 | 第 4–5 步 |
| 终端/HTTP | `POST /qc/series/...`、`POST /qc/review-tasks`、`GET /qc/review-tasks` 输出 | 第 3 步 |
| 终端/HTTP | `POST /qc/review-tasks/{id}/decision?decision=...&notes=...` 调用与响应 | 第 6 步 |

每例一段（四例 = 四段），段内不剪辑；串行跑，不并行录。

## 录多久

- 每例一段：导入+分割（GPU 分割分钟级，全程录）+ 审阅 + 决策，单段预估
  30–60 分钟；四例合计预估半天。以当天实际为准，不预设上限。
- 审阅屏页面加载前轮实测约 160–180s（M5-FIX-VOX-REPORT §4：run4-* 五轮），
  录屏从打开页面开始，包含加载等待，不剪掉。

## 谁在场

- 医生：操作审阅屏（看 mask、nnInteractive 提示、判定 accepted/corrected/
  rejected）。人选待用户定。
- 实施方（Claude Code/headless 当天由用户或指定人执行）：跑导入/分割/端点
  调用、核对 DecisionTrace 落库、记走查记录。
- 其余人员不进场（院内科研 RESEARCH-ONLY，数据为合成/公开脱敏集）。

## 录屏文件存放与命名

- 存放：`~/data/BodyDonor/walkthrough-recordings/`（data_dir 下，与 storage/
  eval 产物同级；**不进 git 仓库**——像素/录屏属数据目录，仓库只留文档）。
- 命名（沿用 M5 browser-test 会话惯例 `run4-*` + 本轮前缀）：
  `wt-<case_id>-<step>-<YYYYMMDD-HHMM>.<ext>`，例：
  - `wt-s0662-import-20261120-1400.mkv`
  - `wt-amos_0004-review-20261120-1430.mkv`
  - `wt-amos_0014-nni-20261120-1500.mkv`
  - `wt-s0071-decision-20261120-1600.mkv`
- 每例一个会话索引 JSON：`wt-<case_id>-session.json`（对齐 M5 run4-*/
  results.json 惯例），字段见下「与 DecisionTrace 对应」。

## 录屏与 DecisionTrace 的对应（具体字段，不是原则）

每例录屏起止时间写入会话索引 `wt-<case_id>-session.json`，键与代码字段
一一对应（字段名亲读自 src/bodydonor/models.py:208 DecisionTrace 定义）：

| 索引键 | 来源（代码字段） | 说明 |
|--------|------------------|------|
| `case_id` | `DecisionTrace.case_id`（models.py，`series.case_id`） | 与录屏文件名一致 |
| `series_id` | `DecisionTrace.series_id` | 导入输出（`nifti-xxxxxxxx`） |
| `mask_version_id` | `DecisionTrace.mask_version_id` | QC 时记录 |
| `qc_report_id` | `DecisionTrace.input_refs.qc_report_id` | qc_scoring trace 里 |
| `review_task_id` | `DecisionTrace.review_task_id`（仅 forced/suggested） | gate_decision trace 里 |
| `recording_file` | 录屏文件名 | 上表命名 |
| `recording_start` / `recording_end` | 录屏起止（**UTC**，秒级，格式
  `%Y-%m-%dT%H:%M:%SZ`） | 对应 `DecisionTrace.timestamp` / `human_outcome.decided_at`（qc_trace.py:251 写 UTC，本地时间须换算） |
| `decided_at` | `DecisionTrace.human_outcome.decided_at`（qc_trace.py:234 update_human_outcome 写入，格式 `%Y-%m-%dT%H:%M:%SZ`） | 决策回流端点响应里可查 |

对应关系核对（走查当天照做）：

1. 每例录屏开始前记 `recording_start`；录屏内操作与 trace 时间戳比对：
   `human_outcome.decided_at` 应落在 `[recording_start, recording_end]`
   窗口内。
2. `case_id` 与录屏文件名一致；`series_id` 两处一致：会话索引与
   `decision_traces.series_id`（录屏文件名只含 case_id，查 SQL 见
   checklist.md）。
3. Case C 的 submit-nni 产物 `correction_source.json` 的
   `provenance.sessionId`（app.py:1114 docstring）也写进会话索引——
   录屏内提交动作 ↔ sidecar ↔ 新 MaskVersion 三者可对上。
4. 走查结束后跑 checklist.md 全部 SQL，把查询输出粘进走查记录（不凭印象）。
