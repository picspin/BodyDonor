# M7-VERIFY-LOOP-REPORT — verify-loop skill（Phase A/B/C）

日期：2026-11-20 · 分支：`feat/m7-verify-loop-skill`（base `origin/main 421ef0c`）· HEAD：`82f0722`

Muse VM 同步（`~/workspace/bodydonor/`）：**待协调者执行**——本报告只写 worktree 副本。

## 提交清单（18 commits，base 421ef0c → 82f0722）

| commit | 内容 |
|---|---|
| cc7ee2d | Phase A：probe + DESIGN.md + SKILL.md skeleton |
| 4fb98ab | Phase B 决定：4 项开放问题逐项拍板 |
| 6de5c3e | Phase B cdp_open_review.py（CDP 渲染确认 + Edge 缺席 fallback） |
| e4c7f6b | cdp_open_review.py ruff SIM105 收尾 |
| f3ca9f4 | Phase B chain_steps.py（Step 2–12 + 逐步断言） |
| 96c2b25 | Phase B run_chain.sh + chain_app.py（入口 + scratch settings fail-closed） |
| 100580d | Phase B 先红后绿测试（git-clean/MaskVersion/trace 两档/export 接线） |
| fd7dac2 | /ingest/scan multipart 上传通道（File(...) 参数） |
| f8b4233 | step2 改 NIfTI-direct zip（DICOM zip commit 不落盘 = 既有行为） |
| 80da48d | seg-meta item key=class + run_chain 预建 data/tmp |
| 1326e88 | step9/10 mask_key 从 Series.storage_key 派生 |
| 391fb37 | Edge --headless=new（旧 headless 附着既有会话） |
| 5b1e4e4 | Edge profile 落 Windows 侧 TEMP |
| d6ea220 | CDP 等待 75s（冷启动慢） |
| 9308784 | Edge profile 经 wslpath -w 转 Windows 形路径 |
| b147df8 | SKILL.md 脚本表 + 链路 12 步与实现同步 |
| bcb1cf6 | Phase C §3 勘误（NIfTI-direct 决定 5 / 12b 范围 / 悬空节 / frontmatter） |
| 82f0722 | Phase C：CDP 目标按 type=="page" 过滤（dogfood 实测修复） |

## Diffstat（全轮）

7 files changed, 1352 insertions(+)：DESIGN.md 187 / SKILL.md 78 / cdp_open_review.py 279 / chain_app.py 34 / chain_steps.py 396 / run_chain.sh 116 / test_verify_loop_chain.py 262。

## 设计决定（DESIGN.md 行号）

Phase A（4 项）：
1. 触发时机 = 仅 pre-commit（DESIGN.md:6）——非 CI-every-run、非 ad-hoc、比 pytest 重
2. 链路 12 步固定顺序（:22；step 2 已勘误为 NIfTI-direct，:28）
3. git-status-clean 断言范围 precise（:49）
4. CI Hypothesis 固定种子（:70）

Phase B（4 项，:121 起）：
1. 分段走真实 CPU 路径不 mock（:123）——87s/117 类实测
2. 渲染确认 = 200 + hdr-status=="已加载" + 4 canvas + console 无 error（:134）
3. Edge 存在走 CDP；缺席 fallback = HTTP-only 冒烟，明标 degraded（:143）
4. DecisionTrace "两行" = pipeline_stage 列非 trace_kind（:153）

Phase C §3(a)（:162，决定 5）：链路导入走 NIfTI-direct——DICOM commit 不落像素
是既有行为，两 checkout 实测确认（见下）。

## §3(a) 核查结论：DICOM 主张 **确认**（两 checkout 实测）

- 机制：`_build_dicom_series` 像素栈 = 单层 2D `sitk.GetImageFromArray(arr)`
  （ingest.py:410，arr 为单实例 (rows, cols)）；像素落盘在
  `pipeline._save_pixels`（pipeline.py:354，**不在 ingest.py**——任务书提示的
  grep 落空属实），其内 `reorient_ras`（nifti_geom.py:143）→
  `nib.as_closest_canonical` 对 2D 图 raise
  `OrientationError: Data array has fewer dimensions than orientation`
  （nibabel orientations.py:159），被 `_save_pixels` 的
  `except Exception`（pipeline.py:383，"像素落盘失败不中断导入"）静默吞掉 →
  返回 None → `storage_key` 永不回填（pipeline.py:136-137）。
