# M7-SHADOW-WALKTHROUGH-PREP-REPORT

日期：2026-11-20（第 2 轮补齐）。上一轮实施方写完三文档并提交 3 个 commit
后死亡（Request timed out），未做全量测试、未做审查、未写轮次报告；本轮只
做补齐，不重写三文档。

## 1. 分支 / HEAD / 提交列表

- 分支：`feat/m7-shadow-walkthrough-prep`（worktree `~/work/BodyDonor-shadow`）
- 基线：`origin/main 421ef0c`；未 push、未 merge、未开 PR（按派遣要求）
- 上一轮 3 个提交（保留未动）：
  - `89b9652` docs(m7-shadow): 影子走查剧本——4 case 实测核实+端点行号亲读+待用户定
  - `a4a2aa7` docs(m7-shadow): 录屏方案——界面/时长/命名+DecisionTrace 字段对应表
  - `56acc59` docs(m7-shadow): DecisionTrace 落库检查清单——C1-C4 逐项可核对+SQL 实测跑通
- 本轮新增 2 个提交：
  - `3e6bdb0` docs(m7-shadow): 核实修正——s0071 层数 306→278、C2 日志位置、cli.py:48、BACKLOG:53
  - `87074c4` docs(m7-shadow): 审查修复——时区 UTC、auto 档不适用、uvicorn PYTHONPATH、config 还原等 9 项
- diffstat（实测 `git diff --stat 421ef0c..87074c4`，见 §5）

## 2. 第 1 节核实（逐项方法与结果）

| 断言 | 实测方法 | 结果 |
|------|----------|------|
| 4 case 存在性 | `ls` ct_lite/images 与 amos22_subset/train/imagesTr | ✅ s0662/s0071/amos_0004/amos_0014 均在 |
| rt-e230e4cf | sqlite mode=ro 查 review_tasks | ✅ 存在，status=pending，qc-477c4c3d，series nifti-b63b1d93，flags=["connected_component","boundary_smoothness","hu_distribution"]（qc_reports.flags 列） |
| Dice s0662 | eval/holdout_results.json cases 列表 | ✅ mean_dice 0.98、最差 gallbladder 0.9266、n_zeros=0 |
| Dice amos_0004 | 同上 | ✅ 教师 prostate 0.0（mean 0.7862）；学生 prostate 0.0 |
| Dice amos_0014 | 同上 | ✅ 教师 prostate 0.0、duodenum 0.744、urinary_bladder 0.7484；学生 prostate 0.0 |
| Dice s0071 | 同上 | ✅ vertebrae_C5 0.0、C6 0.011、T2 0.0149（剧本写 0.015 属四舍五入，保留） |
| 层数 | nibabel shape[2] | ❌→✅ s0662=419、amos_0014=93、s0071=278（清单写 306，**已改**）、amos_0004=78 |
| 端点行号 | sed -n 逐行 | ✅ app.py 14 处（:131/:475/:497/:515/:563/:661/:683/:725/:781/:801/:850/:874/:1114/:1480）全中；cli.py :42/:382/:436 中，:52→**:48**（已改，注释行）；review.html:255-256 读 ?series_id ✅ |
| alembic head | DB alembic_version 表 + `alembic heads` | ✅ f8a3b9c2d1e4 |
| C1 两行语义 | qc_trace.py:139/:174（qc_scoring human_outcome=None）+ app.py:607-621（全部 gate_decision 补填）+ qc_trace.py:234/:247-256（幂等不覆盖） | ✅ 属实 |
| C4 QC 条件 | config/qc_thresholds.yaml overall 节 | ✅ hard_flag_rules: [volume_prior]、min_score: 0.5 |
| M5-FIX-VOX §4 | 仓库根 M5-FIX-VOX-REPORT.md §4（:50-101） | ✅ run4-* 五轮、页面加载 160–180s 属实，引用保留 |
| C2 日志 | `ls ~/data/BodyDonor/*.log` | ❌→✅ 无命中（清单写"仅测试日志"，**已改**为"无命中、历轮测试日志在 tmp/ 下"） |
| dry-run 声明 | `python -m bodydonor.cli import amos_0014 --modality CT --dry-run` | ✅ 1 series、0 skipped、轴位；affine 6.4x anisotropy 注记（不排除） |

补充实测：rt-e230e4cf 的 mask mv-4039eb2d 是 **manual（review-ui）非
teacher**——文档未误述此点，仅备注；C3 join 实测 1 行、mismatch 0 行、
C1/C3/trace-flags SQL 逐字跑通（现库 0 行 trace）。
AMOS imagesTr 40 文件全 3D（nibabel 逐一核对，"全部为单模态 CT"按名单核对
成立）；amos_0004/source.json source=total ✅。

## 3. 全量测试 + ruff（实测）

- 全量 pytest：nohup PID 686781 串行跑（GPU 锁未并行、未硬闯），耗时约 31
  分钟（01:58 起 02:32 止，实测 mtime）。结果：
  - **JUnit 实测：748 tests / 0 errors / 0 failures / 0 skipped**（JUnit XML
    `m7-shadow-suzi-fulltest-junit.xml`，748 个 testcase 元素逐一解析核对，
    无 failure/error 子元素）
  - 日志 `~/work/bodydonor-plans/shadow-my-fullrun.log`：0 行 FAILED/ERROR
  - **注记（如实）**：日志尾缺 pytest summary 行（进程在 JUnit 落盘后未打印
    summary 即退出；grep 全日志无 "N passed" 行）——因此总数以 JUnit 实测
    748/0/0/0 为准，不写"全绿"以外的推断表述
