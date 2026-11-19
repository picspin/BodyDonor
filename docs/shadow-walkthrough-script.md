# 影子走查剧本（Shadow Walkthrough Script）

定位：院内科研 RESEARCH-ONLY 准备文档。影子走查 = 医生按真实流程走一遍
导入 → 分割 → 审阅 → 决策，全程只读观察、不改变产品行为；数据只用合成/公开
脱敏集（AMOS22 公开子集 CC-BY-4.0、TotalSegmentator ct_lite 样例），**绝不碰
真实患者数据**。本文是走查当天的操作剧本；医生人选与时间待用户定（见文末
「待用户定」）。

环境前提（走查当天核对）：

- worktree：`~/work/BodyDonor-shadow`，分支 `feat/m7-shadow-walkthrough-prep`。
- 数据目录 `~/data/BodyDonor/`；DB 用 worktree 根的 `bodydonor.db` 副本
  （schema 已升级到 alembic head `f8a3b9c2d1e4`；生产库
  `~/data/BodyDonor/bodydonor.db` 只读不写）。
- 后端启动（先例 M5-BACKLOG-REPORT.md:53）：`nohup uvicorn bodydonor.app:app
  --host 0.0.0.0 > ~/data/BodyDonor/tmp/walkthrough-uvicorn.log 2>&1 &`，
  健康检查 `GET /health`（src/bodydonor/app.py:131）。
- 测试命令用 venv：`~/work/BodyDonor/.venv/bin/python`，PYTHONPATH 指本
  worktree 的 `src`。

## Case 选择（四个，全部实测核实过存在性与许可）

| # | case | 来源 | 许可 | 为什么选它（覆盖环节/风险） |
|---|------|------|------|------------------------------|
| A | s0662 | `~/data/BodyDonor/downloads/ct_lite/images/s0662.nii.gz` | TS 样例（CC-BY-4.0 派生，见 config/qc_thresholds.yaml cold_start 节） | 常规通过例：hold-out 实测 mean Dice 0.980、最差类 gallbladder 0.927、无 0.0 类（eval/holdout_results.json cases）——验证「没 flag 的真好」 |
| B | amos_0004 | `~/data/BodyDonor/downloads/amos22_subset/train/imagesTr/amos_0004.nii.gz` | AMOS22 公开子集（CC-BY-4.0） | QC 标红/forced 例：worktree DB 已有 pending ReviewTask `rt-e230e4cf`（flags=connected_component/boundary_smoothness/hu_distribution）；教师 prostate Dice 0.0（整类漏检）。前轮实测 rank 1 → forced。走完整 ReviewTask→决策回流 |
| C | amos_0014 | `~/data/BodyDonor/downloads/amos22_subset/train/imagesTr/amos_0014.nii.gz` | 同上 | nnInteractive 修正例：实测弱类——教师 prostate 0.0、duodenum 0.744、urinary_bladder 0.748（eval/holdout_results.json）；学生 prostate 同 0.0（共同盲区，走查当天验证此定性是否复现）。修正一个类 + 提交 |
| D | s0071 | `~/data/BodyDonor/downloads/ct_lite/images/s0071.nii.gz` | 同 A | 边界例：实测 vertebrae_C5 0.0、C6 0.011、T2 0.015（颈椎区整类漏检）——能力边界处教师行为，观察医生如何处置边界例 |

**多模态例：没有。** 本库现可用数据只有 CT（AMOS22 子集 imagesTr 全部
CT、ct_lite 全部 CT）；AMOS22 的 MR 分割不在 `train/imagesTr` 名单内
（40 个文件全部为单模态 CT）。理由如实写明，不为凑数造 multimodal 例。

case 存在性核实方法（走查当天复核一遍即可）：
`ls ~/data/BodyDonor/downloads/ct_lite/images/` 与
`ls ~/data/BodyDonor/downloads/amos22_subset/train/imagesTr/`；
来源 sidecar：`cat ~/data/BodyDonor/downloads/amos22_subset/<case>/source.json`
（source=total，TotalSegmentator 教师推理产物）。

## 完整流程（每例相同骨架，差异在标 ★ 的步骤）

以下端点名/行号均亲读自 src/bodydonor/app.py（HEAD 421ef0c）。

### 第 0 步：准备（一次性）

1. 启动后端（命令见上），`curl http://127.0.0.1:8000/health` 返回 ok。
2. `bodydonor status` 确认 deployment_mode=on_prem、data_dir、storage backend。
3. 开录屏（方案见 docs/shadow-walkthrough-recording.md）。

