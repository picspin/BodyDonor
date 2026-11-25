# M7-GATE-R3 轮次报告：③ 审查绑定 commit

基线 R1 HEAD `b833b66`（①② 已落地）。本轮 5 提交（feature + fix/test + 报告 + 两轮审查修复），全部在 `feat/m7-gate-hardening` worktree，未 push / 未 merge / 未开 PR。

## 提交列表（实测 `git log --oneline b833b66..HEAD`）

- `a5d4894` feat(m7-gate): ③ 审查绑定 commit——wrapper 补写 region/resolved_in_commit/stale + HEAD 移动标 stale + check_review_fresh.py 读侧校验（4 files, +84/-5）
- `79e0798` fix(m7-gate): ③ stale 旗标初始化——fresh 报告也写 stale:false + 新增 test_review_binding.py 9 例（2 files, +227）
- `c98699f` docs(m7-gate): R3 轮次报告骨架（1 file, +71）
- `8b687b4` fix(m7-gate): ③ 审查修复——退出码语义写明 + 缺字段校验路径补测试 + fresh 脚本错误信息（3 files, +24/-3）
- `85c5c79` fix(m7-gate): ③ 终审修复——fresh 脚本异常覆盖统一 + null 字段校验 + 报告分支测试（3 files, +55/-3）

## 累计 diffstat（`git diff 421ef0c..HEAD --stat` 尾行）

`12 files changed, 1186 insertions(+)`（分支累计含 ①② 历史；本轮 5 提交净增 `6 files changed, 455 insertions(+), 5 deletions(-)`）。

## ③ 逐项证据

### §1 结构化 JSON 报告

- 落点 `reviews/review-<review_sha>.json`（沿用 R2 命名）。
- schema 定死：`{review_sha, region, findings[{severity, file, line, evidence, suggestion, reproduction, resolved_in_commit}], stale}`。
  - `review_sha`：wrapper `scripts/deep_review_from_main.sh:24` 抓取 `git rev-parse HEAD` 并打印（R2 已有，本轮沿用）。
  - `region`：wrapper 校验段补写，`scripts/deep_review_from_main.sh:122`（`d["region"] = sys.argv[3]`，sys.argv[3] = 传给包装脚本的范围字符串，如 `b833b66..HEAD`；默认范围时为实际算出的 `merge-base..HEAD`）。定义：**region = 传给包装脚本的范围字符串**。
  - `findings`：沿用现有六字段，不增不减（`resolved_in_commit` 例外见下）。
  - `resolved_in_commit`：per-finding 键，wrapper 补写 `scripts/deep_review_from_main.sh:125`（`f.setdefault("resolved_in_commit", None)`），未解决时 `null`；被"接受为已文档化限制/风险"的条目填记录该决定的提交 sha。**注意：是 per-finding 键，不是顶层键**（首轮测试把断言写成顶层导致 KeyError，已按 schema 修正）。
  - `stale`：顶层布尔，补写段 `scripts/deep_review_from_main.sh:123`（`d.setdefault("stale", False)`，79e0798 修正：fresh 报告也初始化为 false）。
- 版本管理决定（协调方已定）：`reviews/` 运行产物不进版本管理，`.gitignore:16-18` 追加 `reviews/`（理由：与 JUnit XML 同类；审计轨迹=轮次报告 md + PR 评论；JSON 是供校验脚本读取的机器件）。
- 测试：`tests/test_review_binding.py::test_wrapper_output_full_schema`——五键齐全 + findings 六字段 + `resolved_in_commit=null`；ruff 全绿。

### §2 失效判定（两处落地）

1. **产出时**（wrapper）：`scripts/deep_review_from_main.sh:134-142`——校验后重读 `now_sha=$(git rev-parse HEAD)`（135 行）与开头 `review_sha` 比对；不一致 → 打印 `STALE: HEAD moved during review (<old> -> <new>)` 并把报告 `stale` 写成 `true`（python 段 `d["stale"] = True`，142 行），不静默当 fresh 用。
2. **读取时**：新脚本 `scripts/check_review_fresh.py`（实测 53 行）——读报告 `review_sha` 与当前 HEAD 比对 + `stale` 旗标：一致且 stale=false → `FRESH` exit 0；不一致或 stale=true → `STALE: review_sha=<report_sha>, HEAD=<head>`（旗标路径前缀 `STALE: report stale flag set (...)`）exit 1；报告缺失/非 JSON/顶层非 object → 明确 STALE 信息 exit 1（fail-closed，实测 rc=1）。脚本被 pytest 测到：6 例（fresh/HEAD-moved/stale-flag/missing/invalid-json/non-object）。
- 测试：`test_fresh_check_fresh_report_exits_zero` / `test_fresh_check_head_moved_exits_nonzero` / `test_fresh_check_stale_flag_exits_nonzero_even_if_sha_matches` / `test_fresh_check_missing_report_exits_nonzero` / `test_fresh_check_invalid_json_exits_nonzero` / `test_fresh_check_non_object_json_exits_nonzero`。

