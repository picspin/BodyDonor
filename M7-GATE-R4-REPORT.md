# M7-GATE-R4 轮次报告：④ deep-reviewer 加第四区域"验证器审查"

基线 R3 HEAD `eca9142`，分支 `feat/m7-gate-hardening`。本轮只做 ④（验证器审查区域），
工作树 `~/work/BodyDonor-gate-hardening`，未 push、未 merge、未开 PR，未碰 `~/work/BodyDonor-S2`。

## 提交列表

| commit | 内容 |
|---|---|
| e5891cc | feat(m7-gate): ④ deep-reviewer 加第四区域"验证器审查"——六个核查项+严重度指引+守卫测试 |

## diffstat（实测 `git diff --stat HEAD~1..HEAD`）

```
 .claude/agents/deep-reviewer.md     | 13 +++++++++++++
 tests/test_deep_reviewer_regions.py | 29 +++++++++++++++++++++++++++++
 2 files changed, 42 insertions(+)
```

## ④ 逐项证据

### 1. 新区域文本（.claude/agents/deep-reviewer.md）

- 位置：审查清单 5 项之后、`## 工作方法（对抗式并行深审）`（:44）之前，同级 `##` 节，标题
  `## 验证器审查（区域四：测试是否在糊弄）`（.claude/agents/deep-reviewer.md:31）。
- 六个核查项（deep-reviewer.md:35-40，每项"查什么 + 怎么认"）：
  1. **sleep 假装异步**（:35）
  2. **跳过断言**（:36）
  3. **测试写真实路径**（:37，引 M6-F 原案）
  4. **降低阈值**（:38）
  5. **放宽断言**（:39）
  6. **mock 冒充实测**（:40）
- 严重度指引（deep-reviewer.md:42）：写真实路径造成数据损坏 → blocker；断言缺失/无法失败 →
  blocker；阈值/断言放宽无依据 → warning；sleep 假装异步 → warning、时序竞态判 blocker。
- finding 格式：与现有清单完全一致（severity/file/line/evidence/suggestion/reproduction，
  severity 三档 blocker/warning/nit），未发明新字段。
- 未改动：现有清单 5 项、输出格式、`## 调用约定` 小节均未触碰。

### 2. 守卫测试（tests/test_deep_reviewer_regions.py）

- `test_agent_file_contains_validator_region`：断言 `## 验证器审查` 存在。
- `test_agent_file_contains_six_checkpoint_keywords`：断言六个核查项标题关键词 +
  `M6-F 原案` + `严重度指引` 共 8 个关键词。
- 实测：`PYTHONPATH=~/work/BodyDonor-gate-hardening/src ~/work/BodyDonor/.venv/bin/python
  -m pytest tests/test_deep_reviewer_regions.py -q` → **2 passed**；ruff → **All checks passed!**
- 防回归说明：守卫读工作树版 agent 文件，防止后续改动悄悄删区。

### 3. 一致性核对

- `grep -n "正确性\|许可与合规\|数据诚实性\|区域"` 于
  `scripts/deep_review_from_main.sh`：**无区域枚举**（exit 2 无匹配）。
- `post-review-to-pr.sh` 实际位于 `.claude/hooks/post-review-to-pr.sh`（任务给的 scripts/
  路径不存在）；grep 同词表：无区域枚举（仅 severity 字段处理，属 findings 去重/渲染逻辑）。
- 结论：本轮只改 deep-reviewer.md 本体，两份脚本无需改动。

## 演示验证（真跑审查，不进 pytest）

三个糊弄用例放在**临时分支** `tmp/fake-tests-demo`（从 e5891cc 开出），gate 分支从未包含
糊弄用例；验证完已删除分支与用例。

| 用例 | 文件 | 糊弄点 | 审查判定 |
|---|---|---|---|
| (a) | tests/test_demo_fake_a.py | `time.sleep(0.1)` 后 `assert True` 假装异步 | **命中** |
| (b) | tests/test_demo_fake_b.py | 直接读写真实 `config/qc_thresholds.yaml`，无断言 | **命中** |
| (c) | tests/test_demo_fake_c.py | `assert dice > 0.1` 替代 `> 0.8`，阈值放宽 | **命中** |