### 第 1 步：导入（ingest）

- CLI 通道（本地盘）：`bodydonor import <nii 路径> --modality CT`
  （src/bodydonor/cli.py:42；NIfTI 必须显式模态，cli.py:48 注释）。
- 本轮四例已实测 dry-run：s0662=419 层、s0071/amos_0014/amos_0004 均轴位
  选中（走查当天以实际输出为准）。
- 记下输出的 Series ID（形如 `nifti-xxxxxxxx`）。

### 第 2 步：teacher 分割（看 teacher mask）

- `bodydonor segment <series_id>`（cli.py:382；GPU 锁互斥，四例**串行**跑，
  不并行）。
- 或 API 等价：`POST /segments?series_id=...`（app.py:683）。
- 产物：多类 mask + labels.json 落盘 + MaskVersion 行（source=teacher）。

### 第 3 步：QC 与 ReviewTask 生成

1. 每例 `POST /qc/series/{series_id}`（app.py:475）产 QCReport + qc_scoring
   DecisionTrace（同一事务，qc_pipeline.py:191）。
2. 全部例跑完后 `POST /qc/review-tasks`（app.py:497）按 gating 三档生成
   ReviewTask（forced/suggested 建任务，auto 不建；app.py:497 docstring）。
3. 核对：`GET /qc/review-tasks`（app.py:515）列出 pending 任务。
   ★ 预期 amos_0004 进 forced（前轮 rank 1 实测）——以当天实际输出为准，
   不预设结论。

### 第 4 步：审阅页看 mask（每例）

- 浏览器打开 `http://127.0.0.1:8000/review?series_id=<series_id>`
  （app.py:661 `GET /review`，review.html:255-256 读 `?series_id=`）。
- 页面数据源（review.html 实测 fetch）：`GET /review/{series_id}/meta`
  （app.py:725）、`/image.nii.gz`（:781）、`/mask.nii.gz`（:801）、
  `/labels.json`（:850）、`/seg-meta`（:874）。
- 医生逐类开关核对 teacher mask；niivue 三正交面 + MIP（app.py:661 docstring）。

### 第 5 步：nnInteractive 修正一个类 ★ 仅 Case C（amos_0014）

1. 在审阅页对 prostate 做点/框提示（页面 `POST
   /review/{series_id}/nninteractive-correct`，app.py:1114；GPU 忙 → 409）。
2. 确认后 `POST /review/{series_id}/submit-nni`（app.py:1480）→ 新
   MaskVersion（source=nninteractive_guided，sidecar
   `nninteractive-correction/correction_source.json`；**不进商用蒸馏训练集**）。
- CLI 等价（备用，长推理时用）：`bodydonor nninteractive-correct <image>
  --point x,y,z ...`（cli.py:436）。

### 第 6 步：决策回流（每例）

- 审阅屏判定后逐例调 `POST /qc/review-tasks/{review_task_id}/decision
  ?decision=accepted|corrected|rejected&notes=...`（app.py:563）。
- 端点行为（app.py:563 docstring + 实现亲读）：写 ReviewTask.status/decision，
  同一事务补填该 review_task_id 下**全部** gate_decision DecisionTrace 的
  human_outcome（已补填不覆盖，幂等）；并累计 qc_thresholds.yaml
  calibrated_on（qc_reflux.py record_review_decision）。
- Case B 走 forced 任务决策；Case A/D 若 auto 未建任务，用 CLI
  `bodydonor qc-calibrate` 之外的口径如实记录「auto 档无决策端点路径」，
  医生判定写入走查记录（本轮如实呈现 gating 语义，不绕过产品行为）。

### 第 7 步：收尾核对

- 跑 docs/shadow-walkthrough-checklist.md 全部打勾项。
- 停录屏；归档录屏与会话索引（recording.md 命名规则）。

## 待用户定（与终报未决清单一致）

1. **医生人选**：须用户提供（本剧本不编造）。走查全程医生在场操作审阅屏；
   实施方在场做导入/分割/端点调用与记录。
2. **时间**：须用户提供；四例串行 GPU 分割 + 逐类审阅，预估半天（以实际为准）。
3. 录屏文件最终归档位置（方案暂定 `~/data/BodyDonor/walkthrough-recordings/`，
   见 recording.md；如院内另有规定以规定为准）。
4. 决策端点测试账号/签名要求（如院内要求操作留痕，须用户确认形态）。
