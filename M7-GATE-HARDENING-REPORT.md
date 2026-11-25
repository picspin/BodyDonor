# M7-GATE-HARDENING 终报：验证框架缺口四硬门控第一轮（BodyDonor）

日期：2026-11-19。协调方：gate-hardening 协调方（素子子代理）。分支 `feat/m7-gate-hardening`，worktree `~/work/BodyDonor-gate-hardening`，基线 `origin/main 421ef0c`（PR #4 已合）。**未 push、未 merge、未开 PR**（用户未批推本轮）。只做 BodyDonor，不碰 MRQlab。

指导原则（用户 2026-11-19 定）：先落验证框架缺口四的硬门控，在 BodyDonor 做第一轮；MRQlab 移植等这轮验证通过后再说。目标是把 M6-F 类问题（决策回流测试改写真实 `config/qc_thresholds.yaml`，事后才被发现）变成事前拦截。

## 1. 分支 / HEAD / 提交 / diffstat（协调方实测）

- 代码终态 HEAD：`a07f719`（R4 轮次报告提交）；本终报提交后 HEAD 再进一位（纯文档提交，见下）。
- 提交总数：`421ef0c..a07f719` 共 **20 提交**（实测 `git log --oneline | wc -l`）。
- Diffstat（实测 `git diff --shortstat 421ef0c..a07f719`）：**14 files changed, 1371 insertions(+), 0 deletions(-)**。14 文件：`.claude/agents/deep-reviewer.md`、`.gitignore`、4 份轮次报告、4 个新脚本（`scripts/deep_review_from_main.sh`、`scripts/check_review_fresh.py`、`scripts/git-hooks/commit-msg`、`scripts/git-hooks/README.md`）、4 个新测试文件。

### 轮次提交列表（实测）

| 轮次 | 提交 | 内容 |
|---|---|---|
| R2（②） | `58471bd` `1e2be2a` `a2d102d` `cac79bb` `18f0724` | 审查唯一入口脚本 + 调用约定 + 本地 main 滞后护栏 + 审查 8 条全修 + 报告 |
| R1（①） | `ff61f68` `7667f0d` `8e62d18` `5f53a6b` `b833b66` | commit-msg hook + scratch 仓 7 例测试 + 审查修复（rename/非 ASCII/SIGPIPE）+ README + 报告 |
| R3（③） | `a5d4894` `79e0798` `c98699f` `8b687b4` `85c5c79` `60f6b13` `eca9142` | 审查绑定 commit（schema + stale 两处判定 + 校验脚本）+ 两轮审查修复 + 报告回填 |
| R4（④） | `e5891cc` `d295b98` `a07f719` | 验证器审查区域 + 终审修复 + 报告 |

- 顺序说明：按 R2 → R1 → R3 → R4 执行。R2 先行是为了让 R1/R3/R4 的审查都用第 2 项落地后的"从 main 读规则"路径执行；R2 自身的审查用新建包装脚本 dogfood。
- 审查路径（全轮一致）：每轮审查均经 `bash scripts/deep_review_from_main.sh "<range>"` 执行，规则从 main 提取注入 prompt；终报写清路径的要求已满足（各轮次报告均有记录）。

## 2. 四项逐项证据

### ① pre-commit hook 硬隔离 config/（R1，HEAD b833b66）