**演示特例说明（为什么绕过包装脚本）**：包装脚本 `deep_review_from_main.sh` 的规则只从 main
提取（`git show main:.claude/agents/deep-reviewer.md`，scripts/deep_review_from_main.sh:49），
main 上没有本轮新区域——用包装脚本演示等于拿旧规则审新区域，必然测不到。所以演示改为直接派
deep-reviewer 子代理并把工作树新版 agent 文件全文贴进 prompt。这是演示特例；正式审查（下节）
仍走包装脚本。子代理的结论也如实标注了"未经 deep_review_from_main.sh 发起，不生成
reviews/review-<sha>.json"——调用约定自我执行正确。

糊弄用例先实测跑通（3 passed，证明能骗过 pytest），再送审。审查返回 **3 条 blocker
findings，三个用例全部命中**：

- (a) tests/test_demo_fake_a.py:5 — sleep 后 `assert True` 恒真断言，无法失败。
- (b) tests/test_demo_fake_b.py:7 — 直接改写真实 `config/qc_thresholds.yaml` 无 tmp_path
  无断言；审查员还实测指出当前配置无 `calibrated_on: 20` 子串故 replace 是空操作、但 mtime
  被改写——比派遣给的糊弄点更细。
- (c) tests/test_demo_fake_c.py:3 — 硬编码 `dice=0.55` 配 `> 0.1` 恒通过，注释自证阈值
  0.8→0.1 放宽无依据。

## 正式审查（R2 包装脚本）

- 路径：`bash scripts/deep_review_from_main.sh "HEAD~1..HEAD"`（R2 落地的唯一入口），base sha
  `e5891cc`，1 个 deep-reviewer 子代理。包装脚本按 R2 语义从本地 main 提取规则（本轮对
  deep-reviewer.md 的新区域在 merge 进 main 前不进入注入 prompt——已知语义非 bug；新区域
  有效性由上节演示验证证明）。脚本实测输出：`review_sha=e5891cc…`、`WARNING: 本地 main
  (d7b5c44) != origin/main (421ef0c)，规则取自本地 main`、`OK: reviews/review-e5891cc….json`。
- findings 文件：`reviews/review-e5891cc24d6128a5cc253f2b1bbd7c929781ec45.json`，**2 条 nit**
  （region="HEAD~1..HEAD"，生成时 stale=false、HEAD 尚在 e5891cc——findings 的读与 double-check
  均在该窗口内完成）。d295b98 修复提交后补跑 `scripts/check_review_fresh.py
  reviews/review-e5891cc….json` → **STALE exit 1**（HEAD 已移，按 R3 契约行为工作，findings
  引用已在本报告内完成）。

| # | severity | 位置 | 内容 | 处理 | 依据 |
|---|---|---|---|---|---|
| 1 | nit | tests/test_deep_reviewer_regions.py:17 | 第二个测试只用子串 in 判断关键词，不校验六项存在于区域内、也不校验关键词与核查项关联 | **不修** | 审查员实测+本轮复现：八个关键词在整份 agent 文件仅出现于区域四（`grep -c`：sleep 假装异步 2 次均区内=核查项+严重度指引，其余各 1）；变异实测：删除 `mock 冒充实测` 守卫即 FAIL——子串检查已具守卫证明力；审查员自标"非必需修复" |
| 2 | nit | tests/test_deep_reviewer_regions.py:28 | 元组右括号 `):` 缩进 3 空格，与文件其余 4 空格不一致（语法有效，纯风格） | **已修**（d295b98） | cat -A 复现属实（本实施方写测试时缩进手误）；修复后 pytest 2 passed + ruff All checks passed! |

## 未决清单

- 无功能性未决。两条 nit 已处理（1 修 1 不修，理由见上表）。
- 本轮对 deep-reviewer.md 的新区域在 merge 进 main 后才会进入 `deep_review_from_main.sh`
  的注入 prompt（R2 已记录的语义）；PR 合并前，正式审查对区域四的覆盖依赖演示验证路径。

