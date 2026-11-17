# M7-S3P2 R3b 轮次报告——⑤ 两条轴替代原三档

2026-11-18。基线 R3a HEAD `a135fbd`，本收口 13 提交 `fb960aa..a49eea5`（含本报告提交）。
只做本项 ⑤；④ 的删除未动。

## 最重要：两轴不能单独编码准入，须加 license_verified（请用户确认）

许可两轴（attribution_required / share_alike）描述的是许可义务，**不能单独
编码准入状态**：准入未核实的条目与 allowed 条目同为 Apache-2.0（两轴值完全
相同），准入差别只在证据强度（单一来源 vs 双证据）。诚实做法是注册表加小
字段 `license_verified: bool`，准入规则显式写进判定层 docstring：

- license_verified=False（含无权重条目 nnunet_v2/framework）→ 阻断待核实；
- license_verified=True 且研究可用 → 放行，义务按两轴保留（share_alike=true
  的派生物按 output_license_inherits 保留 SA 条款——conditional 语义由该
  继承字段承接）。

**请用户确认是否接受 license_verified 字段**。若不接受，备选是把准入快照
留在决策表测试里（不推荐：准入状态散在测试断言而非数据，注册表 status
报告与 API 看不到，新增条目无校验可依）。

## 新旧判定对照表（零静默变化，实测确认）

快照 tests/data/m2_decision_table.json 是旧方案 36 条目实测判定；迁移后
快照两测（test_route_decisions_unchanged / test_distill_decisions_unchanged）
通过 = 36 条目逐条一致。对照结构：

| 旧 research_distill | 条目数 | 新字段取值 | 新判定 |
|---|---|---|---|
| allowed | 25 | attribution_required=True, share_alike=False, license_verified=True | (True, "ok") 不变 |
| conditional | 4（nninteractive/synthseg/moose/flexray） | 同上两轴，但 nninteractive share_alike=True；4 条 inherits 均非空 | (True, "ok+license_inherits") 不变 |
| unverified | 7（teeth/trunk_cavities/liver_lesions/vertebrae_pp/vertebrae_pp_refined/vertebrae_body/nnunet_v2-framework） | license_verified=False | (False, "blocked:unverified") 不变 |

无任何条目新旧判定不一致；route 路径未改，零变化。

## 提交列表

| 提交 | 内容 |
|---|---|
| fb960aa | 注册表 research_distill 三值枚举退役换许可两轴+license_verified（36 条目 + validate_entry + M-1 换 share_alike 强制 inherits） |
| 9dbcc36 | 判定层迁移准入规则显式——license_verified 承载证据强度，义务按 inherits 保留 |
| 249c94c | distill_data 注释口径同步（纯注释，判定委托 ontology 函数不改逻辑） |
| eb86cd3 | 准入未核实拒绝措辞保留 unverified 语义词（_reason_key 分类依赖） |
| 1b1fc82 | 决策表测试单一来源断言换两轴+license_verified |
| bc1b3b5 | 门禁测试跟进（unverified 单态 / 转换表计数 / 五字段校验 / M-1 重命名） |
| 729c235 | 注册表测试 fixture 与 by_task 断言跟进 |
| b06a3d2 | 补 SA 两轴语义（nnInteractive/SynthSeg）与 src 残留守卫测试 |
| 0a789f4 | LICENSES-THIRD-PARTY §9 法务事实与字段语义 |
| 897f2fa | 测试名与 docstring 历史口径叙述同步 |
| c802870 | 审查修复：两轴 int 校验收紧 + docstring 计数漂移更正 |
| cc948b2 | 审查 findings 文件 |
| a49eea5 | R3b 轮次报告（本文件） |

diffstat（实测）：9 files, +331/-120。

## ⑤ 逐项证据

### 1. 注册表 36 条目（src/bodydonor/license_registry.py）

- 字段集 `_REGISTRY_FIELDS`：research_distill 删除，换
  attribution_required/share_alike/license_verified（src/bodydonor/license_registry.py:21-33）。
- 枚举：`ATTRIBUTION_REQUIRED_VALUES=(True,)`、`SHARE_ALIKE_VALUES=(True, False)`
  （:44-46）；旧 `RESEARCH_DISTILL_VALUES` 删除。
- 条目按协调方转换表机械改字段：25 Apache-2.0（total/total_mr 组 6、mri_core、
  组二 18）= 署名无 SA；nninteractive = share_alike=True + inherits 含
  CC-BY-NC-SA 条款；synthseg（SLA 非 ShareAlike，协调方派遣暂定、待用户复核）、
  moose（训练数据 NC caveat 不进 SA 轴）、flexray（CC BY-NC 4.0 非 SA）=
  署名无 SA；7 条 license_verified=False（5 单证据 + vertebrae_body 出入 +
  framework 无权重）。每条目 restriction_basis 补两轴取值依据一句。
- validate_entry（:415 起）：三 bool 校验（attribution_required 恒 True 且
  isinstance(bool)、share_alike isinstance(bool)、license_verified
  isinstance(bool)）；S3R4 M-1 的 conditional 检查换 share_alike 强制
  output_license_inherits 非空（:436-443）。

### 2. 判定层（src/bodydonor/ontology.py:592 mask_allowed_in_distill_training）

- 准入只读 license_verified（:662-669 `all(e["license_verified"])`）；拒绝
  reason 含 unverified 语义词（_reason_key 分类依赖，:675-681）。
