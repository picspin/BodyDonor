# M7-S3P2-R4b 轮次报告——⑦ 术语纠偏

日期：2026-11-18。基线：R4a-fix HEAD `c1b6d87`（⑥ 已拆 + SA 挡项已纠偏）。分支 `feat/m7-s3-postmerge-2`（worktree），未 push、未 merge、未开 PR。

## 状态

**⑦ 完成。清查实测：零处 NC 成分被表述为 "open source"——如实零改动文件，守卫测试固化清查结论。** 2 提交，改动文件 1 个（tests/test_license_whitelist.py，+78 行净增），受影响测试全绿，ruff 全绿。

## 提交列表（c1b6d87..HEAD）

| 提交 | 内容 |
|---|---|
| `6d5a557` | test：⑦ 术语守卫——全仓 NC 成分 open source 表述清查零残留回归断言（+68） |
| `5786f54` | fix：审查 F1/F2 修复——自跳过改行级豁免（动态白名单）+ 扫描改 git ls-files（+44/−34） |
| `71a8699` | docs：本报告 + 审查 findings 落盘（+119） |

diffstat（实测，c1b6d87..HEAD）：`tests/test_license_whitelist.py | 78 ++++++++++`，1 file changed。

## ⑦ 逐项证据

### 清查（先全仓再改，一个不漏）

检索词："open source"、"open-source"、"opensource"、"开源"、"Openly available"、"openly available"（含大小写不敏感、连字符变体 `open.?source`）。扫描范围：worktree 全仓文本（--exclude-dir=.git/.venv/.pytest_cache/.ruff_cache/__pycache__，grep -I 跳过二进制）+ plans 目录（~/work/bodydonor-plans/，含三份 license 文档与全部轮次报告/派遣文件）。

**命中总数（终态实测）：worktree 18 行、plans 10 行**，逐文件：

| 文件 | 行数 | 分类 | 处理 |
|---|---|---|---|
| LICENSES-THIRD-PARTY.md | 6 | (a) 上游 Apache-2.0 README 逐字引用（§1.2 55/63、§8 371/392，"Openly available for any usage"——total/total_mr 权重原文，表述准确）+ (b) 仓库自身分层（397「开源层 Apache 2.0」）+ (c) 236 行引用 README 原文佐证商用安全路径 | 保留 |
| M0-REPORT.md | 1 | (a) 22 行 M0 核实记录引用上游 README 原文 | 保留 |
| proposal.md | 3 | (c) 16/20/33 行：仓库自身 Apache-2.0 层（分层开源 Q2 已定），与 NC 成分无关 | 保留 |
| src/bodydonor/license_registry.py | 3 | (a) 98/116 行 docstring 引用上游清单原文 + 561 行 `"usage": "any (openly available for any usage)"` 注册表事实字段（Apache-2.0 total 任务，表述准确） | 保留 |
| M7-S3P2-R4b-REVIEW.md | 5 | 本轮审查报告自身（findings 引用检索词与复现行文） | 保留（报告产物） |
| plans/（10 行） | — | s3p2-r4b-dispatch / s3p2-r4-dispatch（派遣文自身）+ M7-LICENSE-CHECK.md（上游 README 逐字引用 118/122）+ M7-PLAN-v3-research.md 等其余文档零命中 | 保留（plans 仓不在本 worktree 改动范围） |

