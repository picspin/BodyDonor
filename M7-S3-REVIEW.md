# M7-S3 深度审查清单（第 2d 轮，只审查不修复）

- 范围：`git diff d7b5c44..HEAD`（9 提交，HEAD d0824bf，分支 `feat/m7-s3-modality-registry`）
- 方法：4 个 deep-reviewer 只读子代理并行分片（a 模态门闩 / b 注册表路由 / c 迁移与 trace / d 报告事实核对），协调方逐条 double-check 后入列；关键 finding（Blocker、基线、测试计数、qc.py/pipeline.py 代码行）已实码复核
- SubagentStop hook 产物：worktree 内未生成 findings 文件；findings 取自子代理回传结果
- 本轮不修复任何 finding（修复是下一轮）
- 计数：**Blocker 1 / Warning 15 / Nit 8**

## Blocker

### B1. 生产派发路径不做模态门控，"total 拒 MR"只在函数层成立
- 位置：`src/bodydonor/segmentation.py:81`（`check_worker_task` 内 `check_task(task)` 不传模态）；同型问题 `src/bodydonor/cli.py:237`
- 复现：读码确认——`check_worker_task("total", Settings(teacher=TeacherConfig(allowed_tasks=["total"])))` 不抛 `SegmentWorkerError`，与模态无关；生产调用点 `segmentation.py:177` 与 `:338` 都走此函数。测试 `tests/test_teacher_registry.py:110` 只直接调 `check_task("total", modality="MR")`，未覆盖 worker 路径
- 影响：M7-S3-REPORT 把「total 拒 MR 的原因输出」列为验收证据，但模态门闩在生产派发链路不生效——MR series 仍可派发 `total`
- 修复方向：派发链路携带 modality（series/ingest 的模态字段）传入 `check_worker_task` → `check_task`；补一条经 `check_worker_task` 的 MR 拒绝测试

## Warning

### W1. `ready` 判定未跟随模态过滤
- 位置：`src/bodydonor/license_registry.py:354`
- 复现：`ready` 用 `entries`（全部同名条目）而非模态过滤后的 `ok`。测试中追加两条 `task="x"`（CT/ready=False，MR/ready=True），`check_task("x", "CT")` 返回 `(True, "allowed")`。当前注册表每 task 一条，暂未触发
- 修复方向：改 `ready = [e for e in ok if e["ready"]]`，补同 task 双模态条目测试

### W2. `allowed_tasks` 白名单兜底与规范 §2.2.2 偏差
- 位置：`src/bodydonor/license_registry.py:362-363`；规范出处 `/home/gogvf/work/bodydonor-plans/M7-PLAN-v3-research.md:101-105`
- 复现：`task="lung_vessels"` 不在 TEACHER_REGISTRY，经 `:362` 放行；规范写明"不再维护另一份白名单"，而 `config/settings.yaml:39-70` 的 28 个任务多数不在注册表内。报告 §6 已列为未决
- 修复方向：用户拍板前保留兜底并在报告明示偏差；或将白名单迁入注册表后退役

### W3. 白名单契约变化：配置不再能撤销注册表内任务
- 位置：`tests/test_license_whitelist.py:80-91`
- 复现：`LicenseRegistry(Settings(teacher=TeacherConfig(allowed_tasks=[]))).check_task("total", "CT")` 返回 True；仅 `blocked_tasks` 能否决。测试名 `test_bare_settings_fail_closed` 与断言（total 放行）矛盾
- 修复方向：改测试名（如 `test_bare_settings_registry_ready_task_allowed`），文档记录契约变化

### W4. `nninteractive` 的 `ready=True` 缺部署依据
- 位置：`src/bodydonor/license_registry.py:116-117`
- 复现：同条目 `weight_integrity="not_recorded"`，LICENSE-CHECK §八未声明权重已部署；`check_task("interactive_correction")` 因 ready=True 放行
- 修复方向：权重未部署则改 `ready=False`；已部署则补 checkpoint 路径与 sha256 实录

### W5. `research_use` 仅为声明，`check_task` 不读取
- 位置：`src/bodydonor/license_registry.py:343`（对照 `:104` nninteractive/restricted）
- 复现：把 `check_task` 当蒸馏门调用，`nninteractive` 返回 True，`research_use="restricted"` 不阻断；商用蒸馏红线目前仅由 config `commercial_distill_tasks: [total]` 保证
- 修复方向：蒸馏门读注册表 `research_use`，不只靠 config 列表

### W6. `nnunet_v2` 条目 `research_use="allowed"` 与 LICENSE-CHECK §七冲突
- 位置：`src/bodydonor/license_registry.py:171`
- 复现：框架条目（`output_type="framework"`）标 `research_use="allowed"`；LICENSE-CHECK §七写明框架不产生蒸馏资格
- 修复方向：改 `unverified` 或在 known_restrictions 写明；蒸馏路径拒绝 `output_type="framework"`

### W7. 恒真断言
- 位置：`tests/test_teacher_registry.py:167`
- 复现：`assert rec.planned != rec.actual or rec.planned == rec.actual` 对任意值恒真；FallbackRecord 测试只验证 dataclass 赋值
- 修复方向：删恒真断言；改为断言 planned/actual 确定值，或明确标注仅结构测试