- ruff 全仓（src + tests）：`All checks passed!`
- 本轮只改文档（docs/ 3 份 + 报告），未改代码，预期全绿与实测一致

## 4. deep-reviewer 审查 findings 与处理

deep-reviewer（只读子代理，无 Bash）返回 **4 Warning + 5 Nit，0 Blocker**。
逐条 double-check（调用方实测复现）后全部确认属实，全部已修（提交 `87074c4`）：

| # | 级别 | finding | double-check 结果 | 处理 |
|---|------|---------|-------------------|------|
| W1 | warning | decided_at 是 UTC（qc_trace.py:251 datetime.now(UTC)），录屏索引写"本地时间"，C1 窗口比对东八区会误判 | ✅ `date +%z`=+0800、qc_trace.py:251 实读 UTC | recording.md 录屏起止改 UTC（格式 %Y-%m-%dT%H:%M:%SZ） |
| W2 | warning | auto 档不建 ReviewTask→无 rt_id→决策端点走不到→gate_decision 不补填；但 C1 要求每例 decision 与医生判定一致→矛盾；剧本替代口径含混（提 qc-calibrate 无关） | ✅ qc_pipeline.py:409-422（auto 不建）+ app.py:563（按 rt_id 锚）实读 | 剧本第 6 步写具体记录步骤（「auto 档无决策端点路径，human_outcome 不补填（设计语义）」+引用 app.py:563）；checklist C1 第 1 条对 auto 例改「不适用，非不通过」 |
| W3 | warning | uvicorn 启动无 cd 无 PYTHONPATH：venv bodydonor 可能指向主 checkout、DB url 相对 cwd | ✅ **实测 `python -c "import bodydonor"` 输出 `/home/gogvf/work/BodyDonor/src/...`（主 checkout）**——最重的一条，真实复现；config.py:66 url 相对路径 | 剧本启动命令改 `cd ~/work/BodyDonor-shadow && PYTHONPATH=$PWD/src nohup ... -m uvicorn ... --port 8000`，并加 import 路径核对步骤 |
| W4 | warning | 决策回流写 config/qc_thresholds.yaml（受 git 管理），无还原步骤，有误提交风险（759411f 先例） | ✅ qc_reflux.py:45 实读 `overall["calibrated_on"] = total` | 剧本第 6 步 + checklist C1 第 4 条加「git checkout -- config/qc_thresholds.yaml 还原」 |
| N5 | nit | "series_id 三处一致：录屏文件名"，但文件名只含 case_id | ✅ recording.md:36-41 命名 `wt-<case_id>-...` | 改「case_id 与文件名一致；series_id 两处一致（会话索引+decision_traces）」 |
| N6 | nit | 剧本只写 s0662=419，其余三例未逐例写层数 | ✅ nibabel 实测四例 | 剧本第 1 步逐例写 419/93/278/78 |
| N7 | nit | C3 "version=基线 teacher+1" 未读递增逻辑 | ✅ 实读 app.py:1671 附近 `version = (last or 0) + 1`（series 内 max+1） | 改「该 series 现有 max(version)+1」+实测行号 |
| N8 | nit | C2 grep 模式在 pytest 日志上验证，不能证明对 uvicorn 日志有效 | ✅ 属实（m7-s3r2-fulltest.log 是 pytest 输出） | 改「模式为通用判据，走查当天先跑基线」 |
| N9 | nit | 启动命令漏 --port 8000（先例有） | ✅ M5-BACKLOG-REPORT.md:53-54 实读 | 已并入 W3 修复 |

审查员读码确认无需修改项：端点行号全对、方法/参数一致、alembic 链
3c17→e7f3→b8e2→a4f9→f8a3 无下游修订、C1-C3 SQL 列名/schema 全真实、
overall 节一致、DecisionTrace 字段全存在、decided_at 格式 %Y-%m-%dT%H:%M:%SZ。

## 5. 交付与实测数字汇总

- diffstat（421ef0c..87074c4）：3 files changed, 349 insertions(+)（上一轮）
  + 本轮 2 提交（核实修正 2 files 7+/6-；审查修复 3 files 35+/17-）
- 全量测试（§3 实测）：748 tests / 0 errors / 0 failures / 0 skipped
  （JUnit 实测）；日志 0 行 FAILED/ERROR；ruff `All checks passed!`

## 6. 未决清单（用户定，不写"全部就绪"）

1. **医生人选**：须用户提供（剧本不编造）。
2. **走查时间**：须用户提供；四例串行 GPU 分割 + 逐类审阅，预估半天。
3. 录屏归档位置：方案暂定 `~/data/BodyDonor/walkthrough-recordings/`，如院内另有规定以规定为准。
4. 决策端点操作留痕形态（账号/签名要求，如院内要求）：须用户确认。
5. rt-e230e4cf 的 mask 为 manual（review-ui 手工保存）非 teacher——走查 Case B 时以 DB 实际状态为准，不预设。
6. deep-reviewer 未覆盖项（其自报）：venv 路径已由调用方实测（W3）；C2 grep 对 uvicorn 日志的正向验证留走查当天。
