# M7-S3F-REVIEW.md — S3F 第 4 轮对抗性审查清单

- 分支 `feat/m7-s3-followup-3items`，HEAD `62ed47b`，基线 `efd0c61`；审查范围 `git diff efd0c61..HEAD`（9 提交，23 文件 +691/-83）。
- 方法：4 个 deep-reviewer 子代理分片（a 显式 modality / b 注册表与白名单 / c 研究蒸馏脱钩 / d 报告事实核对），全部只读（Read/Grep/Glob），未运行 pytest。3 个 Blocker 与关键 Warning 由协调方实读复核确认（ingest.py:652-664、ontology.py:595-646、config.py:42-56、license_registry.py:196-211/346、grep is_task_allowed 调用方）。
- **本轮只审查不修复**；修复在下一轮。

## Blocker（3）

### B1. zip/API NIfTI 无显式 modality 静默记 unknown，不报错（已复核确认）
- 位置：`src/bodydonor/ingest.py:662`（`modality or _NO_MODALITY`）；固化测试 `tests/test_m7_s3f_modality.py:52-58`；app.py:269 API 层透传。
- 复现：`POST /ingest/scan` 上传仅含 `.nii.gz` 的 zip、不带 `modality` 参数 → 200，`series[0].modality == "unknown"`。
- 冲突：验收为「无显式 modality 即报错/拒绝」，现为静默 `unknown`。且 QC 侧无对应 skip 语义（见 W7）。
- 修复方向：`_finish_nifti_files` modality 为空时抛 IngestError（API 转 422），test_zip_nifti_no_modality 改断言 raise。

### B2. restricted 分支一律放行研究蒸馏 + 硬编码 CC-BY-NC-SA（已复核确认）
- 位置：`src/bodydonor/ontology.py:640-644`。
- 复现：`mask_allowed_in_distill_training({"experimental": False, "source": "teacher", "task": "brain_mri_qc"})` 返回 True。
- 冲突：注册表 synthseg（`license_registry.py:137-140`）与 moose（:159-161）`known_restrictions` 明写「蒸馏阻断待法务复核；不得登记为可蒸馏」，restricted 分支直接放行违背登记语义；flexray（:199-202）同样受限。且硬编码 `license_inherits: CC-BY-NC-SA` 许可标签错误：synthseg=FreeSurfer SLA、moose=数据含 CC BY-NC、flexray=CC BY-NC 4.0，均非 CC-BY-NC-SA。
- 修复方向：restricted 分支只放行 known_restrictions 未声明蒸馏阻断的条目；`license_inherits` 按条目 license/provenance 逐条写出，不按 research_use 枚举硬编码。

### B3. teacher_task 为空时跳过注册表检查直接放行（fail-open，已复核确认）
- 位置：`src/bodydonor/ontology.py:626-627`（`if task:` 跳过 → :645 `return True, "ok"`）；空值来源 `src/bodydonor/distill_data.py:474/480`（`prov.get("task", "")`）与 :124 调用链。
- 复现：`check_distill_source("total", teacher_task="", profile="research")` 返回 `(True, "ok")`，不经任何注册表检查。
- 备注：商用路径 :131 的 `teacher_task and` 同样放行空值为 HEAD 既有行为；研究路径是本轮新引入的口子，修 B2 时一并 fail-closed。

## Warning（17）

### W1. is_task_allowed 白名单回退本体仍在（已复核确认）
- `src/bodydonor/config.py:48-52`：`return task in self.allowed_tasks` 回退保留；src/ 无调用，仅 `tests/test_storage_config.py:42-43` 调用，形成双权威。修复：删函数 + 改测试断言注册表。

### W2. CLI --modality 必填，纯 DICOM 导入被强制要求
- `src/bodydonor/cli.py:49-51`（Typer 必填）；DICOM 路径忽略该参数（`ingest.py:197`）。复现：`bodydonor import <DICOM 目录>` 不带 --modality → 参数缺失报错。修复：改 Optional，NIfTI 无值时 ingest 层报错（配合 B1）。

### W3. DICOM 头与 --modality 冲突时静默以头为准
- `ingest.py:197、362`：`import <dicom> --modality MR` 报告显示头内 CT，无告警。修复：不一致时报告 warning 或 CLI 提示忽略。