### W8. 模态比较未归一化大小写/空白
- 位置：`src/bodydonor/qc.py:137`（对照 `review_session.py:293` 有归一化，口径不一致）
- 复现：`run_qc(..., modality="ct", ...)` → `hu_distribution = {"skipped": "modality unsupported"}`，本应真跑；`modality=" CT"` 同理；`tests/test_m7_s3_modality.py` 无小写/空白用例
- 修复方向：比较前 `.strip().upper()`，补归一化用例

### W9. `source_info_from_persisted` 未拒 None/空模态
- 位置：`src/bodydonor/pipeline.py:329`（对照 `export_seg.py:218` 执行 `ds.Modality = source.modality`）
- 复现：`source_info_from_persisted({"rows":32,"columns":32,"instances":[]}, "u1", "u2", None)` 不报错返回 `modality=None`；再交 `export_seg` 写出空 Modality。当前 src 无生产调用方传 None，属防御缺口（与 8dc6549"模态必须显式给出"不符）
- 修复方向：modality 为 None/空串 raise（与 persisted None 一致），补 raise 断言测试

### W10. gate_decision trace 未填 modality/skip_reason；报告"两条 trace 同源"陈述不成立
- 位置：`src/bodydonor/qc_pipeline.py:474-488`（`record_gate_trace` 调用）、`src/bodydonor/qc_trace.py:181-225`（gate trace 构造）；报告冲突 `M7-S3-REPORT.md:54-56`
- 复现：`record_gate_trace` 无 `modality` 参数、不写 `skip_reason`，尽管 `rule_scores` 与 `modality_row` 在作用域内。对含 skipped 规则的 MV 跑 `generate_review_tasks`，其 gate_decision trace 两列均 NULL。`grep 'skip_reason=' qc_trace.py` 只命中 `record_qc_trace` 一处（`:177`）
- 修复方向：`record_gate_trace` 增 `modality` 参数由 `qc_pipeline` 传入，`skip_reason` 由 `_skip_reasons(rs)` 填充；或确认为有意不填并在 models.py 文档与报告说明，同时改报告表述

### W11. 新填充行为（modality/skip_reason）无测试断言
- 位置：`tests/test_m6f_trace.py:1121` 附近（对照 `:1147-1179` roundtrip 测试）
- 复现：modality/skip_reason 仅出现在迁移 roundtrip 测试，只断言列存在与存量行 modality 为 None；删除 `qc_trace.py:176-177` 两行，现有测试仍全过（分片 c 依据读码，未实跑）
- 修复方向：新增单测：`record_qc_trace(modality="CT", rule_scores 含 skipped)` 断言两列值（含 disabled 排除、全 disabled 返回 None）

### W12. 报告 §2.2 注册表条目名列举错误
- 位置：`M7-S3-REPORT.md:38-39`
- 复现：报告称 12 条目含 `licensed_tasks`、`brain_aneurysm`；实际 TEACHER_REGISTRY task 为 total、total_mr、body_mr、vertebrae_mr、liver_segments_mr、liver_lesions_mr、feature_encoding、interactive_correction、brain_mri_qc、moose_qc、framework、flexray_xray（12 数字正确，名字错误——两者只是 license 说明字典的 key）
- 修复方向：改为真实 task 列表；brain_aneurysm 禁用语义指向 config `blocked_tasks`

### W13. 报告 §3.2 测试计数错误
- 位置：`M7-S3-REPORT.md:67`
- 复现：报告写"4 def × parametrize 2 = 6 项"；实际 `grep '^def test_'` 得 6 个函数（`:54 :74 :102 :122 :229 :241`），仅 1 处 parametrize×2 → 7 项收集。协调方已实码核实
- 修复方向：改为"6 个 test 函数、1 个 parametrize 2 项、合计 7 项收集"，以运行时 collect 计数为准

### W14. 报告 §5 ruff"全部改动文件"不实
- 位置：`M7-S3-REPORT.md:138-139`（§4 `:120` 同）
- 复现：列表仅 6 项（迁移/models/qc_trace/qc_pipeline/test_m6f_trace/test_m5e）；`qc.py`、`pipeline.py`、`license_registry.py`、`scripts/qc_coldstart.py`、`tests/test_m7_s3_modality.py`、`tests/test_teacher_registry.py`、`tests/test_license_whitelist.py` 未列入
- 修复方向：用 `git diff --name-only d7b5c44..HEAD` 生成真实清单重跑 ruff，或改为"列出的 6 文件通过"

### W15. 报告 §6"唯一仍冒充 CT"过度
- 位置：`M7-S3-REPORT.md:143-145`
- 复现：`grep -n '"CT"'` 除 `ingest.py:255` 外还命中 `app.py:226`（agent_context_preview 硬编码）与 `export_seg.py:49`（`SourceSeriesInfo.modality: str = "CT"` 默认值）
- 修复方向：改"主要"，并把两处列入未决问题 1 范围

## Nit