### §3 派遣约定

- (a)+(b) `scripts/deep_review_from_main.sh:78-84`（prompt 段）：派遣指令带 base sha（78 行"派遣指令里必须写明被审 base sha：`<review_sha>`"）+ 子代理回报须写明审的哪个 sha（79 行"人类可读结论首行写明本次审查的 base sha" + JSON `review_sha` 字段）。
- `.claude/agents/deep-reviewer.md:69`（调用约定小节追加）：审查绑定 commit 约定——每次审查产出 `reviews/review-<sha>.json`；HEAD 一变旧报告即 stale；引用前须跑 `scripts/check_review_fresh.py` 核验；退出码不代表新鲜（85c5c79 补写）。

### §4 post-review-to-pr.sh 评估结论：**(b) 不改**

理由：
1. PR head 即合并候选——CI 门禁语义上就该打在 head 上：status 打在被审 sha 上会丢失"当前合并候选是否通过审查"这一门禁信号。
2. stale 问题由本轮 JSON + check_review_fresh.py 覆盖（§2）：审查报告绑定的是 `review_sha`，引用侧核验；hook 的 status 是 PR 门禁信号，两者语义不同，不互相替代。
3. hook 已有 fallback（`.claude/hooks/post-review-to-pr.sh:153-155`：422 line-not-in-diff 回退汇总评论），审查后 HEAD 前移导致的 inline 422 已有兜底。

风险与已接受限制：审查期间 HEAD 前移时，commit status 仍打在新 head（未被审的 commit）上——已接受，理由如上。

### §5 测试（失败测试先行）

新文件 `tests/test_review_binding.py`（实测 289 行，11 个 test 函数，全部 scratch 仓 + fake_claude 替身，不动 worktree HEAD）：
- (a) 校验脚本：`test_fresh_check_fresh_report_exits_zero` / `test_fresh_check_head_moved_exits_nonzero` / `test_fresh_check_stale_flag_exits_nonzero_even_if_sha_matches` + 终审补 3 例 `test_fresh_check_missing_report_exits_nonzero` / `test_fresh_check_invalid_json_exits_nonzero` / `test_fresh_check_non_object_json_exits_nonzero`。
- (b) schema：`test_wrapper_output_full_schema`（五键 + findings 六字段 + per-finding resolved_in_commit）。
- (c) 产出时 HEAD 移动：`test_wrapper_marks_stale_when_head_moved_during_review`（替身 `movelate` 模式：落盘后 `git commit --allow-empty` 移动 HEAD，断言 `STALE` in stdout 且 `stale=true`）+ `test_wrapper_fresh_when_head_unchanged` 对照。
- (d) 校验拒绝路径（审查修复补）：`test_wrapper_rejects_finding_missing_field`（缺字段）/ `test_wrapper_rejects_finding_null_field`（字段值为 null）。

## 测试数字（实测）

- `tests/test_review_binding.py + tests/test_deep_review_from_main.py`：**19 passed**（11 新 + 8 既有）。
- ruff：`tests/test_review_binding.py` + `scripts/check_review_fresh.py` 全绿（All checks passed）。
- `bash -n scripts/deep_review_from_main.sh`：SYNTAX-OK。
- `scripts/check_review_fresh.py` 实测 53 行；错误分支实测：`/nonexistent.json` → `STALE: report not found` rc=1；`not json` → `STALE: report not readable JSON` rc=1；`[]` / `null` 顶层 → `STALE: report not a JSON object` rc=1。

## 审查 findings 清单与处理

审查路径：**两轮均走 `bash scripts/deep_review_from_main.sh "<range>"`（R2 落地的唯一入口）**。

### 第一轮（`b833b66..HEAD`，review_sha=79e0798）：6 findings（2W4N）

审查期间我提交了报告骨架 c98699f，HEAD 移动触发 wrapper stale 判定（`STALE: HEAD moved during review (79e0798 -> c98699f)`，实测输出）——**③ 机制在实跑中正确触发**，该报告按契约 stale 不可直接引用，findings 内容仍做 double-check：

