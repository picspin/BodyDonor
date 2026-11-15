# M7-S3-FOLLOWUP-REPORT.md — S3F 第 5 轮：deep-reviewer findings 修复 + 报告定稿

日期：2026-11-15。分支：`feat/m7-s3-followup-3items`。
基线：第 4 轮审查（HEAD `62ed47b`，清单 `M7-S3F-REVIEW.md`：3 Blocker / 17 Warning / 8 Minor，全部经复现确认）。

## 1. 本轮提交列表（6 提交）

| 提交 | 内容 |
|------|------|
| `62c9288` | fix：B1 zip/API NIfTI 无显式 modality 报错 + W2 CLI 可选 + W3 冲突 warning + W4 None 标签 |
| `c5637b5` | fix：W7 QC unknown 模态归 modality_unknown skip 文案 |
| `2b16c01` | fix：B2/B3 研究路径 fail-closed + W8 profile 校验 + W11/W12/W13/W14/W16 + M1–M6 |
| `3f19400` | test：W5 CLI CliRunner 用例 + W10 prepare_dataset profile 透传测试 |
| `fa9594a` | docs：S3F ② 报告事实修正（88→71、行号、24/25 口径、W15 行为变更明示） |
| `f35dd18` | fix：ruff 修复（E501 拆行 + import 排序 + 删重复测试） |

`M7-S3F-REVIEW.md`（第 4 轮清单）随 `2b16c01` 提交。

## 2. 三项验收证据（S3F ①②③ 复述）

- **① 显式 modality**：NIfTI 无显式 modality -> IngestError（ingest 单文件 `ingest.py:218`、zip `_finish_nifti_files`，API 422）——`test_m7_s3f_modality.py` 15 例全绿。
- **② 注册表唯一来源**：`check_task` 白名单回退退役（`license_registry.py:452`）、24 任务登记、占位守卫匹配「待建/待落地」——`test_teacher_registry.py` 20 例全绿。
- **③ 研究蒸馏脱钩**：`profile="research"` 走注册表 `research_use`，不读 `COMMERCIAL_DISTILL_TASKS`；商用路径行为不变——`test_distill_license_gate.py` 30+ 例全绿。

## 3. Blocker 修复逐条（3/3）

- **B1**（`ingest.py:662`）：`_finish_nifti_files` modality 为空抛 `IngestError`（API 转 422），不静默记 unknown。测试 `test_zip_nifti_no_modality_raises` + API 层 422 断言（先红后绿）。
- **B2**（`ontology.py:640`）：restricted 分支只放行 `known_restrictions` 未声明「蒸馏阻断」的条目——synthseg（FreeSurfer SLA，§六）与 moose（数据含 CC BY-NC，§五）阻断；**FleXray 放行，依据 = LICENSE-CHECK §三**（权重 CC BY-NC 4.0 只阻断商用蒸馏，"仅科研（QC/研究路由）"——其 `known_restrictions` 无蒸馏阻断声明，是模态约束非许可阻断）。`license_inherits` 按条目实际许可逐条写出（`_restricted_license_label`：nnInteractive=CC-BY-NC-SA、flexray=CC-BY-NC、synthseg=FreeSurfer-SLA、moose=CC-BY-NC），不按 research_use 枚举硬编码；测试断言非 nnInteractive restricted 教师不返回 CC-BY-NC-SA 标记（先红后绿）。
- **B3**（`ontology.py:626`）：研究路径空 `teacher_task` fail-closed（阻断带「无注册表条目」原因）。商用路径 `:131` 同款放行未动（HEAD 既有行为）。

## 4. Warning 修复逐条（17/17）