- 义务保留：inherits 非空即列出条款（不再限定 conditional 标记，:683-689）；
  nnInteractive 修正产物既有硬拒分支不动（:614-636）。
- 准入三档写入 docstring（:599-608）。

### 3. 研究路径（src/bodydonor/distill_data.py）

- check_distill_source 判定委托 ontology 函数不变（:115-119）；仅 3 处注释
  口径同步（:50-51/:113-114/:446-447）。

### 4. 法务事实（LICENSES-THIRD-PARTY.md §9 + 注册表 docstring）

- 用户 2026-11-17 任务给出四条法务事实：research 只化解 NC 不化解 ShareAlike；
  nnInteractive 与 SynthSeg 蒸馏学生权重须以 BY-NC-SA 分发；repo 出站纯 NC
  则隔离独立制品；软件内不另建合规门禁，依据只留文档。

### 5. 测试（数字实测）

- tests/test_research_field_decision_table.py：**8 passed**。快照两测零变化
  （36 条目）；test_two_axis_fields_single_decision_source（research_use 与
  research_distill 清零 + 两轴逐条有值）；test_share_alike_semantics_
  nninteractive_vs_synthseg（nnInteractive 恒 SA + 条款含 CC-BY-NC-SA、
  SynthSeg SLA 1(c)/(d) 条款、其余 34 条全无 SA）；test_src_no_research_
  distill_residual（src 全树零残留防回潮）。
- tests/test_distill_license_gate.py：**50 passed**（含 R3b 补 int 1/0 两
  失败 case）。test_license_unverified_blocks（license_verified=False 阻断、
  reason 含 unverified 与 license_verified）；test_registry_36_entries_new_
  fields_complete（三 bool + 转换表计数 share_alike=1 / license_verified
  False=7）；test_validate_entry_share_alike_requires_inherits（M-1 承接）。
- tests/test_teacher_registry.py：**25 passed**（fixture 三 bool 字段 +
  by_task license_verified 真/假断言）。
- tests/test_ontology.py：受影响测试随 103 passed 组跑通过（未核实条目阻断
  测试重命名 test_license_unverified_registry_entry_blocked）。
- 受影响 4 文件合计 **103 passed**（c802870 后复跑）；ruff 改动文件全绿。
  未跑全量套件（GPU 锁互斥，本项未触 GPU 路径代码）。

## 全仓 grep research_distill 残留清单

src 零残留。剩余触点全部列明：

- tests/data/m2_decision_table.json：快照 fixture，历史字段名保留（快照读的
  是判定结果，不读注册表字段）。
- 测试 docstring 历史口径叙述 + 清零断言/守卫文本（有意引用）：
  test_ontology.py:225 附近、test_distill_license_gate.py 若干 docstring、
  test_research_field_decision_table.py 断言文本。

## 判定不准请用户定的条目清单

1. **synthseg/brain_mri_qc**：FreeSurfer SLA v1.0 不是 ShareAlike 许可，按
   协调方派遣暂定填 share_alike=False（待用户复核）；蒸馏学生权重仍须以 BY-NC-SA
   分发（§9 法务事实 3）。请复核。
2. **moose/moose_qc**：权重 CC BY 4.0 非 SA，share_alike=False；训练数据含
   NC 部分（AutoPET 溯源未决）是独立 caveat，不进 SA 轴。请复核。
3. **vertebrae_body**：安装包 commercial_models（id 305，需 license）与
   README open 清单出入；两轴暂按 Apache-2.0（署名无 SA）填，license_verified=
   False 准入阻断，⑥ 轮再议 NC 框架下消解。
4. **nnunet_v2/framework**：框架条目不附带权重，两轴对其近乎 N/A；按
   Apache-2.0 填署名义务、license_verified=False，准入阻断（无可蒸馏之物）。

## 审查 findings 清单与处理

deep-reviewer 1 个（只读，逐条读码核对；findings 见
M7-S3P2-R3b-REVIEW.md）：

- W1（属实已修）：ontology.py:601 与 license_registry.py:8-9 docstring
  「5 个 unverified」按旧三值口径写，与 license_verified=False 实际 7 条
  漂移——改不带数量的描述（c802870）。
- N1（属实已修，复现 REPRODUCED）：attribution_required/share_alike 枚举
  比较 `in (True,)` 放过 int 1/0——改 isinstance(bool)，失败测试先行补进
  test_validate_entry_rejects_bad_new_fields（c802870）。
- Blocker 0。审查逐项核对：36 条判定等价（读码 + 调用方实跑快照两测）、
  validate_entry 无绕过（MOOSE 场景正确放行）、all() 语义等价（task 无
  重复）、无遗漏消费方、reason 子串全保留。

## 未决清单

1. license_verified 字段方案请用户确认（本报告最前节；备选与不推荐理由已写）。
2. synthseg/moose 两轴取值请复核（见上清单 1/2）。
3. vertebrae_body 出入与 nnunet_v2/framework 的 N/A 填法待 ⑥ 轮。
4. synthsense/moose 蒸馏学生权重的出站许可形态（BY-NC-SA 分发 vs 隔离独立
   制品）待法务澄清后落 §9 补录。
5. 快照 tests/data/m2_decision_table.json 含旧字段名 research_distill——快照
   是判定结果记录不随迁移改；若用户接受 license_verified 方案，下轮可考虑
   重做快照格式（非必需）。
