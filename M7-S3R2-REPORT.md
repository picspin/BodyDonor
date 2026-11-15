# M7-S3 收口轮第 2 轮报告（S3R2）

- 日期：2026-11-15
- 分支：`feat/m7-s3-closeout`（worktree `~/work/BodyDonor-S3R`）
- 基线：`cb4f8fd`（S3R1 HEAD）
- Ponytail：full（session hook `PONYTAIL MODE ACTIVE` level: full 确认）

## 提交列表（S3R2 本轮）

| hash | 内容 |
|---|---|
| `c164a84` | ④ 许可判定结构化：36 条目新增 `research_distill`/`restriction_basis`/`output_license_inherits` 三字段；删"蒸馏阻断"子串匹配与 `_restricted_license_label` 查表；`validate_entry` 跟进新字段 |
| `d91443f` | ⑥ X-ray 导出修正：DX/CR 保留源 SOP Class；SEG 自身模态与源影像模态分开核对 |
| `2e6001a` | ruff F401 清理 |

## 改了什么

### ③ ready 三分验证（只验证，未改任何 ready 值）

任务定义：研究用途是否允许（research_use）/ 适配器是否支持 / 当前机器权重是否可用三件事分开，不让一个布尔值承包。验证结论：**当前 HEAD 已是三分结构，无回退，无需修**。

- **许可**：`TEACHER_REGISTRY.research_use`（allowed/restricted/unverified）+ 本轮新增 `research_distill`——注册表条目级字段，不参与部署判断。
- **适配器/部署**：`LicenseRegistry.check_task` 的 `ready` + `modalities` 门控（`license_registry.py` check_task）：ready=False 拒绝带 "checkpoint 未部署" 技术原因；模态不符带 "not supported by registry"。
- **机器权重可用性**：推理链路 `check_task`（白名单一票否决）→ runner 子进程加载 checkpoint，缺失报 FileNotFoundError。

### ③ 验收证据

1. **total 研究推理实测走通**（GPU 锁先确认 FREE，独占跑）：
   - `segment_series('nifti-b63b1d93', task='total', device='gpu')`（amos_0004，CT 78 层）
   - 结果：OK，n_classes=117，elapsed=128.68s，peak_vram=8580.3 MiB（真卡，TS 2.10.0 权重在）
   - **ready=False 未挡已有 total 推理**——用户定性属实：不是 Blocker，无需修。
2. **缺权重/未部署 → 技术原因**：
   - `check_task('total_mr', modality='MR')` → `(False, "task 'total_mr' registered but not ready (checkpoint 未部署；见注册表 ready=False)")`——不带许可措辞（断言 '许可'/'BLOCKED'/'license' 不在消息内，通过）。
   - 分链路：`segment_series('nifti-b63b1d93', task='lung_vessels')`（CT series，注册条目 ready=False）→ `任务白名单拒绝：task 'lung_vessels' registered but not ready (checkpoint 未部署；见注册表 ready=False)`——技术原因，非许可原因。
3. **缺权重真实跑（nnInteractive，临时改名 checkpoint 事后恢复）**：
   - `run_task(...)`（amos_0004 图像 + prompts bbox）→ `{"status": "error", "exit_code": 1, "error": "模型加载失败：FileNotFoundError: ... checkpoint_final.pth"}`——技术原因（文件缺失），非许可原因。
   - 权重已恢复原状：sha256 前 16 位 `b3ac4421f85457bb` 与 S3 W4 实测记录一致。
4. **24 个新登记任务 ready=False 未改**——本轮零改动 ready 值。

### ④ 许可判定结构化

- **36 条目新增三字段**（M7-PLAN v3 §2.2.1 新增三字段行，一字不差）：
  - `research_distill`：allowed/conditional/blocked/unverified（枚举 `RESEARCH_DISTILL_VALUES`）
  - `restriction_basis`：许可明文还是项目政策，附 LICENSE-CHECK 章节
  - `output_license_inherits`：输出需保留的许可条款（取代 `_restricted_license_label` 查表）
