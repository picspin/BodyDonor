# M7-S3P2 R4a 轮次报告——⑥ ready 拆就绪三分

2026-11-18。基线 R3b HEAD `eb0183b`，本轮 8 提交 `8505bc5..dcc9c6d`（含本报告提交）。
只做本项 ⑥；⑦ 另派不在本轮。

## 提交列表

| 提交 | 内容 |
|---|---|
| `8505bc5` | 注册表 ready 布尔退役换就绪三分——weights_present/class_map_present 推导 + check_task 拒绝原因写明挡项 |
| `e532105` | 注册表测试迁移就绪三分 + total 权重实测记录 + 快照 nninteractive route 变更明示 |
| `3aede7f` | 新增就绪三分测试组——推导边界/挡项原因三分/36 条目逐条/src 零残留守卫 + class_map_present 缺 ref 边界修复 |
| `15b6498` | 测试 fixture ready 键清零 + whitelist 测试 docstring 就绪三分口径同步 |
| `74b6207` | nninteractive_backend docstring 就绪三分口径同步（W-7 已知缺口叙述随字段化迁移） |
| `5a4253e` | 审查 W1 修复——check_task 挡项原因三元表达式优先级陷阱改列表收集 |
| `2bb6854` | 审查 findings 文件（M7-S3P2-R4a-REVIEW.md） |
| `dcc9c6d` | R4a 轮次报告（本文件） |

diffstat（实测）：6 files, +289/-58。

## ⑥ 逐项证据

### 1. 字段拆分（src/bodydonor/license_registry.py）

- `_REGISTRY_FIELDS` 删 `ready`（:38-52 区域，ready 行删除）；新增
  `READY_PLACEHOLDER_MARKERS = ("待建", "待落地")`（:57）与两个推导函数：
  - `weights_present`（:68-70）：`weight_integrity.status == "verified"`（sha256
    已核实）→ True；not_recorded/其他 → False。规则写死进模块 docstring（:17-27
    就绪三分区）与函数 docstring，全仓统一。
  - `class_map_present`（:73-78）：class_map_ref 非空且不含「待建」/「待落地」
    → True。缺 ref（None/缺失键）→ False（bool(cls_ref) 守卫——`str(None)='None'`
    不含占位标记的漏洞，`test_class_map_present_derivation_boundaries` 失败测试
    先行暴露）。
  - `share_alike`：既有字段（M7-S3P2 ⑤）复用不重建。
- 12 条目 `"ready": True/False` 键全删（perl 整行删除，grep 零残留）。
- `total` 条目 `weight_integrity` 实测记录（:99-107）：sha256
  `3489323a4b759506ab46b489eb78b72142f2143aea6e6d828604d754bd7a9270`、
  size_bytes 164951587、status=verified、checkpoint_path 本机 Dataset298_total_6mm
  （2026-11-18 实测；审查 sha256sum 复跑一致）。依据：S3 W4 nnInteractive 先例
  同法（权重实际已部署就实录）；S3R2 ③ total 研究推理实测已走通（n_classes=117）。
  **其余 34 条目权重未部署/未核实，一律 not_recorded 不造假。**

### 2. check_task 读三字段（src/bodydonor/license_registry.py:604-667）

- 就绪判定（:642-645）：`weights_present(e) and class_map_present(e) and not
  e["share_alike"]`——三字段齐才放行。
- 拒绝原因写明挡项（:646-661）：逐条列表收集 + set 去重，原因字符串含
  「缺权重」/「缺 class_map」/「SA 义务」逐项（审查 W1 修复后的最终形态）。
- 实测拒绝原因：`body_mr` → "挡项: 缺权重"；占位 class_map 合成条目 →
  "挡项: 缺 class_map"；nninteractive 形态 → "挡项: SA 义务"。

### 3. validate_entry 占位守卫迁移（:511-515）

W16 守卫语义移路由侧：ready 布尔退役后旧「ready=True × 占位」矛盾不再可表达，
占位 class_map_ref 登记仍合法（13 个未就绪条目即此形态），占位 → 派生
class_map_present=False → check_task 拒绝派发。占位类映射永远不能当作在位的
语义不变（`test_placeholder_class_map_ref_blocked_at_routing_both_wordings`
两种措辞都断言路由拒绝）。

### 4. 快照 nninteractive route 变更（tests/data/m2_decision_table.json）