### N1. `run_qc` 注释"向后兼容"措辞误导
- 位置：`src/bodydonor/qc.py:481`
- 复现：不传 modality 的旧调用方（如 `tests/test_qc.py:106`、`:335`）hu_distribution 变 skipped modality_unknown——行为是有意设计（`test_m7_s3_modality.py:89-99` 断言），但注释兼容性表述与实际不符
- 修复方向：注释改为"modality 未传/未知时 HU 规则跳过（不再静默按 CT）"

### N2. `source_info_from_persisted` raise 分支无测试覆盖
- 位置：`tests/test_source_instances.py:120/151/176`
- 复现：三处调用均传非 None persisted 与字面量 "CT"，无 `pytest.raises(ValueError)` 用例
- 修复方向：补 persisted=None 的 raise 用例；采纳 W9 后再加 modality=None 用例

### N3. 迁移 roundtrip 测试只查列名不查类型/可空性
- 位置：`tests/test_m6f_trace.py:1145-1155`
- 复现：只比较 PRAGMA table_info 列名集合；把迁移 `config_hash` 的 `String(64)` 改 `String(63)` 测试仍过
- 修复方向：对 type 与 notnull 逐列断言

### N4. 注册表条目数断言过松
- 位置：`tests/test_teacher_registry.py:39`
- 复现：`len >= 11`，实际 12 条；删任一条目仍通过
- 修复方向：改 `== 12` 或按 (teacher_id, task) 逐条断言

### N5. MR 拒绝原因断言过弱
- 位置：`tests/test_teacher_registry.py:112`
- 复现：只断言 `"modality" in msg`，无法区分模态原因与其他拒绝
- 修复方向：断言完整片段（如 `"modality 'MR' not supported"`）

### N6. 白名单模块文档过时
- 位置：`tests/test_license_whitelist.py:7`
- 复现：文档写"只有 teacher.allowed_tasks 里的任务放行"，与注册表路由行为（`:88-89`）不符
- 修复方向：更新为注册表 ready 条目或 allowed_tasks 兜底

### N7. `validate_entry` 放过"(待建)"占位
- 位置：`src/bodydonor/license_registry.py:223-229`（占位 `:141 :161 :199`）
- 复现：`class_map_ref="synthseg-classes/v1 (待建)"` 通过校验（条目 ready=False 已兜底）
- 修复方向：`validate_entry` 拒绝含"待建"的 class_map_ref 作为 ready=True 前置条件

### N8. 报告 §1 提交清单缺 d0824bf
- 位置：`M7-S3-REPORT.md:8-16`
- 复现：列 8 条，缺第 9 条 d0824bf（报告草稿自身提交）
- 修复方向：补列或注明不含本报告提交

## 已核实无问题（不入列）

- 迁移与 models 6 列类型/长度/nullable/无 server_default/无索引一致；downgrade 按 `reversed(_NEW_COLUMNS)` 对称；迁移链 3c17abaf19f7 → e7f3a1c9d204 → b8e21c4a7f05 → a4f9e2c81d03 → f8a3b9c2d1e4 线性无分叉；`tests/test_m5e.py:48-52` head 断言与链尾一致且未弱化
- qc.py 门闩覆盖：`hu_values` 仅被 `rule_hu_distribution` 使用，唯一显式用 HU 的规则已门闩；`_skip_reasons`（`qc_trace.py:124-136`）排除 disabled、收录 skipped 原文、去重拼接，逻辑正确
- `run_qc` 的 `modality` 是末尾关键字参数有默认值，JSON runner 契约向后兼容；`scripts/cdp_json` 不调 `run_qc`
- `test_m7_s3_modality.py` 四类断言（MR skipped/unknown skipped/CT 不变/QCReport 留记录）真实存在且读回落库 `rule_scores`
- 边界：未知 task/空 task 拒绝；brain_aneurysm 经 blocked 一票否决红线保持；注册表 12 条 license 字段与 LICENSE-CHECK 一致（Apache-2.0/MRI-CORE 已纠正/CC BY-NC/MOOSE 与 SynthSeg QC-only/CC BY-NC-SA）；`commercial_distill_tasks: [total]` 保证 nnInteractive 不进蒸馏
- 报告运行时数字（624 passed / 2624.11s、16/18/8、roundtrip returncode=0、DB/eval 复制）不可静态证实，内部算术自洽（624 = 615+5+4；2624.11s ≈ 43:44），未列为 finding

## 未证实 / 未核查（供下一轮参考）

- `rule_volume_prior`（`qc.py:147` 起）函数体未通读——体积先验是否隐含 CT 层厚假设未核实（分片 a 仅 grep 关键字）
- 早退路径（空 mask/全 mask，`qc.py:493-505`）不写 HU skipped 记录——既有行为非本轮引入
- `ingest.py:255` NIfTI 硬编码 `modality="CT"` 不在本轮 diff 内，但意味着 MR NIfTI 绕过门闩（报告已记未决问题）
- `92b583d` ruff F401 结果未实跑；`ts_runner.py:41` 第二道白名单检查、`cli.py:237` 调用链未深入
- `tests/test_qc.py:106/:335` 旧用例断言是否依赖 HU 结果未核实（W8/N1 相关）