- **判定改读结构化字段**：`ontology.mask_allowed_in_distill_training` 现读 `research_distill`：
  - allowed/conditional → 放行（conditional 派生物 reason 写 `output_license_inherits` 条款）
  - blocked/unverified/无条目 → 阻断待核实
  - **"蒸馏阻断"子串匹配已删**（`any("蒸馏阻断" in r ...)`）；`_restricted_license_label` 查表已删。`known_restrictions` 退役为说明文字，不再参与字符串判定。
- **字段值依据（逐项按 M7-LICENSE-CHECK 原文 + 章节）**：
  - **total / total_mr 等 TS 族（20 条目）**：allowed——许可明文 Apache-2.0（§四；S3F ② 双证据组同）
  - **teeth/trunk_cavities/liver_lesions/vertebrae_pp(+refined)（5 条目）**：unverified——许可证据单一来源（仅 README 清单），安装包结构未核实（S3F ② 组）
  - **vertebrae_body**：unverified——安装包 commercial_models 含（id 305 需 license）与 README open 清单出入（S3F ②）
  - **MRI-CORE（feature_encoding）**：allowed——Apache-2.0（§二，README "code and models"）
  - **nnInteractive（interactive_correction）**：conditional——项目政策（M6 已记录修正产物不进蒸馏边界）+ 许可明文 CC BY-NC-SA 4.0（§八：研究路由允许、派生物标 SA 并传播署名、商用须法务）；`output_license_inherits=["CC-BY-NC-SA 4.0（ShareAlike…）"]`
  - **SynthSeg（brain_mri_qc）**：conditional——FreeSurfer Software License Agreement v1.0 明示允许并入专有程序、商业化风险自担（§六），**不禁止本地科研蒸馏**；保留在第 3 条第三方权利 + ADNI DUA 两项法务未决（§六未解决项 1/2）
  - **MOOSE（moose_qc）**：conditional——权重 CC BY 4.0 本身允许商用含衍生（§五），不禁止本地科研蒸馏；保留在 NC 训练数据溯源（AutoPET）+ "Commercial Version" 关系两项未决（§五未解决项 2/3）
  - **nnUNet v2（framework）**：unverified——框架不附带权重，不产生蒸馏资格（§七）
  - **FleXray（flexray_xray）**：conditional——权重 CC BY-NC 4.0 禁止商业目的使用，QC/研究路由（输出不进商用蒸馏）允许（§三资格判断理由）；`output_license_inherits=["CC-BY-NC 4.0…"]`
- **红线遵守**：未重核任何许可原文（全部字段值引用 LICENSE-CHECK 既有结论）；SynthSeg/MOOSE 不因商业政策模糊自动禁止；未发明新禁令；未判任何一家为 blocked（本轮无人标 blocked，blocked 枚举保留给法务明文禁止的将来条目）。
- **`validate_entry` 跟进**：三字段必填 + `research_distill` 枚举校验 + `restriction_basis` 非空串 + `output_license_inherits` 列表。

### ⑥ X-ray 导出修正

- **DX/CR 保留源信息**：`_build_source_images` 合成源实例 SOP Class 按源模态取——DX → Digital X-Ray Image Storage - Presentation（`1.2.840.10008.5.1.4.1.1.1.1`），CR → CR Image Storage（`...1.1`），其余默认 CT Image Storage；`ds.Modality = source.modality` 保留源值。
- **内部别名**："X-ray"（ingest 层内部别名，非 DICOM CS 合法值）规范化为 CR；真实 DX/CR 源各自保留，不再一律转 CR。
- **SEG 自身模态与源影像模态分开核对**：`verify_seg` 第 2 道校验补 SEG 对象自身 `Modality == SEG` 独立核对（恒为 SEG）；源影像模态从 `ReferencedSeriesSequence.ReferencedInstanceSequence[].ReferencedSOPClassUID` 取回（DX roundtrip 测试断言为 DX Storage）。

## 测试数字

- 全量：后台跑 `tests/` 带 `--junitxml=/home/gogvf/data/BodyDonor/tmp/m7-s3r2-junit.xml`（结果见文末补记）。
- 分组跑（本轮改动直接影响）：
  - `test_distill_license_gate.py test_teacher_registry.py test_license_whitelist.py test_m5e.py`：**84 passed**
  - `test_export_seg.py test_source_instances.py test_m7_s3f_modality.py`：**35 passed**
