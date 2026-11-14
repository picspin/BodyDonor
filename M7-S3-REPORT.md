# M7-S3 报告草稿（第 2c 轮收尾）

状态：草稿（待 deep-reviewer 下一轮独立审查）。全量 pytest 数字已实测填入（§4）。

## 1. 分支与提交

- 分支：`feat/m7-s3-modality-registry`（基于 main @ 7d644af 之后的主线）
- 提交（全轮，hash 一行一个）：
  - `7fdb766` feat: QC 模态门闩——RULE_MODALITIES + _modality_skip，HU 规则只对 CT 真跑
  - `70e7b89` feat: qc_series 读 Series.modality 传 run_qc；qc-coldstart 显式 CT
  - `8dc6549` feat: source_info_from_persisted 移除 CT 缺省——modality 必须显式给出
  - `96d4e3e` test: 模态门闩测试——合成 MR skipped/unknown skipped/CT 不变 + QCReport 留记录
  - `ae33e9c` feat: 教师注册表与路由拒绝——首批条目 + check_task 改读注册表
  - `92b583d` chore: 删未用 import load_settings（ruff F401）
  - `ebbd217` feat: DecisionTrace 增 6 列——迁移 + 模型同步 + modality/skip_reason 填充
  - `d33ee34` test: m5e head 断言跟随迁移链尾——a4f9e2c81d03 → f8a3b9c2d1e4

## 2. 改了什么

### 2.1 模态门闩（2a，`7fdb766`–`96d4e3e`）

- `qc.py`：新增 `RULE_MODALITIES`（规则 → 适用模态名单）与 `_modality_skip`；
  `run_qc` 增 `modality` 入参。模态不在规则适用名单 → `skipped: modality
  unsupported`；modality NULL/空（旧数据未回填）→ `skipped: modality_unknown`
  （§2.2.3：跳过写原因，不静默按 CT）。未声明模态的规则不受影响（volume_prior
  等照跑）。HU 规则只对 CT 真跑。
- `qc_pipeline.py`：`qc_series` 读 `Series.modality` 传 `run_qc`。
- `pipeline.py`：`source_info_from_persisted` 移除 CT 缺省——modality 必须
  显式给出（数据侧由 ingest 源头给）。
- `scripts/qc_coldstart.py`：冷启动脚本显式 `modality="CT"`。

### 2.2 教师注册表与路由拒绝（2b，`ae33e9c`–`92b583d`）

- `license_registry.py`：新增 `TEACHER_REGISTRY`（(task, modality) 条目，
  字段集按 M7-PLAN v3.2 §2.2.1 用户拍板：teacher_id、task、checkpoint_id、
  modalities、output_type、research_use、known_restrictions、provenance、
  ready、weight_integrity；mask 教师另带 class_map_ref 与
  data_contract_version）。首批 12 条目（total、licensed_tasks、brain_aneurysm、
  nninteractive、synthseg、moose、flexray 等）。
- `check_task(task, modality)` 改读注册表（接口不变）：blocked_tasks 一票
  否决 → 注册表条目 ready 且模态匹配 → 放行；模态不符 / 未就绪 → 拒绝带
  原因（回退语义 §2.2.3）；无条目回退 allowed_tasks 白名单。
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
    `disabled` 不记——配置态不是回退原因），两条 trace 同源。
  - `teacher_id` / `distill_profile` / `config_hash` / `env`：写入处取不到
    （QC 路径没有教师选择 / 蒸馏档案上下文），留 NULL，待 S5/M7-e1 manifest
    接线时填。

## 3. 测试

### 3.1 新增/受影响测试（逐文件实测）

| 文件 | 条数 | 结果 |
|---|---|---|
| tests/test_m7_s3_modality.py（新增，2a；4 def × parametrize 2 = 6 项） | 6 | 全绿（2a 轮实测） |
| tests/test_teacher_registry.py（新增，2b） | 16 | 全绿（2b 轮实测） |
| tests/test_license_whitelist.py（改，2b） | 8 | 全绿（2b 轮实测） |
| tests/test_m6f_trace.py（增 roundtrip，2c） | 18（+2 迁移） | 全绿（2c 本轮实测，16 deselected 是 -k migration 过滤） |
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
- 全链 returncode=0，断言全过。

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
最终轮（PYTHONPATH + DB 已拷 + eval/*.json 已拷，-rs，串行）：
624 passed, 0 failed, 0 skipped, 0 deselected —— 2624.11s（43:44）
```

- 624 = 第 2 轮 615 passed + 5 failed（全部转绿）+ 4 skipped（全部实跑），
  数字自洽。无跳过项（本地产物全部在位实跑）。
- ruff：全部改动文件 `All checks passed!`（见 §5）。

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

- 全部改动文件（迁移、models、qc_trace、qc_pipeline、test_m6f_trace、
  test_m5e）：`All checks passed!`（2c 本轮实测，主 checkout venv ruff）。

## 6. 未决问题（需用户拍板 / 下轮处理）

1. **`ingest.py:255` NIfTI 硬编码 `modality="CT"`**：`import_cmd` 对裸
   NIfTI 摄入路径硬编码 CT。模态门闩后这是唯一仍冒充 CT 的路径——真实
   MR 裸 NIfTI 会被错判。需用户拍板：要求 NIfTI 摄入显式给 modality（CLI
   参数/文件名约定），还是保留 CT 缺省并在 trace/QCReport 记
   `source=default_ct`。
2. **`check_task` 白名单回退未退役**：注册表无条目时仍回退
   `settings.teacher.allowed_tasks` 白名单（现行逻辑第 4 分支）。白名单是否
   彻底退役为纯记录（无条目一律拒绝，注册即路由）需用户拍板。
3. **注册表 `weight_integrity` 全部 `not_recorded`**：12 条目的
   sha256/size_bytes 均未实录（checkpoint 未部署或未校验）。S5/M7-e1
   manifest 接线时补。
4. **nnInteractive `class_map_ref` 是提示型占位**：
   `prompt-targets/v1 (目标由交互提示决定，无固定类映射)`——无固定类映射，
   与 mask 教师的带版本类映射契约不一致。M7-e1 加权实验设计时需明确
   nnInteractive 样本进 manifest 的形态。
5. **`FallbackRecord` / `classify_missing_output` 未被 worker 执行路径消费**：
   目前只是工具函数 + 测试（§2.2.3 回退语义的形状先落地）；worker 分发层
   （segmentation.py）还没接。S5 或后续轮接线。
6. **真实 MR 误报率未核实**：模态门闩只证明门闩行为（skipped 留记录），
   不证明 MR QC 质量——需真实 MR 影像，不作为本切片完成条件（计划原文）。

## 7. 红线遵守

- 不做学习型门控器；未改 M7-e 交付形态；未碰商用蒸馏审批流程、
  Edge/mTLS/Orthanc/MinIO/Redis 产品化。
- 未对外宣称临床性能、学生门禁 passed 或 MR QC 可用。
- 未提交真实 hold-out JSON（本地产物测试标 `local_artifacts`，CI 跳过）。
- 未推远端、未 merge、未开 PR。