### W4. Modality 标签缺失的 DICOM 得到字符串 "None"（既有缺陷，不在本轮 diff）
- `ingest.py:362`：`str(getattr(first_ds, "Modality", "") or None) or None` 对缺失标签产出 `"None"`。修复：`str(getattr(first_ds, "Modality", None) or None)`。

### W5. CLI --modality 行为零测试覆盖
- `tests/test_m7_s3f_modality.py:5` 声称「CLI 必填」，全目录无 CLI 用例（grep import_cmd/--modality 无命中）。修复：补 typer CliRunner 用例。

### W6. test_dicom_folder_ignores_modality_param 只断言 n_series
- `tests/test_m7_s3f_modality.py:67-74`：未断言 modality 字段，「头内标签为准」未验证。修复：断言 `r2.series[0].modality == r1.series[0].modality` 且等于头内值。

### W7. QC 对 unknown 返回 "modality unsupported" 而非 modality_unknown
- `src/bodydonor/qc.py:139-141`：unknown 不在 RULE_MODALITIES；qc.py:134-135 仅处理 None/空，两种 skip 语义混淆。修复：`_modality_skip` 把 unknown 归 modality_unknown。

### W8. profile 非法值静默落商用分支
- `src/bodydonor/distill_data.py:97、141`：check_distill_source 与 strict 版不校验 profile；CLI 有 choices（`scripts/prepare_distill_dataset.py:44`），Python API 无防护。复现：`profile="Research"` 走商用分支无报错。修复：入口 raise ValueError。

### W9. 蒸馏许可门新分支缺测试钉住
- `tests/test_distill_license_gate.py` 全文件 + `tests/test_ontology.py:225`：未覆盖 (a) research 空 task（B3）、(b) restricted synthseg/moose 阻断（B2）、(c) CC-BY-NC-SA 文案错误、(d) 非法 profile（W8）。另 `test_commercial_distill_only_total` 名字误导，实测 unverified 阻断。修复：随 B2/B3 修复补断言 + 改名。

### W10. prepare_dataset profile 透传零测试
- `prepare_dataset(profile=...)` 与 CLI --profile（62ed47b）全仓仅单元直调，无透传/解析测试。修复：补 `prepare_dataset(dry_run=True, profile="research")` + CLI 解析用例。

### W11. ts_runner 注释过时
- `src/bodydonor/ts_runner.py:18-19、40`：注释称 runner「再查一次 config 白名单」且「同源」，实际只查 RUNNER_BLOCKED_TASKS（:42、:97-98）。修复：改注释或让 runner 也查注册表。

### W12. 调用方仍以 allowed_tasks 展示白名单计数
- `src/bodydonor/cli.py:225、596`、`src/bodydonor/app.py:147`：展示「任务白名单（teacher.allowed_tasks）」，与注册表唯一来源语义矛盾。修复：改注册表条目数或标注仅展示。

### W13. LICENSES-THIRD-PARTY.md 与注册表未同步
- `LICENSES-THIRD-PARTY.md:64-70`（§1.2）：vertebrae_body、teeth、trunk_cavities 等仍列 open；注册表（license_registry.py:291-317）标 unverified。修复：文档随注册表更新。

### W14. test_license_whitelist.py 断言仍钉 allowed_tasks 成员关系
- `tests/test_license_whitelist.py:108-112`：不影响路由，但把配置项钉成许可判据。修复：随 W1 改断言。

### W15. ready=False 拒绝为行为变更，报告未明示
- `src/bodydonor/license_registry.py:213-282`：25 TS 任务全部 ready=False，旧 allowed_tasks 放行的任务（lung_vessels 等）现一律 "not ready" 拒绝；`tests/test_teacher_registry.py:215、224` 已断言。属有意变化，需在 M7-S3F2-REPORT.md 明示为行为变更。

### W16. validate_entry 占位守卫不匹配「待落地」（已复核确认）
- `license_registry.py:346` 只匹配子串「待建」，组二 class_map_ref 写「待落地」（:280 等）；守卫不触发。当前 ready=False 无现实错误，但「永远不能在类映射落地前 ready」的保证不成立（M7-S3F2-REPORT.md:55-56 的陈述随之不成立）。修复：守卫匹配两者或报告删措辞 + 补测试。