- ruff：改动的 7 文件 **All checks passed!**
- 导入模块核实：`PYTHONPATH=~/work/BodyDonor-S3R/src`，`import bodydonor` 指向 worktree src（分组跑全部用该环境）。
- 本地真实数据测试（标注 `@pytest.mark.local_artifacts` 的用例）：在本机跑会执行；CI 跳过——**本轮 CI 不跑真实数据用例，报告如实说明**。

### 新增测试钉住（④）

- `test_known_restrictions_substring_no_longer_decides`：known_restrictions 写"蒸馏阻断/禁止蒸馏"不改变判定（子串匹配退役）。
- `test_research_distill_blocked_and_unverified_block`：blocked/unverified 阻断（临时改内存条目，事后恢复，不动注册表文件）。
- `test_registry_36_entries_new_fields_complete`：36 条目三字段齐全逐项计数 (36,36,36) + 枚举合法。
- `test_validate_entry_rejects_bad_new_fields`：缺失/非法枚举/空 basis/非列表即拒绝。
- `test_research_profile_restricted_non_nninteractive_no_sa_label` 改写：synthseg/moose conditional 放行 + 条款写出；flexray CC-BY-NC 非 SA。

### 新增测试（⑥）

- `test_dx_source_stays_dx`：DX 源合成实例 Modality=DX + DX SOP Class。
- `test_cr_source_stays_cr`：CR 源保留。
- `test_xray_seg_roundtrip_preserves_source_info`：DX 源 SEG roundtrip——SEG 自身恒 SEG、源引用 SOP Class 为 DX Storage。
- `test_xray_internal_alias_maps_to_cr`（改写）：内部别名 "X-ray" 规范化 CR。

## 验收对照（计划 ③④⑥ "怎样算做完"）

| 验收项 | 结果 |
|---|---|
| ③ total 研究推理实测走通（本地 TotalSeg 权重在） | ✅ amos_0004 实测 117 类 128.7s |
| ③ 权重缺失/损坏返回技术原因非许可原因 | ✅ ready 拒绝 + nni FileNotFoundError 实测 |
| ③ 24 新任务 ready=False 不改 | ✅ 零改动 |
| ④ 子串匹配与标签表删除 | ✅ grep 证据：ontology.py 无 `any("蒸馏阻断" in ...)`、无 `_restricted_license_label` |
| ④ 36 条目新字段齐全且有依据 | ✅ 测试计数断言 (36,36,36) + 逐项 LICENSE-CHECK 章节 |
| ④ known_restrictions 不参与判定 | ✅ 测试钉住 |
| ⑥ DX 源 stays DX / CR stays CR / 源信息保留 | ✅ 3 新测试 + SOP Class 断言 |

## 未决问题

1. `research_distill` 当前无条目标 blocked——枚举保留，等法务明文禁止的条目（MOOSE/SynthSeg 法务澄清前维持 conditional）。
2. nnInteractive conditional 的语义=项目政策边界（修正产物不进蒸馏）而非许可禁止研究蒸馏——下游若要放开修正产物进研究蒸馏，需用户重定边界，本轮未动。
3. 全量测试 + JUnit XML 已实测完成（见文末补记）。
4. ⑥ 只做导出修正，未扩大到 X-ray 摄入全面重审（按任务定义）。

---

## 补记：全量测试结果

- 全量：**671 passed, 0 failed, 0 error, 0 skipped**（`tests/` 45 文件；JUnit XML 已存 `/home/gogvf/data/BodyDonor/tmp/m7-s3r2-junit.xml`，testsuite tests=671 failures=0 errors=0 skipped=0；pytest -q 100% 点数 + exit=0，日志 `/home/gogvf/data/BodyDonor/tmp/m7-s3r2-fulltest.log`）。
- 首次后台跑被 10m 时限 kill（管道化 pytest 输出全丢）——重启后直接写日志文件 + 2h 时限跑完，与记忆中教训一致。
- 本地真实数据用例（`@pytest.mark.local_artifacts`）在本机执行通过；CI 会跳过。
- 导入模块核实：worktree src（见上）。