- 实测（2026-11-20，探针脚本合成 DICOM zip 40 slices scan→commit）：
  - 本 worktree：`storage_key=None`、存储目录零文件（pixel_data 在但落盘失败）
  - main checkout（~/work/BodyDonor）：**同** `storage_key=None`、零文件
  - 异常类型单独复现：OrientationError（2D nib 图过 as_closest_canonical 即 raise）
- 结论：NIfTI-direct（决策 5）与实现一致；DESIGN.md 决定 2 step 2 已勘误（:28）。
  修 DICOM 2D 落盘属导入管线工作，非本链回归，另派（开放项 1）。

## §3(b)(c)(d)

- (b) SKILL.md frontmatter + 正文已改 NIfTI-direct（含 DICOM-zip 不落像素注记）
- (c) DESIGN.md 悬空空节已删（内容已并入 Phase B 决定 + §3(d) 节）
- (d) 12b 范围拍板：应用无日志文件——`src/bodydonor/` 零处 `logging` 引用
  （grep 实测），uvicorn stderr 即全部日志面，应用日志 grep 无从接线；
  已记录 DESIGN.md:180，非静默缺口

## Dogfood 证据（Phase C 任务 1）

- 首跑（04:18）：step6 CDP 红（hdr 超时 + canvas=0 + console 干净）→ 根因复现：
  CDP `/json` 里 Edge 自带扩展的 `background_page` 目标排在 `page` 之前
  （注册竞态，顺序不稳），`cdp_open_review.py:152` 盲取 `tabs[0]` 连到扩展页，
  导航落空。修复：按 `type=="page"` 过滤（82f0722）。链的 git-clean 断言
  正确拦下未提交的修复（白名单外 ` M cdp_open_review.py` 判红）——断言有效。
- 修复后全链重跑（04:20:38–04:22:10，**92s**，12 步全绿 PASS）：
  - step1 FastAPI up（8765，scratch settings fail-closed）
  - step2 NIfTI-direct zip 导入 → series nifti-3f2986d2
  - step3 真实 CPU 分段 n_classes>0
  - step4 QC 评分 qc_report_id 在
  - step5 分档 plan 在
  - step6 CDP：hdr-status=="已加载" + canvas=4 + console 无 error
  - step7 save 200（deleted_classes 整类删除）
  - step8 MaskVersion versions=[1,2]（teacher v1 + save v2）
  - step9 SEG 导出 seg.dcm 非空
  - step10 verify_seg n_errors=0（体素差=0）
  - step11 trace 两档各 1（qc_scoring + gate_decision）
  - step12 git-clean 白名单外零脏 + 12b 日志无 ERROR
- 日志：`/tmp/vl-dogfood-c.log` → 持久副本
  `/home/gogvf/work/bodydonor-plans/vl-dogfood-c.log`

## 测试

- 全量 pytest：**760 passed, 0 failed, 0 errors**（2679.50s = 44:39）
  JUnit：worktree `m7-verify-loop-suzi-fulltest-junit.xml`
  （failures="0" errors="0" tests="760"）
- 先红后绿（Phase B，tests/test_verify_loop_chain.py 262 行）：
  git-clean 真红（泄漏文件）/ traces 空库红+他 series 不冒充 /
  mask_version 空库红+v1 红+v2 绿 / export 接线红（mask 缺失，行为红由
  test_export_seg.py 既有覆盖）
- ruff：`ruff check src tests .claude/skills/verify-loop/scripts` 全绿

## 开放项（非"全修"）

1. **DICOM 2D 像素落盘静默失败**（本报告 §3(a)）：`_save_pixels` 吞
   OrientationError → storage_key=None。两 checkout 同行为，修属导入管线
   （2D 图跳过 reorient 或多维化），另派。链路已绕行（NIfTI-direct）。
2. **Step 6 CDP 目标竞态**已修（type=="page" 过滤），但 /json 目标列表
   含扩展 background_page/service_worker 的机器上仍有理论窗口（page 目标
   缺失时回落 tabs[0]）——当前 Edge 实测 page 恒在，未观测到复发。
3. **Edge profile 目录清理**：Windows 侧 TEMP 的 vl-edge-* profile 因
   CrashpadMetrics-active.pma 权限拒绝无法 rm（9P）——累积 5 个，手工清理。
4. **Step 12b 只覆盖 uvicorn stderr**：app 无 logging 基建，应用日志 grep
   无从接线（DESIGN.md:180 已记录）；若未来 app 加 logging，须补 12b。
5. **Hypothesis 四条不变量**未写（DESIGN.md 决定 4 范围外，属后续）。
6. Muse VM `~/workspace/bodydonor/` 副本同步：待协调者执行。
