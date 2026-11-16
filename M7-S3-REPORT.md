# M7-S3 报告（第 2e 轮定稿：deep-reviewer findings 修复）

状态：定稿（2d 轮 deep-reviewer 审查 Blocker 1 / Warning 15 / Nit 8，本轮逐条修复；
清单见 M7-S3-REVIEW.md）。全量 pytest 数字已实测填入（§4）。

## 1. 分支与提交

- 分支：`feat/m7-s3-modality-registry`（基于 origin/main d7b5c44；S2 的旧基线
  7d644af 不再引用）
- 提交（全轮，hash 一行一个）：
  - `7fdb766` feat: QC 模态门闩——RULE_MODALITIES + _modality_skip，HU 规则只对 CT 真跑
  - `70e7b89` feat: qc_series 读 Series.modality 传 run_qc；qc-coldstart 显式 CT
  - `8dc6549` feat: source_info_from_persisted 移除 CT 缺省——modality 必须显式给出
  - `96d4e3e` test: 模态门闩测试——合成 MR skipped/unknown skipped/CT 不变 + QCReport 留记录
  - `ae33e9c` feat: 教师注册表与路由拒绝——首批条目 + check_task 改读注册表
  - `92b583d` chore: 删未用 import load_settings（ruff F401）
  - `ebbd217` feat: DecisionTrace 增 6 列——迁移 + 模型同步 + modality/skip_reason 填充
  - `d33ee34` test: m5e head 断言跟随迁移链尾——a4f9e2c81d03 → f8a3b9c2d1e4
  - `d0824bf` docs: 报告草稿
  - 2e 轮 findings 修复（逐条见 §8）：
  - `c0ba521` fix: B1 生产派发路径带模态门控——check_worker_task/segment_series/run_total 透传 modality
  - `52e335a` fix: W1/W4/W6/W7/W8/N4/N5/N6/N7——ready 跟随模态过滤、权重实录、nnunet unverified、模态归一化
  - `0e473a8` fix: W9+N2 source_info_from_persisted 拒 None/空模态 + N1 注释措辞
  - `9565e00` fix: W10+W11 gate trace 填 modality/skip_reason（两 trace 同源）+ N3 迁移逐列 type/notnull 断言
  - `dd3a10f` docs: 报告定稿——2e 修复逐条 + 事实修正

## 2. 改了什么

### 2.1 模态门闩（2a，`7fdb766`–`96d4e3e`）

- `qc.py`：新增 `RULE_MODALITIES`（规则 → 适用模态名单）与 `_modality_skip`；
  `run_qc` 增 `modality` 入参。模态不在规则适用名单 → `skipped: modality
  unsupported`；modality NULL/空（旧数据未回填）→ `skipped: modality_unknown`
  （§2.2.3：跳过写原因，不静默按 CT）。未声明模态的规则不受影响（volume_prior
  等照跑）。HU 规则只对 CT 真跑。
- `qc_pipeline.py`：`qc_series` 读 `Series.modality` 传 `run_qc`；
  `generate_review_tasks` 读候选池行的 `Series.modality` 传 `record_gate_trace`
  （W10，两条 trace 模态同源）。
- `pipeline.py`：`source_info_from_persisted` 移除 CT 缺省——modality 必须
  显式给出（数据侧由 ingest 源头给）；W9 后 modality 为 None/空串显式 raise
  （与 persisted None 一致）。
- `scripts/qc_coldstart.py`：冷启动脚本显式 `modality="CT"`；
  `scripts/run_distill_teacher_inference.py` 同（2e B1 后 `run_total` 门控
  需要显式模态，蒸馏路径是 CT 教师 total）。

### 2.2 教师注册表与路由拒绝（2b，`ae33e9c`–`92b583d`）