- 脚本：`scripts/git-hooks/commit-msg`（32 行，mode 100755，已进 git）+ 安装文档 `scripts/git-hooks/README.md`（30 行）。安装：`git config core.hooksPath scripts/git-hooks`（版本化、每个 clone 设一次，无第二套并存）。
- 守卫逻辑（亲读全文）：merge 提交（`MERGE_HEAD` 存在）放行；`git diff --cached --name-only --no-renames -z` 读暂存列表写临时文件（避开 `$()` 吃 NUL 与大列表 SIGPIPE 两个坑）；`grep -aq '^config/' -z` 命中 → 从 `$1` 消息文件读非 `#` 注释行，`grep -qF '[config-change]'` 精确子串、大小写敏感；无标记 → exit 1，stderr 打印原因 + 放行方式 + "不是免审"说明。
- 落点选型（关键机制）：单个 commit-msg hook 同时查暂存文件 + 消息。pre-commit 阶段拿不到 commit message（git hook 顺序 pre-commit → prepare-commit-msg → commit-msg），"pre-commit 里读 COMMIT_EDITMSG"的写法被明确禁用；commit-msg 的 `$1` 天然给出消息文件，暂存列表在该阶段依然可读（七例实证）。
- 边界决定（均写进 README 与测试）：删除 config 文件算 config 变更须标记；merge 放行（拒绝会卡住 merge 状态无法安全回退）；`--amend` 同规则；注释行标记不认（git 剥离注释行）。
- 测试：`tests/test_precommit_config_guard.py`（135 行，10 例），scratch git 仓 + README 同一安装路径真跑 `git commit` 断言行为（拒/放行/删除/amend/注释行/rename 移出/非 ASCII/限制固化各一）。协调方独立重跑：**10 passed**；受影响 6 文件合批协调方独立重跑：**108 passed / 0 failed**（实施方 107 passed + 1 skipped，收集总数同为 108——那 1 个 skipif 门控测试在我这轮跑起来了；耗时我方 731.83s、实施方 60.43s，计数一致，耗时差未深究，见未决）。
- 审查（经包装脚本，review_sha=7667f0d）：7 findings（1B4W2N）。Blocker 是 rename 移出 config/ 逃检（`--name-only` 默认只列新路径），连同非 ASCII 引号转义、SIGPIPE 误放行两个 Warning 均为真问题，已修并先红后绿；另 2 条接受为已文档化限制（`--amend --no-edit` 复用旧标记、merge 夹带），1 条 Nit 未修（文档漂移加固，收益低）。

### ② deep-reviewer 规则从 main 读（R2，HEAD 18f0724）

- 包装脚本：`scripts/deep_review_from_main.sh`（115 行，可执行）。机制（协调方亲读全文）：`review_sha=$(git rev-parse HEAD)` 抓取打印；origin/main 护栏（本地 main 缺失报错、与 origin/main 不一致打 WARNING、规则文件有差异即 fatal）；`git cat-file -e main:<rule>` 存在性检查（缺失报错、不回退工作树版）；`git show main:...` 提取进临时文件（mktemp + trap 清理）；prompt 含显式优先权指令（main 文本为准、不要读工作树版 agent 定义）+ 审查范围 + 执行方式；调 `claude -p --dangerously-skip-permissions` 派 deep-reviewer；调用前 `rm -f` 防同 HEAD 残留复用；事后校验报告存在、合法 JSON、review_sha 匹配、findings 是数组。
- 规则集：只有 `.claude/agents/deep-reviewer.md` 一个（协调方已核实：`CLAUDE.md` 65 行全文无 review 相关字样；仓库内无 watcher 脚本；`post-review-to-pr.sh` 只发 PR 评论不调用审查）。
- 调用约定：`.claude/agents/deep-reviewer.md` 末尾加"调用约定"小节（只加不改现有清单/输出格式），并如实写明子代理自身的 name/tools/hook 仍由工作树版解析（审查 W5 修复后的表述）。
- 测试：`tests/test_deep_review_from_main.py`（236 行，8 例），scratch 仓 + `fake_claude` PATH 替身：main 版提取、main 无规则文件报错不回退、review_sha 与 JSON 落盘、同 HEAD 残留不复用、坏 JSON/坏结构拒绝、origin 规则分歧拒绝/仅警告。协调方独立重跑：**8 passed**；ruff 全绿；`bash -n` 与 `--help` 均通过。
- 审查（用新建包装脚本 dogfood）：8 findings（5W3N），逐条 double-check 全部属实，全修。

### ③ 审查绑定 commit（R3，HEAD eca9142）