### W17. M7-S3F2-REPORT.md 24/25 口径不符
- 报告 :10 与提交标题（842c713「25 任务登记」）口径冲突（实际登记 24，25=含未登记 LEGACY）；:75「25 个均按 TS 任务定义登记 CT」实际登记 24（license_registry.py:216-282 合计 18+5+1），报告 §3.3 自己也写 LEGACY 未登记。修复：统一 24 + 注记标题口径。

## Minor（8）

- M1. `tests/test_m7_s3f_modality.py:45`："X-ray" 非 DICOM CS 合法值（仅大写/数字/空格/下划线）；export_seg.py:220 `ds.Modality = source.modality` 会写出非法 CS。当前测试不写 DICOM 故未暴露。修复：规范化 DX/CR + 导出测试覆盖。
- M2. `config/settings.yaml:35-36`：注释仍写「教师任务白名单」，allowed_tasks 已无路由作用。改为展示/历史记录说明。
- M3. `tests/test_teacher_registry.py:202`：注释「19 个 TS 任务」与随后 24 项不符。
- M4. `license_registry.py:258-260`：「licens 摘要」笔误 + 括号内多余空格。
- M5. `distill_data.py:89-90、112-113`：孤立注释 + 「不由 commercial_distill_tasks 把关」与「走 ontology 版 research_use 门」表述重复。
- M6. `ontology.py:606-610` docstring「nninteractive 不在本研究路径」紧跟研究路径资格来源节，易读成 nnInteractive 不受研究路径门约束（实际 :615-623 显式拒绝）。建议移到拒绝分支处或改写。
- M7. `M7-S3F2-REPORT.md:16`：check_task 行号写 347，实际 `def check_task` 在 452（347 是 validate_entry 内部）。
- M8. `M7-S3F2-REPORT.md:98`：分发处行号写 segmentation.py:360，实际调用在 361。
- 备注（不计入）：`tests/factories.py:47` 测试工厂默认 modality="CT" 属测试辅助非生产残留；`check_teacher_task_commercial`（distill_data.py:163-169）读常量 COMMERCIAL_DISTILL_TASKS 与注册表脱钩为既有设计（test_license_whitelist.py:75 锁定 settings==["total"]）。

## 核对通过（无问题，子代理确认）

- 白名单回退已从全部路由路径删除（grep is_task_allowed：src 仅函数本体，调用方仅 test_storage_config.py:42-43）。
- 注册表 TS 条目数 30 与 settings.yaml 31 项减 lung_vessels_LEGACY 一致；全部条目带齐 research_use/ready/weight_integrity/known_restrictions/provenance，validate_entry 模块加载时校验（:332-335）。
- 拒绝消息 "not in teacher registry" 在 license_registry.py:488、segmentation.py:85-87 与测试间一致。
- lung_vessels_LEGACY src/ 无代码引用（仅 settings.yaml:43），被拒属预期。
- 注册表 36 条目对 TS 2.10.0 安装包实测核对一致（class_map 键、commercial_models['vertebrae_body']=305）；M7-S3F2-REPORT.md 提交 hash、三个 commit 文件集、18+5+1 分组、各调用方行号（app.py:147、cli.py:225/596、config.py:48）核实一致。
- 研究分支只对 source=="total" 生效（distill_data.py:117 source 白名单在研究分支之前拒绝）；nnInteractive 修正产物双路拒绝（distill_data.py:115、ontology.py:615-623）。

## 未证实（不计入 findings）

- M7-S3F2-REPORT.md:115「88 passed / 0 F/E, exit 0」：7 测试文件 test 函数计数 71，88 需 parametrize 展开约 17 条——需 pytest 实测（协调方全量运行进行中，以实测为准）。
- LICENSES-THIRD-PARTY.md 引用的 README「访问日期 2026-11-06」原始网页内容：无存档，未核实。
- export_seg 全链路（`_build_source_images` 未逐行读；pipeline.py:321 对 None 已 raise，未见 CT 缺省残留）。
- 报告「无 skipif 跳过的逻辑测试」：未逐文件核查 skipif。