`[True, "ok+license_inherits"]` → `[False, "blocked:not-ready"]`：⑥ 预期语义
变更——nninteractive share_alike=True，就绪三分下 check_task 以 SA 义务挡。
生产实害论证：nninteractive_backend.py:183-190 生产修正路径（correct_image /
_run_locked）未接注册表门控（S3R4 W-7 已知缺口，docstring 明示），check_task
只覆盖 TS 教师路径；蒸馏防线在数据侧（sidecar source + ontology 判定拒绝）。
其余 35 条目 route 判定零变化（对照脚本实测：changed count = 1）。

## 拆分后状态表

### 5 个缺 class_map 的任务（用户指的最可能是 MR 组 5 个）

对照 S3 收口口径（S3R1-REPORT.md:130 ③ 任务定义 + S3R2 ③ 验证）：MR 组 5 个
为纯技术缺失（class_map 待落地 + 权重未下载），license_verified=True、
share_alike=False，除 class_map/权重外无其他阻断：

| 条目 | weights_present | class_map_present | share_alike | 其余字段 |
|---|---|---|---|---|
| totalsegmentator/total_mr | False（未下载） | False（TS MR 50 类待落地） | False | license_verified=True |
| totalsegmentator/body_mr | False | False | False | license_verified=True |
| totalsegmentator/vertebrae_mr | False | False | False | license_verified=True |
| totalsegmentator/liver_segments_mr | False | False | False | license_verified=True |
| totalsegmentator/liver_lesions_mr | False | False | False | license_verified=True |

### 其余 8 个含占位 class_map 的条目（除 class_map 外另有阻断）

| 条目 | weights_present | class_map_present | share_alike | 其他阻断 |
|---|---|---|---|---|
| mri_core/feature_encoding | False（vitb 未下载） | False（无 class_map_ref，feature 教师） | False | feature 教师不进 mask 投票 |
| synthseg/brain_mri_qc | False | False（待建） | False | FreeSurfer SLA 条款继承（output_license_inherits 非空） |
| moose/moose_qc | False | False（待建） | False | CC BY 4.0 条款继承 + NC 训练数据 caveat |
| nnunet_v2/framework | False | False（无 class_map_ref，framework） | False | 无权重可蒸馏（license_verified=False） |
| flexray/flexray_xray | False | False（待建） | False | CC BY-NC 4.0 NC 条款继承 |
| totalsegmentator/teeth | False | False（待落地：2.10.0 不含） | False | license_verified=False（单证据） |
| totalsegmentator/trunk_cavities | False | False | False | license_verified=False |
| totalsegmentator/liver_lesions | False | False | False | license_verified=False |
| totalsegmentator/vertebrae_pp | False | False | False | license_verified=False |
| totalsegmentator/vertebrae_pp_refined | False | False | False | license_verified=False |

（注：协调方实测占位 ref 条目 13 个 = MR 组 5 + synthseg/moose/flexray 3 +
未核实 TS 5；mri_core/nnunet_v2 两条 feature/framework 条目无 class_map_ref
字段、class_map_present 派生 False——同一推导规则覆盖，一并列出。）

### vertebrae_body 三方分歧案

拆分后三字段：`weights_present=False`（未下载）、`class_map_present=True`
（`ts-classes/v1 (TS 2.10.0 map_to_binary.class_map)` 在位，license_registry.py
vertebrae_body 条目）、`share_alike=False`。license_verified=False（保持）。

- **曾经挡住它的是什么**： NC 框架下
  曾按安装包实况登记 unverified（commercial_models 含 id 305 需 license 与
  README open 清单出入，M7-S3F ② 实测）。
- **商业暴露如何消解**：法务事实（用户 2026-11-17 任务给出，R3b 报告 §9）：research
  只化解 NC 条款不化解 ShareAlike；NC 框架下权重禁止商业目的使用——本仓蒸馏
  训练集与研究路由均非商业用途，安装包 id 305 的商用 license 要求不构成 NC
  框架下的暴露，pending 项直接消解。** NC 框架论证请用户复核**（R3b 未决项 3
  的收口）。
- **证据冲突保留不抹掉**：known_restrictions 里「安装包 commercial_models 含
  该任务（id 305，需 license）：非商用走 academic license 渠道；与 README open
  清单出入已注记」与 provenance.verified「安装包实测 commercial_models
  ['vertebrae_body']=305（需 license）；README open 清单（访问 2026-11-06）
  称 open——按安装包实况登记 unverified」原文未动，包内与清单出入作为文档注记
  保留（`test_allowed_tasks_registered_tasks_route_via_registry` 断言
  commercial_models 字样仍在 known_restrictions）。

