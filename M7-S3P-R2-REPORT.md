# M7-S3P R2 轮次报告：③ source.json 回填（45 目录）

基线 R1 HEAD `dfe49a2`；分支 `feat/m7-s3-postmerge`；工作目录 `~/work/BodyDonor-S3P`。

## 0. 结论

45 个缺 `source.json` 的历史目录全部回填为 `source/task=total`（TS total 教师，Apache-2.0），0 个保持缺失；`read_teacher_provenance` 对全部 65 目录（训练池 35 + hold-out 30）实测 `provenance_missing=False`；`prepare_dataset(dry_run=True)` 预检通过。未动门禁代码。

## 1. 实测枚举（先枚举后回填，未抄报告数字）

### AMOS（`~/data/BodyDonor/downloads/amos22_subset/`，40 目录）

- **已有 source.json：20 个**（amos_0035/0036/0038/0042/0043/0044/0045/0047/0048/0049/0050/0052/0054/0057/0058/0059/0060/0064/0066/0067）——2026-11-09 10:56–11:46 生成，与 `nnUNet_raw/distill_teacher_inference.json`（n_cases=20，0 失败）逐 case 吻合，内容 `{"source":"total","task":"total","created_by":"run_distill_teacher_inference.py"}`。
- **缺失 source.json：20 个**（amos_0001/0004/0005/0006/0007/0009/0010/0011/0014/0015/0016/0017/0019/0021/0023/0024/0025/0027/0030/0033，**含 amos_0004**），全部 ts_out 117 类完整。

### ct_lite（`~/data/BodyDonor/downloads/ct_lite/ts_out/`，25 目录）

- **全部 25 个缺**（s0045…s1394），全部 117 类完整。

## 2. 来源判定（逐目录从证据定，不猜）

三档依据，每档有产生脚本 + mtime 窗口 + 独立旁证：

| 档 | 目录 | 证据 | 依据 |
|---|---|---|---|
| M3 门禁评估 | AMOS 15 个 hold-out（amos_0001…0023，mtime 2026-11-07 18:14–19:07） | `eval/evaluate_holdout.py` ts_teacher_inference 输出落 `amos22_subset/{case}/ts_out/`；holdout.txt 逐 case 吻合 | M3 门禁评估 TS 推理（81c94ac 之前产出） |
| M4 训练侧 | AMOS 5 个非 hold-out 训练 case（amos_0024/0025/0027/0030/0033，mtime 2026-11-08 11:06–11:16） | `scripts/run_distill_teacher_inference.py` 81c94ac 版本；`write_teacher_source_json` 补丁 a0da6d9（2026-11-09 10:47）晚于产出 | M4 训练侧教师推理，先于补丁 |
| M3 冷启动 | ct_lite 25 个（mtime 2026-11-07 17:26–18:55） | `scripts/qc_coldstart.py`（e97a152）；`config/qc_thresholds.yaml` cold_start n_cases=25 + calibrated_at 2026-11-07 实证 | M3 QC 冷启动 TS 推理 |

来源全部**按证据推断为 TS total**（mtime 窗口 + ts_out 117 类全量 + 产生脚本旁证；非任务清单实测），与已有 20 份 source.json 逐字段同构。判据为全目录 mtime 一致性（117 个 nii 逐一在窗口内才通过，非单文件）。

## 3. 回填清单

回填脚本：`scripts/backfill_source_json.py`（`--dry-run` 演练 -> 实写；幂等，已有 source.json 不重做）。

- **AMOS 20 份**：逐目录 `{case}/source.json`，内容 `{"source":"total","task":"total","created_by":"<M3 评估/M4 训练>","case_id":"...","backfill":true,"backfill_basis":"<表 2 依据>","backfilled_at":"2026-11-16"}`。
- **ct_lite 1 份（库级）**：`ts_out/source.json`，同上字段 + `"cases":[25 个 case_id]`。位置按 `read_teacher_provenance` 契约 `teacher_out.parent/source.json`——ct_lite 布局 `teacher_out=ct_lite/ts_out/{case}`，parent=`ts_out/`，**一库一份**非每 case 一份。

**实写 21 份文件、覆盖 45 个目录**（AMOS 20 每目录 + ct_lite 库级 1 份覆盖 25 case）。

### 过程中的纠错（如实记录）

第一版脚本把 ct_lite 写成每 case 一份 `ts_out/{case}/source.json`（25 份）——与 `read_teacher_provenance` 读取契约错一层，验收实测 10 个 ct_lite 训练 case 仍 `provenance_missing=True` 暴露。修正：脚本改库级一份（逐 case 判证据、全部通过才写，任一定不下来整库保持缺失 fail-closed）；错位文件是本轮刚写的，已删除并按契约重写。

## 4. 定不下来的目录

**无。** 45 个全部从证据定下（dry-run 与实写两轮均 0 个 KEEP-MISSING）。

## 5. 验收实测