- `license_registry.py`：新增 `TEACHER_REGISTRY`（(task, modality) 条目，
  字段集按 M7-PLAN v3.2 §2.2.1 用户拍板：teacher_id、task、checkpoint_id、
  modalities、output_type、research_use、known_restrictions、provenance、
  ready、weight_integrity；mask 教师另带 class_map_ref 与
  data_contract_version）。首批 12 个 task：total、total_mr、body_mr、
  vertebrae_mr、liver_segments_mr、liver_lesions_mr、feature_encoding、
  interactive_correction、brain_mri_qc、moose_qc、framework、flexray_xray；
  brain_aneurysm 不在注册表——禁用语义在 config `blocked_tasks`（一票否决）。
- `check_task(task, modality)` 改读注册表（接口不变）：blocked_tasks 一票
  否决 → 注册表条目 ready 且模态匹配 → 放行；模态不符 / 未就绪 → 拒绝带
  原因（回退语义 §2.2.3）；无条目回退 allowed_tasks 白名单。
  模态比较归一化（W8：`.strip().upper()`，"mr"/" MR" 按 MR 处理）；
  ready 判定跟随模态过滤（W1：同 task 双模态条目下按过滤后的条目取 ready）。
- 拒绝输出带注册的 modalities 列表（`total` 套 MR 的原因输出可读）。

### 2.3 DecisionTrace 增 6 列（2c，`ebbd217`）

- 新迁移 `f8a3b9c2d1e4`：`batch_alter_table` 加 modality / teacher_id /
  distill_profile / skip_reason / config_hash / env——**全部可空**（存量
  DecisionTrace 行必须能迁移）；列已存在幂等跳过；downgrade 对称删列。
- `models.DecisionTrace` 同步 6 列（不 divergence）。
- 填充（写入处能直接取到的就填，不新造 plumbing）：
  - `modality`：`qc_series` 已查 `Series.modality`（本为 run_qc 传参），
    同一变量传 `record_qc_trace`。
  - `skip_reason`：`record_qc_trace` 与 `record_gate_trace` 从
    `rule_scores` 的 skipped 原文提取（`_skip_reasons`：去重 "; " 拼接，
    `disabled` 不记——配置态不是回退原因），两条 trace 同源（W10 修复后
    `record_gate_trace` 增 `modality` 参数由 `qc_pipeline` 传入，`skip_reason`
    同 `_skip_reasons(rs)` 填充，两条 trace 两列同值）。
  - `teacher_id` / `distill_profile` / `config_hash` / `env`：写入处取不到
    （QC 路径没有教师选择 / 蒸馏档案上下文），留 NULL，待 S5/M7-e1 manifest
    接线时填。

## 3. 测试

### 3.1 新增/受影响测试（逐文件实测）

| 文件 | 条数 | 结果 |
|---|---|---|
| tests/test_m7_s3_modality.py（新增，2a；6 个 test 函数、1 个 parametrize 2 项、合计 7 项收集） | 7 | 全绿（2a 轮实测） |
| tests/test_teacher_registry.py（新增，2b；2e 增 B1/W1/W8 测试） | 19 | 全绿（2e 轮实测） |
| tests/test_license_whitelist.py（改，2b；2e 改名 test_bare_settings_registry_ready_task_allowed） | 8 | 全绿（2e 轮实测） |
| tests/test_m6f_trace.py（增 roundtrip，2c；2e 增 W10/W11 两列断言测试） | 20（17 + 1 roundtrip + 2 新增） | 全绿（2e 轮实测） |
| tests/test_source_instances.py（2e 增 W9 raise 用例） | 10 | 全绿（2e 轮实测） |
| tests/test_m5e.py（head 断言跟随，2c） | 全文件 | 全绿（d33ee34 后定向复跑过） |

### 3.2 模态门闩验收（2a 已实测，QCReport 留记录）

- 合成 MR：HU 规则 `skipped: modality unsupported`，不进 flags。
- modality NULL：`skipped: modality_unknown`，QCReport 留记录。
- CT 行为不变（回归对照）。

### 3.3 注册表验收（2b 已实测）