其余 plans 文档（M7-PLAN-v3-research / M7-PLAN-v3.2-ANALYSIS / NEXT-ITERATION-PLAN / M6-PLAN）与全部非 md 扩展（*.py/*.txt/*.yaml/*.json/*.html/*.sh/*.js/*.ini/*.lock/*.gitignore，git ls-files 188 文件扩展名普查）**零命中**（grep + Python `re.findall` 交叉实测）。

### 结论

**零处 NC 许可成分被表述为 "open source"**（(a) 类改写为零）。既有命中全部属于两类：上游 Apache-2.0 权重的准确引用与仓库自身 Apache-2.0 层的中文表述——按派遣「若清查结果是零处 NC 相关表述，报告如实写零处、附清查证据，不许为凑数而改不该改的」执行。NC 成分在仓内的既有表述已是正确口径：LICENSES-THIRD-PARTY.md §2/§8（"CC BY-NC-SA 4.0（非商业，ShareAlike）"、"custom (free for non-commercial)"）与 src/bodydonor/license_registry.py LICENSE_FACTS（nninteractive.weights `"usage": "interactive correction only; outputs must not enter commercial distillation training"`、totalsegmentator.licensed_tasks `"usage": "non-commercial only"`）。

### 守卫（清查结论固化为回归断言）

`tests/test_license_whitelist.py::test_no_nc_component_described_as_open_source`（`5786f54` 终态）：

- 正则 `"open[" r"\s" "-]?source|open" "source|开" "源"`（IGNORECASE，字面量拆开避免定义行自命中）——抓 "open source"/"open-source"/"opensource"/"开源" 相邻组合，不碰上游 "Openly available" 引文。
- 扫描：`git ls-files` 受跟踪文件 × `_SCAN_SUFFIXES`（md/py/txt/yaml/yml/json/html/rst/cfg/toml/sh/js/ini/mako/lock/gitignore）× 跳过 .git/.venv/.pytest_cache/.ruff_cache/__pycache__。
- 白名单：`_ALLOWED_LINE_REFS` 按行号从真实文件动态读取——LICENSES-THIRD-PARTY.md:397（仓库分层原文）+ proposal.md:16/20/33（仓库自身 Apache-2.0 层）。
- 解码失败记为 violation，不静默。

**测试数字（实测）**：tests/test_license_whitelist.py **9 passed**（8 既有 + 1 新守卫）；与 tests/test_distill_license_gate.py 同跑 **59 passed**；`ruff check tests/test_license_whitelist.py` All checks passed。

**守卫行为探针（实测后清理）**：
- 守卫文件内追加 NC 表述注释行 → **failed（抓住）**——F1 修复生效，将来写进本文件的违规表述不逃逸。
- 未跟踪 tmp_scan/probe.md（NC "open source"）→ **passed（不扫未跟踪文件）**——F2 修复生效，本地与 CI 行为一致。
- 两探针均已清理，工作树 clean。

## 审查 findings 清单与处理

deep-reviewer 1 个子代理（只读，无 Bash），6 findings（Major 2 / Minor 3 / Nit 1），逐条 double-check 均带调用方实测复现；全文见 `M7-S3P2-R4b-REVIEW.md`。

| # | 级 | 声称 | 核实与处理 |
|---|---|---|---|
| F1 | Major | 整文件自跳过使守卫文件内违规表述逃逸 | **实测复现属实，已修**（`5786f54`）：删整文件跳过，改字面量拆开 + 动态白名单 + 措辞改写，自身零命中；探针复测守卫生效 |
| F2 | Major | rglob 扫未跟踪产物，本地与 CI 不一致 | **实测复现属实，已修**（`5786f54`）：改 git ls-files；探针复测一致 |
| F3 | Minor | 正则漏抓 OSI-approved/折行/连字符变体 | **核实后不采纳**：OSI 子串误伤普通单词（LICENSES-THIRD-PARTY.md:184 "rep**osi**tory" Python 实测命中）；折行/连字符属过度设计 |
| F4 | Minor | _SCAN_SUFFIXES 缺扩展名 | **核实属实（零命中），已修**（`5786f54`）：补 .sh/.js/.ini/.mako/.lock/.gitignore，全类型实测零命中 |
| F5 | Minor | 解码失败静默跳过 | **属实，已修**（`5786f54`）：记为 violation |
| F6 | Nit | line 184 命中来源不明 | **闭环**：命中即 "rep**osi**tory" 子串，非术语表述 |

## 未决清单

- **无阻塞未决。** ⑦ 清查零处 NC 相关表述，守卫已固化；审查 6 findings 全部闭环（4 修 1 不采纳 1 闭环）。
- 非本轮范围：plans 仓（~/work/bodydonor-plans/）内派遣文自身含检索词，属协调方文件，不在 worktree 改动范围；M7-LICENSE-CHECK.md 的上游引用为准确表述无需改。
- 上游 README "Openly available for any usage" 引文按派遣逐字保留——若上游将来改口径（如改用 source-available 表述），引用需随上游更新重扫（LICENSES-THIRD-PARTY.md §1.2/§8 访问日期 2026-11-06/11-08/11-13）。
