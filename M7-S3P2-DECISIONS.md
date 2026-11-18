# M7-S3P2 决定落定（2026-11-18 用户拍板 6 项）

用户 2026-11-18 23:18 对 S3P2 终报 §4 六项未决全部拍板。本轮（M7-S3P2-D）把
6 项决定落进仓库（文档 + 注册表依据文字），不改判定逻辑、不改准入行为。
基线 HEAD `1934e5e`。

## ① license_verified 字段方案——已接受

- **决定原文**：`license_verified: bool` 字段正式接受并保留。
- **落点**：`src/bodydonor/ontology.py:609`（`mask_allowed_in_distill_training`
  docstring 研究路径准入规则段末行）："license_verified 字段方案：用户
  2026-11-18 已接受并保留。"
- **不改代码**：字段 R3b 已落地，本轮只记接受。快照
  `tests/data/m2_decision_table.json` 的 36 处历史字段名 `research_distill`
  保留不重做（终报未决 7 已说明非必需）。
- **提交**：a6551e0。

## ② synthseg / moose 两轴取值——已确认

- **决定原文**：synthseg `share_alike=False`（SLA 非 ShareAlike 许可）、
  moose 无 SA（训练数据含 NC 部分是独立 caveat 不进 SA 轴）——两轴取值
  与 R3b 预写的 restriction_basis 一致，用户确认。
- **落点**：
  - synthseg：`src/bodydonor/license_registry.py:251` R3b 预写句
    "无 SA（SLA 非 ShareAlike 许可——用户 2026-11-18 确认）"——保留原句不动。
  - moose：`src/bodydonor/license_registry.py:283-284` 补"（用户 2026-11-18 确认）"。
  - `LICENSES-THIRD-PARTY.md:446-447` share_alike 段"（标出请用户复核）"
    改"（用户 2026-11-18 确认）"。
- **历史轮次报告不动**：R3b/终报的"暂定待用户复核"是确认前历史记录。
- **提交**：574a593。

## ③ vertebrae_body 消解论证——已确认，pending 项关闭

- **决定原文**：NC 框架下无商业暴露、pending 项直接消解的论证确认，pending 关闭。
- **落点**：`M7-S3P2-R4a-REPORT.md` 末尾 "## Addendum 2026-11-18：
  vertebrae_body 消解论证已确认" 一节。
- **证据冲突保留**：`known_restrictions` 与 `provenance.verified` 原文不动，
  注册表 vertebrae_body 条目不动。
- **提交**：d85a859。

## ④ SA 不挡路由——无改动，决定只记于此

- **决定原文**：SA 不挡路由，维持 R4a-fix 零政策变化状态（SA 只作放行
  义务注记）。代码已是该行为，本轮零改动。
- **验证**：`tests/test_teacher_registry.py`（含 nninteractive 路由放行断言）
  实测 109 passed（四文件合计，含 test_distill_license_gate.py /
  test_ontology.py / test_research_field_decision_table.py），行为未变。

## ⑤ LICENSE-CHECK.md 归属选 a——无改动，决定只记于此

- **决定原文**：该文件不进仓库，商业审计内容继续以 gitignored 内部附件
  `COMMERCIAL-CHECK-internal.md` 存在。无任何文件改动。

## ⑥ synthseg / moose 学生权重出站形态——隔离独立制品

- **决定原文**："BY-NC-SA 偏严格了，建议隔离独立制品。"
- **落点**：
  - `LICENSES-THIRD-PARTY.md` §9 法务事实第 3 条（:435-439）改写：
    SynthSeg 蒸馏学生权重**不以 BY-NC-SA 分发**，隔离为独立制品并单列许可
    （SynthSeg 制品单列 FreeSurfer SLA v1.0；moose 制品单列 CC BY 4.0）。
  - `src/bodydonor/license_registry.py:13-18` 头部法务事实注记同步改写
    （原"与 SynthSeg 蒸馏出的学生权重…须以 BY-NC-SA 分发"为现行旧口径断言）。
- **关键区分**：§9 第 2 条 nnInteractive（:432-434）维持"须以 BY-NC-SA
  分发"原状——其 SA 义务是许可证自身要求，隔离是满足方式；未在 §9 把
  nnInteractive 写成"可不走 BY-NC-SA"。全文自读复核两类未混。
- **扫描结果**：`BY-NC-SA` 现行断言扫 src/tests/tracked 文档，其余命中均为
  nnInteractive 语境（注册表 :190-220 nni 条目、:577 weights、distill_data
  拒绝理由、测试 SA 断言），无 synthseg/moose 旧口径残留。历史轮次报告
  M7-S3P2-*.md 不动。
- **提交**：4645a04。

## 验证数字

- 受影响测试（4 文件）：**109 passed in 0.94s**（实测）。
- ruff：`src/bodydonor/license_registry.py` + `src/bodydonor/ontology.py`
  All checks passed（实测；期间修一处 E501 折行）。