- Schema（定死）：`reviews/review-<review_sha>.json` = `{review_sha, region, findings[{severity, file, line, evidence, suggestion, reproduction, resolved_in_commit}], stale}`。`resolved_in_commit` 是 per-finding 键（不是顶层——首轮测试曾按顶层写错已修正并记录）；`region` 定义为传给包装脚本的范围字符串；`stale` 顶层布尔。findings 沿用现有六字段，不增不减。
- 版本管理决定（协调方已定）：`reviews/` 是运行产物，**不进版本管理**（`.gitignore:16-18` 追加 `reviews/`；理由：与 JUnit XML 同类；审计轨迹是轮次报告 md + PR 评论；JSON 是供校验脚本读取的机器件）。
- 失效判定（两处落地，协调方亲读代码）：产出时——包装脚本校验后重读当前 HEAD，不一致即打印 `STALE: HEAD moved during review` 并把报告 `stale` 写成 `true`；读取时——新脚本 `scripts/check_review_fresh.py`（53 行），sha 相符且 stale 为 false 才报 `FRESH` exit 0，其余一律 `STALE` exit 1（缺失/非 JSON/顶层非 object 均 fail-closed）。
- 派遣约定：包装脚本 prompt 段带 base sha + 要求子代理回报写明审的 sha；`.claude/agents/deep-reviewer.md:69` 调用约定小节追加绑定约定（含"包装脚本退出码不代表新鲜"）。
- `post-review-to-pr.sh` 评估结论：**(b) 不改**。理由：PR head 即合并候选，commit status 就该打在 head 上（门禁信号）；stale 由 JSON + 校验脚本覆盖，两者语义不同；hook 已有 422 回退兜底。已接受限制：审查期间 HEAD 前移时 status 仍打在新 head（未被审的 commit）上。
- 测试：`tests/test_review_binding.py`（289 行，11 个 test 函数），scratch 仓 + fake_claude 替身：校验脚本 6 例、schema 1 例、产出时 HEAD 移动标 stale 2 例、校验拒绝路径 2 例。协调方独立重跑（与 R2 文件合批）：**19 passed**（11 新 + 8 既有）；ruff 全绿；`bash -n` 过。
- 审查（两轮均走包装脚本）：第一轮（review_sha=79e0798）6 findings（2W4N），其中 #0 经探针实测证为误报不修；第二轮终审（review_sha=8b687b4，stale=False）5 findings（4W1N），3 修 1 回填 1 不修。`resolved_in_commit` 回填是手工流程（见未决）。

### ④ deep-reviewer 加第四区域"验证器审查"（R4，HEAD a07f719）

- 新区域：`.claude/agents/deep-reviewer.md:31`，标题 `## 验证器审查（区域四：测试是否在糊弄）`，六个核查项每项"查什么 + 怎么认"（sleep 假装异步、跳过断言、测试写真实路径且引 M6-F 原案、降低阈值、放宽断言、mock 冒充实测）+ 严重度指引（写真实路径致数据损坏与断言缺失 → blocker；无依据放宽 → warning；sleep → warning、涉及时序竞态判 blocker）。finding 格式与现有清单完全一致（同 JSON 字段、同 severity 三档），未发明新字段；现有清单 5 项、输出格式、调用约定小节均未动。
- "现有三区域"的说明：仓库内无名为"三区域"的已命名区域（R2 已核实）；任务里的"三区域"指现有 findings 格式与 severity 三档。新区域按此口径落地，报告里已写明。
- 守卫测试：`tests/test_deep_reviewer_regions.py`（29 行，2 例），断言 agent 文件含"验证器审查"一节与 8 个关键词（六项标题 + `M6-F 原案` + `严重度指引`）。协调方独立重跑：**2 passed**；ruff 全绿。
- 演示验证（真跑审查，不进 pytest）：临时分支 `tmp/fake-tests-demo` 上三个故意糊弄的测试——(a) `time.sleep(0.1)` 后 `assert True`；(b) 直接读写真实 `config/qc_thresholds.yaml` 无断言；(c) `assert dice > 0.1` 替代 `> 0.8`——新区域判为 **3 条 blocker，全部命中**；审查员还多抓一个派遣没给的细点（(b) 的 replace 是空操作但 mtime 被改写）。糊弄用例先实测跑通（3 passed，证明能骗过 pytest）再送审；验证完分支已删，糊弄用例不在 gate 分支里（协调方核实：`git branch -a` 无 tmp 分支，`tests/test_demo_fake_*` 不存在）。演示绕过包装脚本的理由已记录：包装脚本从 main 读旧版规则，main 上没有新区域，直接派子代理并把工作树新版 agent 文件全文贴进 prompt 是演示特例；正式审查仍走包装脚本。
- 一致性核对：包装脚本 prompt 与 `post-review-to-pr.sh` 均无区域枚举，只改 agent 本体（协调方派遣曾把 hook 路径写成 `scripts/`，实际在 `.claude/hooks/`，实施方已纠正并记录）。
- 审查（经包装脚本，review_sha=e5891cc）：2 nit（1 修 1 不修，有变异实测依据）。审查后 HEAD 一移，协调方亲手跑校验实测 STALE exit 1。