- `total` 套 MR → 拒绝，原因输出带注册 modalities（`["CT"]`）。
- `total` 套 CT → 放行。
- `total_mr`（ready=False）→ 拒绝，原因输出「registered but not ready」。

### 3.4 迁移 roundtrip（2c 本轮实测，test_migration_s3_roundtrip_upgrade_downgrade_upgrade）

- `upgrade a4f9e2c81d03`（建 14 列旧表）→ 插存量行 → `upgrade head`
  （+6 列，存量行保留、modality 为 NULL）→ `downgrade a4f9e2c81d03`
  （6 列消失、原 14 列保留）→ 再 `upgrade head`（幂等可重放）。
- 全链 returncode=0，断言全过。N3 修复后 upgrade head 段逐列断言
  type（VARCHAR(n)）与 notnull（全部 nullable=1），不只查列名。

### 3.5 全量 pytest（最终轮数字见 §4）

- 跑法：主 checkout `.venv/bin/python -m pytest tests -rs`，
  `PYTHONPATH=/home/gogvf/work/BodyDonor-S3/src`（worktree 的 src 优先，
  避免 bodydonor 解析到主 checkout 安装包——首轮跑挂即此因，collection
  error：cannot import TEACHER_REGISTRY）。串行（GPU 锁）。
- DB：主 checkout `bodydonor.db` **复制**（cp，非移动）进 worktree 根
  （156 KB，gitignored）——`load_settings()` 无 URL 隔离的测试解析到
  `sqlite:///./bodydonor.db`（CWD 相对），worktree 副本让它们实跑真库。