### ready=True→拆分后保持可用的条目（2 个）

| 条目 | weights_present | class_map_present | share_alike | check_task |
|---|---|---|---|---|
| totalsegmentator/total | True（本实测 sha256） | True | False | 放行（"allowed"） |
| nninteractive/interactive_correction | True | True | True（CC BY-NC-SA） | 拒绝（"挡项: SA 义务"——生产路径不过 check_task，无实害） |

## 测试（数字实测）

- tests/test_teacher_registry.py：**31 passed**（迁移 4 测试 + 新增 5 测试 +
  既有 22）。新增：
  - `test_weights_present_derivation_boundaries`（verified/not_recorded/缺失键）
  - `test_class_map_present_derivation_boundaries`（无标记/待建/待落地/缺失键）
  - `test_check_task_rejection_reason_names_blocked_item`（缺权重/缺 class_map/SA 三分）
  - `test_check_task_rejection_reason_combinations_all_named`（审查 W1 失败测试
    先行：7 组合参数化，组合挡项逐项报全）
  - `test_all_36_entries_three_ready_fields_valued`（36 条目三字段逐条有值 +
    ready 键清零）
  - `test_src_no_ready_residual`（src 全树注册表语义 ready 零残留守卫；
    recalibration_ready 豁免）
  - `test_placeholder_class_map_ref_blocked_at_routing_both_wordings`（占位
    两种措辞路由拒绝）
- 受影响 6 文件合计 **150 passed**（test_teacher_registry + test_research_field_
  decision_table + test_license_whitelist + test_distill_license_gate +
  test_ontology + test_qc，8 passed/33 passed 面已含在 150 计数内）。
- test_nninteractive_backend.py 非 local_artifacts 面 **26 passed, 4 deselected**
  （docstring 同步后复跑；local_artifacts 用例涉真卡推理未跑，GPU 锁互斥）。
- ruff：改动文件全绿（license_registry.py、test_teacher_registry.py、
  test_license_whitelist.py、nninteractive_backend.py — "All checks passed!"）。
- 未跑全量套件（GPU 锁互斥；本项未触 GPU 路径代码）。

## 审查 findings 清单与处理

deep-reviewer 1 个（只读，无 shell——findings 见 M7-S3P2-R4a-REVIEW.md）：

- W1（属实已修，实施方实测复现 REPRODUCED）：check_task 挡项原因三元表达式
  优先级错误——`"缺权重" if not w else "" + "缺 class_map" …` 解析为嵌套 else
  分支，组合挡项只报缺权重（复现：wp=False/cp=False/sa=True 条目原因只含
  「缺权重」）。修复：改逐条列表收集 + set 去重（5a4253e）；失败测试先行
  `test_check_task_rejection_reason_combinations_all_named`（修复前 red）。
- Blocker 0 / N 0。
- 审查未完成核对项（审查者无 shell）由实施方补跑，全部通过（sha256 对照一致、
  快照变更论证成立、全仓 grep 零残留、36 条目测试覆盖确认、validate_entry
  无绕过路径）——见 REVIEW.md「实施方补跑实测」节。

## 未决清单

1. **license_verified 字段方案**（R3b 未决 1）仍待用户确认（本报告未改该字段）。
2. **synthseg/moose 两轴取值**（R3b 未决 2）仍待用户复核（本报告未改）。
3. **vertebrae_body NC 框架消解论证**：本报告按用户验收写明消解逻辑（NC 框架
   下无商业暴露），请用户复核确认后该 pending 项关闭。
4. **synthseg/moose 蒸馏学生权重出站许可形态**（R3b 未决 4）待法务澄清。
5. **快照 tests/data/m2_decision_table.json 历史字段名 research_distill**（R3b
   未决 5）：保留（快照读判定结果不读注册表字段），本轮 route 值更新一处
   （nninteractive），理由已在本报告 §4 明示。
6. **recalibration_ready（qc.py:88-89）**：数值重算抽检，另一概念，本轮未动
   （任务明令）；`test_src_no_ready_residual` 守卫已将其列为豁免。
7. **⑦ 全仓清查**：另派，不在本轮。