## 3. 全量数字（实施方与协调方各一遍）

| 方 | 时点 | 数字 |
|---|---|---|
| 实施方（各轮受影响文件） | R2 / R1 / R3 / R4 | 8 / 10（守卫）·107+1跳过（6 文件）/ 19 / 2 passed，均为 exit 0，ruff 全绿 |
| 协调方（本人独立重跑受影响文件） | 各轮 | 8 / 10（守卫）·108（6 文件）/ 19 / 2 passed，均为 exit 0，ruff 全绿 |
| 协调方（本人独立全量重跑） | 代码终态 HEAD `a07f719` | **779 passed、0 failed、0 errors、0 skipped**（JUnit `m7-gate-suzi-fulltest-junit.xml`：tests=779 errors=0 failures=0 skipped=0；日志 0 行 FAILED/ERROR；`779 passed in 2852.79s`；ruff 全仓 `All checks passed!`） |

- 交叉核对：S3P2 基线全量 748 passed + 本轮新增 31 测试（R2 8 + R1 10 + R3 11 + R4 2）= **779**，与我方全量数逐项吻合。
- 本轮实施方未跑全量（各轮只跑受影响文件，按派遣要求）；全仓验证以协调方独立全量 779 为准。
- 全量跑前核实（协调方）：`__file__` 指向 gate worktree src；`bodydonor.db` 与 `eval/*.json` 在 worktree 内；GPU 锁串行；全量约 47 分钟。

### 附录：各轮受影响文件数字（协调方独立重跑核实）

- R2：`tests/test_deep_review_from_main.py` 8 passed；ruff 全绿；`bash -n` + `--help` 通过。
- R1：`tests/test_precommit_config_guard.py` 10 passed；6 文件合批 108 passed / 0 failed（实施方 107 passed + 1 skipped，收集总数同为 108）；ruff 全绿；`bash -n` 过。
- R3：`tests/test_review_binding.py` + `tests/test_deep_review_from_main.py` 19 passed；ruff 全绿；`bash -n` 过；`check_review_fresh.py` 实测 STALE exit 1（协调方亲手跑）。
- R4：`tests/test_deep_reviewer_regions.py` 2 passed；ruff 全绿。

## 4. 第 2 项验证记录（改松工作树规则 → 真审查 → 恢复）

1. **改松**：工作树 `.claude/agents/deep-reviewer.md` 删除审查清单第 4 项"测试：新增/修改的行为是否有测试覆盖；测试是否真的断言了所声称的行为"整条，**未提交**（`git diff` 实测 1 file, 1 deletion）。因改松文件挡切分支，先拷贝存档 `/tmp/relaxed-reviewer.md` 再还原。
2. **审查目标**：临时分支 `tmp/verify-target`（自 origin/main）提交 `7da78fe`，新增 `src/bodydonor/_gate_r2_gap.py`（2 行 `gap_add` 函数，故意无测试）。
3. **真审查**：`bash scripts/deep_review_from_main.sh "7da78fe~1..7da78fe"`，exit 0。关键输出行（`/tmp/r2-verify-evidence/r2-review-run.log`，已存档）：`review_sha=1e2be2a…`；审查输出中"测试覆盖"条目存在且执行了实质核查（diff 无测试 + CI 无覆盖率门槛 + 非 main 路径）。
4. **main 版证明**：改松版清单已无第 4 项，而审查输出中"测试覆盖"条目存在且执行了实质核查，证明审查规则用的是 main 版而非被改松的工作树版。
5. **恢复**：`git checkout -- .claude/agents/deep-reviewer.md` 还原；临时分支 `tmp/verify-target`、`tmp/verify-relaxed-review` 已删除；`git status` 干净（协调方实测 `grep -c "4\. 测试"` = 1，清单第 4 项在工作树与 HEAD 均在位）；审查 JSON 未留在分支（`reviews/` 清空，日志与 JSON 存档 `/tmp/r2-verify-evidence/`）。
6. 过程记录：deep-reviewer 子代理无 Bash，被审提交不在当前工作树时无法自取 diff；首次派发失败后由调用方实测 `git show`/`git grep` 贴入 prompt 复审通过（与 M6 轮 deep-reviewer 无 Bash 的已知边界一致）。

## 5. 未决清单（不写"全部修复"）

**环境与已知限制：**