- eval/*.json（student_holdout_results / holdout_results /
  student_teacher_consistency / coldstart_results，gitignored 运行产物）：
  同款**复制**进 worktree eval/——risk-queue 端点、teacherReliability、
  419 对回归等 local_artifacts 测试实跑不 skipif。
- **首轮失败已修（2 项）**：
  1. 无 PYTHONPATH → bodydonor 解析主 checkout 安装包 →
     test_teacher_registry collection error；加 PYTHONPATH 后过。
  2. 新迁移入链后 head 变更 → test_m5e head 断言（a4f9e2c81d03）红；
     断言跟随更新为 f8a3b9c2d1e4（提交 d33ee34，与 M6-f 时同款跟进）。

## 4. 全量 pytest 数字（最终轮实测）

```
最终轮（PYTHONPATH + DB 已拷 + eval/*.json 已拷，-rs，串行，2e findings 修复后）：
630 passed, 0 failed, 0 skipped, 0 deselected —— exit 0（wall ≈ 47min）
```

- 630 = 2c 轮 624 passed + 2e 新增 6 项（test_teacher_registry +3：B1 worker/
  W1 ready 模态/W8 归一化；test_m6f_trace +2：W10/W11 两列断言；
  test_source_instances +1：W9 raise），数字自洽。点数逐字数实测
  （8 行 × 72 + 末行 54 = 630，全部 `.`，无 F/E/s）。pytest 9.2.0 `-q`
  不打尾行 summary，以 exit code + 点数为准。
- ruff：全部改动文件 `All checks passed!`（见 §5）。

### 4.0 2c 轮数字（findings 修复前基线，如实记录）

```
2c 轮（PYTHONPATH + DB 已拷 + eval/*.json 已拷，-rs，串行）：
624 passed, 0 failed, 0 skipped, 0 deselected —— 2624.11s（43:44）
```

- 624 = 第 2 轮 615 passed + 5 failed（全部转绿）+ 4 skipped（全部实跑），
  数字自洽。无跳过项（本地产物全部在位实跑）。

### 4.1 中间轮数字（如实记录，非最终）

- 第 1 轮（无 PYTHONPATH）：collection error（1 error，1.81s）。
- 第 2 轮（PYTHONPATH，DB 已拷、eval json 未拷，-rN）：
  **5 failed, 615 passed, 4 skipped**（349.72s）。5 失败全部定性：
  - 4 × gitignored eval/*.json 产物缺失（worktree 未拷）：
    test_holdout_results_json_scope_numbers（FileNotFoundError）、
    test_teacher_eval_product_loads_117_classes（None）、
    test_submit_nni_persists_interaction_metrics（teacherReliability 判
    high≠low）、test_risk_queue_order（503 holdout 缺失）。
  - 1 × m5e head 断言（真失败，d33ee34 已修）。
- 修后定向复跑 6 项（上述 4 产物项 + m5e + 419 对回归）：
  **6 passed**（56.78s）。

## 5. ruff

- 全部改动文件（`git diff --name-only d7b5c44..HEAD` 除两份报告外 16 个
  代码文件：迁移、scripts/qc_coldstart、scripts/run_distill_teacher_inference、
  license_registry、models、pipeline、qc、qc_pipeline、qc_trace、segmentation、
  test_license_whitelist、test_m5e、test_m6f_trace、test_m7_s3_modality、
  test_source_instances、test_teacher_registry）：`All checks passed!`
  （2e 轮实测，主 checkout venv ruff）；全仓 `ruff check .` 同绿。

## 6. 未决问题（需用户拍板 / 下轮处理）

1. **`ingest.py:255` NIfTI 硬编码 `modality="CT"`**：`import_cmd` 对裸
   NIfTI 摄入路径硬编码 CT。模态门闩后这是主要仍冒充 CT 的路径——真实
   MR 裸 NIfTI 会被错判；同范围还有 `app.py:226`（agent_context_preview
   硬编码演示元数据）与 `export_seg.py:49`（`SourceSeriesInfo.modality`
   默认 `"CT"`），W15 修复时一并列入。需用户拍板：要求 NIfTI 摄入显式给
   modality（CLI 参数/文件名约定），还是保留 CT 缺省并在 trace/QCReport 记
   `source=default_ct`。两处演示/导出默认值超出 S3 范围，未改。
2. **`check_task` 白名单回退未退役**：注册表无条目时仍回退
   `settings.teacher.allowed_tasks` 白名单（现行逻辑第 4 分支，W2 偏差）。
   白名单里 `lung_vessels`、`body`、`cerebral_bleed` 等任务不在注册表，
   退役会直接拒掉它们——是否彻底退役为纯记录（无条目一律拒绝，注册即路由）
   需用户拍板。
3. **`research_use` 仅声明、`check_task` 不读取（W5）**：`check_task` 不是
   蒸馏门——restricted 教师（nninteractive 等）科研路径仍放行，蒸馏资格另由
   config `commercial_distill_tasks: [total]` 与 S6 蒸馏门把关。蒸馏门是否
   读注册表 `research_use` 待 S6 设计，未改调用方。
4. **注册表 `weight_integrity` 除 nnInteractive 外全部 `not_recorded`**：
   nnInteractive 已实录（sha256/size/LICENSE 运行时值，W4）；其余 11 条目
   checkpoint 未部署或未校验。S5/M7-e1 manifest 接线时补。
5. **nnInteractive `class_map_ref` 是提示型占位**：
   `prompt-targets/v1 (目标由交互提示决定，无固定类映射)`——无固定类映射，
   与 mask 教师的带版本类映射契约不一致。M7-e1 加权实验设计时需明确
   nnInteractive 样本进 manifest 的形态。
6. **`FallbackRecord` / `classify_missing_output` 未被 worker 执行路径消费**：
   目前只是工具函数 + 测试（§2.2.3 回退语义的形状先落地）；worker 分发层
   （segmentation.py）还没接。S5 或后续轮接线。
7. **真实 MR 误报率未核实**：模态门闩只证明门闩行为（skipped 留记录），
   不证明 MR QC 质量——需真实 MR 影像，不作为本切片完成条件（计划原文）。
8. **手动检查命令无模态上下文（顺带核查结论）**：`cli.py` 的
   `license check` 命令与 `ts_runner.py:41` 第二道白名单检查均不传
   modality——手动检查命令无 series 上下文（按 task 粒度检查可接受）；
   runner 第二道是防绕过纵深（主进程已带 modality 门控），保持 task 粒度。
   `tests/test_qc.py:106/:335` 旧用例断言 HU 结果，未传 modality → HU
   skipped——与 W8/N1 修复不冲突（断言不依赖 HU 真跑，行为属有意设计）。

## 7. 红线遵守

- 不做学习型门控器；未改 M7-e 交付形态；未碰商用蒸馏审批流程、
  Edge/mTLS/Orthanc/MinIO/Redis 产品化。
- 未对外宣称临床性能、学生门禁 passed 或 MR QC 可用。
- 未提交真实 hold-out JSON（本地产物测试标 `local_artifacts`，CI 跳过）。
- 未推远端、未 merge、未开 PR。

## 8. 2e 轮 findings 修复（deep-reviewer 2d 清单，逐条）

- **B1（Blocker）已修**（c0ba521）：`check_worker_task` 增 `modality` 入参
  透传 `check_task`；`segment_series` 先查 `Series.modality` 再门控（挪到
  series 查询后）；`run_total` 增可选 `modality`（qc_coldstart 与
  run_distill_teacher_inference 均显式 CT 传入）；补经 `check_worker_task`
  的 MR 拒绝测试。
- **W1 已修**（52e335a）：`ready` 改从模态过滤后的条目取；补同 task 双模态
  条目测试（CT ready=False 拒、MR ready=True 放）。
- **W3 已修**：测试改名 `test_bare_settings_registry_ready_task_allowed`，
  docstring 记录契约变化（注册表静态条目放行，仅 blocked_tasks 能否决）。
- **W4 已修**（52e335a，不改 ready=False——权重实际已部署）：nnInteractive
  条目 `weight_integrity` 实录 checkpoint 路径、LICENSE 首行（CC BY-NC-SA 4.0，
  即 session.license 运行时值）、sha256
  `b3ac4421f85457bbd1aa0d87f5e67bcb7bc8e2ce6b824b6ac45077cc5d630ea9`、
  size 411387150、status=verified。
- **W6 已修**：nnunet_v2 `research_use` 改 `unverified`（框架不产生蒸馏资格，
  LICENSE-CHECK §七），known_restrictions 注明。
- **W7 已修**：删恒真断言，改断言 planned/actual 确定值。
- **W8 已修**：`_modality_skip` 与 `check_task` 模态比较前 `.strip().upper()`
  （与 review_session.py 同口径）；补小写/空白用例。
- **W9+N2 已修**（0e473a8）：`source_info_from_persisted` modality 为
  None/空串 raise（与 persisted None 一致）；补 `pytest.raises` 两条用例。
- **W10+W11 已修**：`record_gate_trace` 增 `modality` 参数（qc_pipeline 从
  `Series.modality` 传入），`skip_reason` 由 `_skip_reasons(rs)` 填充——
  两条 trace 两列同源；补两列值断言测试（含 disabled 排除、全 disabled 返 None）。
- **W12/W13/W14/W15/N8（报告事实错误）**：§2.2 条目名改正、§3.1 计数改正
  （7 项/19 项/18→20 项）、§5 ruff 真实 16 文件清单、§6.1"主要"措辞 +
  app.py:226 / export_seg.py:49 列入问题 1 范围、§1 补 d0824bf 与 2e 提交。
- **Nit 全修**：N1 注释措辞、N3 迁移 roundtrip 逐列 type/notnull 断言、
  N4 `== 12` + (teacher_id, task) 唯一、N5 完整片段断言、N6 白名单模块
  文档更新、N7 `validate_entry` 拒 ready=True 的"(待建)"占位 class_map_ref。
- **不修（用户拍板项，报告 §6 明示）**：W2 白名单兜底保留（问题 2）、
  W5 research_use 不读（问题 3，S6 蒸馏门设计）、W15 两处 CT 硬编码不改
  （问题 1 范围）。
- **顺带核查**：`ts_runner.py:41` / `cli.py:237` 不传 modality 可接受（§6.8）。
