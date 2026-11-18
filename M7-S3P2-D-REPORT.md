# M7-S3P2-D 轮次报告（2026-11-18）

S3P2 终报 §4 六项未决用户拍板后的决定落定轮。基线 HEAD `1934e5e`，
分支 `feat/m7-s3-postmerge-2`。只改文档 + 注册表依据文字，零逻辑改动。

## 提交列表（6 提交，实测）

| 提交 | 内容 |
|------|------|
| a6551e0 | ① license_verified 字段方案接受记入判定层 docstring |
| 574a593 | ② synthseg/moose 两轴取值确认标记（moose 补注 + §9 share_alike 确认） |
| d85a859 | ③ vertebrae_body 消解论证确认记入 R4a 报告附录 |
| 4645a04 | ⑥ synthseg/moose 学生权重出站形态改隔离独立制品（§9 第 3 条 + 注册表头注记；含 E501 折行修复） |
| 457fa0f | 决定落定文档 M7-S3P2-DECISIONS.md（6 项逐项落点 + 验证数字） |
| 502d423 | 审查 2N 修复——测试 docstring 确认口径 + DECISIONS 行号更正 |

## diffstat（实测，1934e5e..HEAD）

```
5 files changed, 95 insertions(+), 7 deletions(-)
（审查修复后另加 tests/test_research_field_decision_table.py +1 -1 与
 M7-S3P2-DECISIONS.md 行号更正）
```

## 六项逐项证据

### ① license_verified 字段方案——已接受

- 落点：`src/bodydonor/ontology.py:609`（mask_allowed_in_distill_training
  docstring 研究路径准入规则段末行）："license_verified 字段方案：用户
  2026-11-18 已接受并保留。"（亲读确认在位）
- 代码零改动；快照 `tests/data/m2_decision_table.json` 36 处
  `research_distill` 保留不重做。

### ② synthseg / moose 两轴取值——已确认

- synthseg：`license_registry.py:251` R3b 预写句原样保留（亲读，
  git diff 核对 574a593 hunk 只字未动该句）。
- moose：`license_registry.py:283-284` 补"（用户 2026-11-18 确认）"。
- `LICENSES-THIRD-PARTY.md:446-447` share_alike 段"（标出请用户复核）"
  改"（用户 2026-11-18 确认）"。
- 历史轮次报告"暂定待用户复核"不动（确认前历史记录）。

### ③ vertebrae_body 消解论证——已确认，pending 项关闭

- 落点：`M7-S3P2-R4a-REPORT.md` 末尾 "## Addendum 2026-11-18：
  vertebrae_body 消解论证已确认"（亲读 tail 确认）。
- 证据冲突注记保留：known_restrictions / provenance.verified / 注册表
  vertebrae_body 条目零改动。

### ④ SA 不挡路由——无改动，实测确认

- 零代码改动（`git diff 1934e5e..HEAD -- '*.py'` 实测：非注释改动 10 行
  全部为 docstring/字符串字面量——头注记、moose 标记、① 确认句、测试
  docstring，无判定逻辑/准入行为改动）。
- 验证：受影响测试 4 文件 **109 passed in 0.91s**（实测，含
  test_teacher_registry.py nninteractive 路由放行断言），行为未变。

### ⑤ LICENSE-CHECK.md 归属选 a——无改动，决定只记于此

- 文件未进仓库状态保持；无任何文件改动，决定记于
  `M7-S3P2-DECISIONS.md` §⑤。

### ⑥ synthseg / moose 学生权重出站形态——隔离独立制品

- `LICENSES-THIRD-PARTY.md` §9 法务事实第 3 条（:435-439）改写为：
  synthseg/moose 蒸馏学生权重**不以 BY-NC-SA 分发**，隔离为独立制品并
  单列许可（SynthSeg 制品单列 FreeSurfer SLA v1.0；moose 制品单列
  CC BY 4.0）。
- `src/bodydonor/license_registry.py:13-18` 头部法务事实注记同步改写
  （原为现行旧口径断言"与 SynthSeg 蒸馏出的学生权重…须以 BY-NC-SA
  分发"）。
- **关键区分核实（亲读 §9 全文）**：第 2 条 nnInteractive（:432-434）
  维持"须以 BY-NC-SA 分发"原状，未写成"可不走 BY-NC-SA"；两类未混。
- **扫描结果**：`BY-NC-SA` × synthseg/moose 同现扫 src/tests/tracked
  文档，唯一残留为 test_research_field_decision_table.py:130 docstring
  （审查发现，已修）；其余命中均 nnInteractive 语境。历史报告
  M7-S3P2-*.md / M7-S3-*.md 不动。

## 验证数字（实测）

- 受影响测试（test_teacher_registry / test_distill_license_gate /
  test_ontology / test_research_field_decision_table）：**109 passed
  in 0.91s**（两轮实测一致，审查修复前后各一次）。
- ruff：改动 py 文件 All checks passed（期间修一处 E501 折行）。
- ④ 零改动核实：`git diff 1934e5e..HEAD -- '*.py'` 非注释改动 10 行，
  全部为 docstring/字符串字面量。

## 审查 findings 与处理

deep-reviewer 1 个（后台），结论 **2 Nit / 0 Blocker / 0 Warning**。
逐条 double-check（复现命令实测）：

| # | Finding | 判定 | 处理 |
|---|---------|------|------|
| 1 | test_research_field_decision_table.py:130 docstring 残留"报告标出请用户复核"（过期口径，唯一残留） | 复现属实（grep 唯一命中） | 已修：改"（用户 2026-11-18 确认）"，502d423 |
| 2 | M7-S3P2-DECISIONS.md ② 行号偏差（synthseg 实际 :251 非 :248-249；moose 实际 :283-284 非 :282-283） | 复现属实（grep 行号对照） | 已修：行号更正，502d423 |

审查未验证项由调用方补齐：④ 零改动（git diff 实测，见上）；109 passed
（实测两轮）。

## 未决清单

- 本轮 6 项决定全部落定，**无新增未决**。
- 既有无关项：worktree 内 `m7-s3p2-suzi-fulltest-junit.xml` 为上轮遗留
  untracked 产物，与本轮无关。