1. **本地 main 滞后 origin/main**（`d7b5c44` vs `421ef0c`）：本仓本地 `main` 分支落后，规则文件当前两处 0 行差异故本轮验证不受影响；包装脚本已加 WARNING + 规则文件差异即 fatal。合 PR 前需在 main 所在 checkout 同步（本轮未动主 checkout，按任务要求）。
2. **包装脚本默认 scope 用 `merge-base main HEAD`**：本地 main 滞后时范围放宽（超集、不漏审，脚本打 WARNING）。可改为 origin/main 或滞后即 fatal，后续轮决定。
3. **本 worktree 未安装 hook**（`core.hooksPath` 为空）：装 hook 是每个 clone 的显式动作，本轮只交付脚本 + README；实际启用须跑 `git config core.hooksPath scripts/git-hooks`。
4. **R1 nit 7 未修**：文档漂移加固（fixture 在 scratch 仓里按相对路径装一次验证 hook 真运行）——收益低未做，后续轮可加。
5. **`resolved_in_commit` 回填是手工流程**：修复轮完成后把 sha 填进对应 finding 条目，无脚本强制，靠轮次报告纪律；如需机器校验可在后续轮加。
6. **`post-review-to-pr.sh` 不改的取舍**（R3 评估结论 b）：PR head 即合并候选，status 就该打在 head 上；stale 由 JSON + 校验脚本覆盖。门禁语义变化时需重新评估。
7. **新区域在 merge 进 main 后才进入包装脚本的注入 prompt**（R2 已记录的语义）：PR 合并前，正式审查对区域四的覆盖依赖演示验证路径；守卫测试只断言工作树版 agent 文件。
8. **deep-reviewer 子代理无 Bash**：被审提交不在当前工作树时审查方需自行贴 diff 实测（R2/R3 均遇此边界，已知）。
9. **包装脚本对 `claude -p` 会话是否落盘 findings 只能事后校验**，无法强制（fail-closed 已覆盖，设计内）。
10. **R1 6 文件合批耗时差**：协调方独立重跑 731.83s vs 实施方 60.43s，计数一致（108 收集、0 失败），耗时差原因未深究（疑似机器负载/GPU 争用时段不同）。
11. **egress-proxy SSH 中断**：本轮四次等候器被运行时 SIGTERM 杀掉、其间 SSH 全程不通（与 S3P/S3P2 同模式）；远端 claude/pytest 进程均未受影响，按教训改用直接 SSH 核查后继续，无重复派活、无重复跑。

## 6. 协调方勘误记录（本轮抓到的报告/派遣错误，均已修正或记录）

- R1 报告：证据节"26 行/102 行"是修前数，实为 32 行/135 行；终报尾"5 修"与 findings 表的 4 修不一致，以表为准（4 修 + 2 文档化 + 1 不修 = 7）。
- R3 报告：头"5 提交"实为 7 提交、diffstat 455 行实为 491 行——回填提交 `60f6b13`/`eca9142` 后未更新数；实施方尾信"6 提交"亦错，以实测 7 为准。
- R4 派遣：协调方把 `post-review-to-pr.sh` 路径写成 `scripts/`，实际在 `.claude/hooks/`——实施方已纠正。
- R2 报告 diffstat 口径：正文"3 文件 355 行"是报告提交前口径，终态为 4 文件 437 行（含报告文件本身），与 S3P2 终报同一约定。

## 7. 报告与日志文件

- 终报（本文件）：worktree 根 `M7-GATE-HARDENING-REPORT.md`；VM 同步件 `~/workspace/bodydonor/M7-GATE-HARDENING-REPORT.md`。
- 轮次报告：`M7-GATE-R2-REPORT.md`、`M7-GATE-R1-REPORT.md`、`M7-GATE-R3-REPORT.md`、`M7-GATE-R4-REPORT.md`（worktree 根 + VM `~/workspace/bodydonor/` 同步）。
- 协调方独立全量：JUnit `~/work/bodydonor-plans/m7-gate-suzi-fulltest-junit.xml`、日志 `~/work/bodydonor-plans/gate-my-fullrun.log`（VM 同步件见 `~/workspace/bodydonor/`）。
- 派遣文件：`gate-r2-dispatch.md`、`gate-r1-dispatch.md`、`gate-r3-dispatch.md`、`gate-r4-dispatch.md`（VM `~/workspace/bodydonor/` + WSL2 `~/work/bodydonor-plans/`）。