- `read_teacher_provenance`：训练池 35 例（AMOS 25 + ct_lite 10）全部 `provenance_missing=False`、sources/tasks 全 `total`、backfill=true 15 例（5 AMOS 训练 + 10 ct_lite 经库级清单）；hold-out 30 例（15+15）全部 `provenance_missing=False`。合计 65 目录全过。
- `prepare_dataset(dry_run=True)`（默认 out_root）：预检 PASS，`n_cases=35`（AMOS 25 + ct_lite 10），exit 0。dry-run 仍逐 case `nib.load` 原图 + 15 类 mask 合并（跳过落盘不跳过合并），35 例约 9 分钟实测。
- 回填脚本幂等：第二次实跑 0 写入。
- 测试：`tests/test_m7_s3p2_backfill.py` 10 项（来源判定 6 + 写入契约/fail-closed/幂等/dry-run 4）。

## 6. 红线遵守

- 未删不重做已有数据（已有 20 份 AMOS source.json 未动；只补缺）。
- 未改 `read_teacher_provenance` / `prepare_dataset` 语义（本轮零行改动）。
- `source.json` 写在 `~/data/BodyDonor` 数据目录，不进 git；提交的只有脚本、测试与报告。

## 7. 审查 findings

deep-reviewer 对抗性审查 7 条（1 Blocker 未发现，4 Warning + 3 Note），全部 double-check 后修复：

| # | 级别 | 位置 | 问题 | 处置 |
|---|---|---|---|---|
| W1 | Warning | `scripts/backfill_source_json.py` undecided 分支 | evidence 字符串无条件 `stat()` aorta.nii.gz，ts_out 缺 aorta 时 FileNotFoundError 中止整个 `_run` | 修：`_aorta_mtime()` 存在性判断（缺失返回 None）；失败测试先写（复现确认）后修，`test_main_undecided_dir_without_aorta_does_not_crash` |
| W2 | Warning | ct_lite 库级清单 | 库级 `ts_out/source.json` 写入后对未来新增 case fail-open：`read_teacher_provenance` 读 ts_out/ 下任何目录都继承 total，门禁不拦 | **未改（另行决策）**：改契约须动 `read_teacher_provenance` 语义（本轮红线）；已列入未决 §8 |
| W3 | Warning | 报告 §5 | 训练池拆分写错（AMOS 20 + ct_lite 15 应为 AMOS 25 + ct_lite 10，与下文自相矛盾） | 修：改为 AMOS 25 + ct_lite 10（40-15=25，25-15=10） |
| W4 | Warning | 来源判据 | 单文件（aorta）mtime 判据偏弱，116 个其他文件未核对；报告把推断写成实测 | 修：判据升级 `_all_in_window`（全部 nii mtime 都在窗口内才通过，目录空 False）+ 报告措辞改"按证据推断"；fixture 同步改全目录 mtime |
| N5 | Note | 写入 | `write_text` 非原子，中断留半截 JSON（后续可见失败非静默） | 修：临时文件 + `os.replace` 原子写 |
| N6 | Note | 测试 | 残留死代码 `rc = bf.main() if False else None` | 修：删除 |
| N7 | Note | fail-open | ct_lite ts_out 空时 `ct_cases == all_ct` 均空成立，写出 `cases=[]` 的库级 total 清单（复现确认） | 修：条件加 `ct_cases` 非空；`test_main_empty_ct_lite_ts_out_writes_nothing` |

修复后：ruff 全绿；`tests/test_m7_s3p2_backfill.py` 12 项 + 受影响套件 90 项全过；真实数据重跑 dry-run 0 可回填（幂等不重做）。全量套件 694/694 passed、0 failed/0 error（EXIT=0，含 local_artifacts 17 项本机实跑；实跑约 40 分钟）。

## 8. 未决

1. **ct_lite 库级清单对未来 case fail-open（W2）**：`read_teacher_provenance` 不校验 case 是否在库级 `cases` 列表内，`ts_out/` 下新增目录会无证据继承 `source=total`。需要改契约（读侧校验 cases 成员，或 ct_lite 改每 case 一份放可读位置）——属 `read_teacher_provenance` 语义改动，本轮红线不动门禁代码，列为后续任务。当前 25 个 case 已回填不受影响。
2. **来源判据为推断非实测**：mtime 窗口 + 目录布局推断（W4 已加固为全目录 mtime 一致性 + 措辞修正），但非读取任务清单/下载 manifest 的实测来源。三档证据（mtime 窗口 + 117 类全量 + 产生脚本 + 独立旁证）方向上安全，如需更强审计链需回溯 M3/M4 运行日志（未留存）。
3. **prepare_dataset dry-run 耗时**：dry-run 仍逐 case 加载原图 + 15 类 mask 合并（跳过落盘不跳过合并），35 例实测约 9 分钟。如需纯预检（只查 provenance/许可/文件存在性）需在 `prepare_dataset` 加 early-exit——属门禁代码改动，未做。