- **W1**：删 `config.py` `is_task_allowed`（src 无调用方）；`test_storage_config.py` 改断言注册表语义。
- **W2**：CLI `--modality` 改 Optional（`typer.Option(None, ...)`）；NIfTI 无值由 ingest 层报错（配合 B1）。
- **W3**：DICOM 头与入参冲突 -> `report.warnings`（`ingest.py` `_build_dicom_series`，requested_modality 经 `_finish_dicom_files` 透传）。
- **W4**：`str(getattr(first_ds, "Modality", None) or None) or None`——缺失标签得 None，不再得字符串 `"None"`。
- **W5**：CLI CliRunner 用例 3 条（DICOM 无参走通 / NIfTI 无参 exit 1 / NIfTI 带参走通）。
- **W6**：`test_dicom_folder_ignores_modality_param` 补 modality 字段断言（等于头内值）。
- **W7**：`_modality_skip` 把 `unknown` 归 `modality_unknown`（与 None/空同语义；两种 skip 文案不再混用）。
- **W8**：`check_distill_source` 与 strict 版 profile 非法值 raise ValueError（`_DISTILL_PROFILES`）。
- **W9**：(a) research 空 task、(b) restricted synthseg/moose 阻断、(c) CC-BY-NC-SA 文案、(d) 非法 profile 测试补齐；`test_commercial_distill_only_total` 改名 `test_research_distill_blocks_unverified_registry_entry`（实测 unverified 阻断）。
- **W10**：`prepare_dataset(profile=...)` 透传测试（research 口径 unverified 拒绝 / 非法 profile raise / dry_run 不落盘）+ CLI `--profile` 已有 choices（S3F ③ 提交 `62ed47b`）。
- **W11**：`ts_runner.py:18-19、40` 注释改写（只查 RUNNER_BLOCKED_TASKS）。
- **W12**：`cli.py:225、596`、`app.py:147` 展示标注「仅展示；路由判定唯一来源 = 许可注册表」。
- **W13**：`LICENSES-THIRD-PARTY.md` §1.2 增注册表差异注记（allowed 18 / unverified 6 / LEGACY 不登记）。
- **W14**：`test_license_whitelist.py:108-112` 断言改注册表语义（不再钉 allowed_tasks 成员关系）。
- **W15**：`M7-S3F2-REPORT.md` §3.2 明示 ready=False 拒绝为行为变更（24 TS 任务一律 "not ready"，权重核实后置 ready=True 恢复）。
- **W16**：`validate_entry` 占位守卫匹配「待建」与「待落地」两者 + 双措辞测试。
- **W17**：`M7-S3F2-REPORT.md` 统一按实际登记 24（提交标题 25 = 含未登记 LEGACY，注记口径）。
- **另**：`M7-S3F2-REPORT.md` "88 passed" 改 **71**——协调方两次实测一致，本轮以 `--collect-only` 第三次核实（7 文件 `71 tests collected in 0.75s`，无 parametrize 展开）。

## 5. Minor 修复逐条（8/8）

- **M1**：export_seg `ds.Modality = "CR" if source.modality == "X-ray" else ...` + 测试覆盖（"X-ray" 非 DICOM CS 合法值，导出规范化 CR）。
- **M2**：`settings.yaml:35-36` 注释改「仅展示/历史记录；路由唯一来源 = 许可注册表」。
- **M3**：`test_teacher_registry.py:202` 注释「19 个」改「24 个」。
- **M4**：`license_registry.py:258-260` 「licens 摘要」笔误 + 多余空格修正（unverified 措辞同步）。
- **M5**：`distill_data.py:89-90、112-113` 删重复表述。
- **M6**：`ontology.py` docstring 的 nnInteractive 说明移到拒绝分支处（不再读成 nnInteractive 不受研究路径门约束）。
- **M7**：`M7-S3F2-REPORT.md:16` 行号 347→452（`def check_task`）。
- **M8**：`M7-S3F2-REPORT.md:98` 行号 segmentation.py:360→361。

## 6. 测试数字与验证

- 受影响测试文件（14 个）+ ruff：**211 passed / 0 F/E**，ruff **All checks passed**。
- 本轮新增/改写测试：B1（2 条改断言）、B2（2 条，先红后绿）、B3（1 条，先红后绿）、W5（3 条 CliRunner）、W7、W8/W9d（2 条）、W10（1 条透传）、W16（1 条双措辞）、M1（1 条 CR 规范化）。
- 无 skipif 跳过的逻辑测试（全合成 fixture，CI 可跑）；真实数据测试（AMOS/hold-out）由协调方独立重跑（不并行 pytest）。
- 修复过程中的连带更新：`test_api_ingest.py` 3 条 POST `/ingest/scan` NIfTI-zip 无参用例改带 `modality=CT`（B1 契约变更）；`test_m7_s3f_modality.py` API 用例改 422 断言。

## 7. 未决问题

- **FleXray 放行的边界**：`known_restrictions` 机制依赖「蒸馏阻断」子串匹配——后续登记新 restricted 教师时须显式写明是否阻断蒸馏（不能靠省略放行）。
- **`_restricted_license_label` 是查表非派生**：新 restricted 条目须同步补表项，否则标签为 `unknown（待逐条核实）`（fail-safe，不猜许可）。
- **商用路径空 `teacher_task` 放行**（`distill_data.py:131`）：HEAD 既有行为，本轮未动；实际风险受 `read_teacher_provenance` 缺省 `{"source": "total", "task": "total"}` 限制（缺 source.json 的历史目录）。
- **全量 pytest**：协调方独立重跑（GPU 锁持有者），本轮只跑受影响文件 + ruff。
- M7-S3F2-REPORT.md §4 中 `config.py is_task_allowed()` 的引用（W1 删除后）已由本轮 `2b16c01` 使该行过时——报告 §4 列的是"改后调用方"，其中 config.py 一行现已不成立，见 `M7-S3F-REVIEW.md` W1 修复。