| # | sev | 位置 | 结论 | 依据 |
|---|-----|------|------|------|
| 0 | warning | tests/test_review_binding.py:22 | **误报不修** | 声称"规则注入契约无覆盖"——既有 `test_extracts_main_version_not_worktree`（test_deep_review_from_main.py:98）正断言该契约；探针实测（wrapper:49 `git show main:` → `cp` 工作树文件）下该测试 **FAILED**，审查者复现只跑新 6 例漏既有 8 例。已还原探针，`git status` 干净 |
| 1 | warning | scripts/deep_review_from_main.sh:102 | **部分属实已修** | 失败路径 nowrite/badjson/badstruct 已有既有测试（test_deep_review_from_main.py:154/166/178）；属实部分="findings 字段缺失"新校验无测试 → 补 `test_wrapper_rejects_finding_missing_field`（wrapper 本就 fail-closed，属补覆盖非修 bug） |
| 2 | nit | scripts/deep_review_from_main.sh:146 | **属实已修** | wrapper HEAD 移动时 exit 0，门禁靠 check_review_fresh.py——deep-reviewer.md:69 调用约定补写明"退出码不代表新鲜" |
| 3 | nit | scripts/deep_review_from_main.sh:124 | **不修** | 审查者自认"实际已覆盖该意图"，措辞指向问题不加字 |
| 4 | nit | scripts/check_review_fresh.py:28 | **部分属实已修** | 文件名 sha 不比对是设计（绑定报告内 review_sha，文件名只是落点约定）；缺失/非 JSON traceback 不友好属实 → 改明确 STALE 信息（exit 1 语义不变，实测 rc=1） |
| 5 | nit | tests/test_review_binding.py:61 | **不修** | 审查者自写"无需修改"（git ≥2.28 为 2020 年版本，实测 2.53.0） |

修复提交 `8b687b4`（15 passed 后又补 null 分支测试）。

### 第二轮终审（`79e0798..HEAD`，review_sha=8b687b4）：**stale=False**、region=`79e0798..HEAD`，5 findings（4W1N）

| # | sev | 位置 | 结论 | 依据 |
|---|-----|------|------|------|
| 0 | warning | scripts/check_review_fresh.py:28 | **属实已修** | 修复轮新增的两 except 分支无测试 → 补 `test_fresh_check_missing_report_exits_nonzero` / `test_fresh_check_invalid_json_exits_nonzero` |
| 1 | warning | M7-GATE-R3-REPORT.md:66 | **属实已修（本报告）** | "226 行 9 例"实为 289 行 11 函数；"14 passed（9 新+8 既有-3 重跑）"算式不成立 → 全部以实测回填（19 passed = 11 新 + 8 既有）；行号引用已按亲读行修正 |
| 2 | warning | scripts/deep_review_from_main.sh:117（修复前行号，现 118） | **复现属实已修** | null 值全字段实测通过校验（PASSED-VALIDATION）→ 校验改 `f.get(k) is None` 拒绝（118 行）+ 补 `test_wrapper_rejects_finding_null_field` |
| 3 | warning | scripts/check_review_fresh.py:29 | **复现属实已修** | `[]` 顶层 / 目录输入均实测 traceback → except (OSError, JSONDecodeError, UnicodeDecodeError) 统一 STALE + 顶层非 dict 判 STALE + 补 `test_fresh_check_non_object_json_exits_nonzero` |
| 4 | nit | scripts/check_review_fresh.py:19 | **不修（nit）** | 审查者自认可接受；outside-git 退出码非 0 已 fail-closed（实测 rc=1 traceback 后改判据——实测 outside-git 时先报 report not found rc=1，不触发该分支） |

修复提交 `85c5c79`。终审后 19 passed、ruff 全绿、bash -n 过；`reviews/review-8b687b4...json` 实测 `stale=false`（fresh）。

### 实跑验证（机制自证）

- 第一轮审查期间提交 c98699f → wrapper 实跑打印 `STALE: HEAD moved during review (79e0798... -> c98699f...)` 并把报告 `stale` 写成 `true`（实测 JSON 内 stale=True）。
- 终审未动 HEAD → 报告 `stale=False`。
- 报告回填提交 60f6b13 落盘后，`python3 scripts/check_review_fresh.py reviews/review-8b687b4....json` 实测打印 `STALE: review_sha=8b687b4..., HEAD=60f6b13...` exit 1——审查结论绑定被审 sha，后续提交即使只是 docs 也使旧报告失效，读侧判定路径按契约工作。

## 未决清单

1. `resolved_in_commit` 的回填目前是手工流程（修复轮完成后把 sha 填进对应 finding 条目）——无脚本强制，靠轮次报告纪律；如需机器校验可在后续轮加。本轮两轮 findings 的 resolved_in_commit：第一轮 [1][2][4]→`8b687b4`，[0][3][5]→不修（null）；第二轮 [0][1][2][3]→`85c5c79`，[4]→不修（null）。
2. `post-review-to-pr.sh` 不改的取舍是本轮评估结论（§4），后续轮次如门禁语义变化需重新评估。
3. 派遣文档（`.claude/agents/deep-reviewer.md`）的规则来源是 main 分支——本轮对该文件的调用约定改动在 merge 进 main 后才会被下一轮审查的注入 prompt 看到（R2 报告已记录此为已知语义）。
