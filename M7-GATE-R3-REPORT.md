# M7-GATE-R3 轮次报告：③ 审查绑定 commit

基线 R1 HEAD `b833b66`（①② 已落地）。本轮 2 提交（`a5d4894` feature + `79e0798` fix/test），全部在 `feat/m7-gate-hardening` worktree，未 push / 未 merge / 未开 PR。

## 提交列表（实测 `git log --oneline b833b66..HEAD`）

- `a5d4894` feat(m7-gate): ③ 审查绑定 commit——wrapper 补写 region/resolved_in_commit/stale + HEAD 移动标 stale + check_review_fresh.py 读侧校验（4 files, +84/-5）
- `79e0798` fix(m7-gate): ③ stale 旗标初始化——fresh 报告也写 stale:false + 新增 test_review_binding.py 9 例（2 files, +227）

## 累计 diffstat（`git diff 421ef0c..HEAD --stat` 尾行）

`11 files changed, 1042 insertions(+)`（分支累计含 ①② 历史；本轮 2 提交净增 `6 files changed, +311/-5`）。

## ③ 逐项证据

### §1 结构化 JSON 报告

- 落点 `reviews/review-<review_sha>.json`（沿用 R2 命名）。
- schema 定死：`{review_sha, region, findings[{severity, file, line, evidence, suggestion, reproduction, resolved_in_commit}], stale}`。
  - `review_sha`：wrapper `scripts/deep_review_from_main.sh:33-35` 抓取 `git rev-parse HEAD` 并打印（R2 已有，本轮沿用）。
  - `region`：wrapper 校验段补写，`scripts/deep_review_from_main.sh:123`（`d["region"] = sys.argv[3]`，sys.argv[3] = 传给包装脚本的范围字符串，如 `b833b66..HEAD`；默认范围时为实际算出的 `merge-base..HEAD`）。定义：**region = 传给包装脚本的范围字符串**。
  - `findings`：沿用现有六字段，不增不减（`resolved_in_commit` 例外见下）。
  - `resolved_in_commit`：per-finding 键，wrapper 补写 `scripts/deep_review_from_main.sh:125`（`f.setdefault("resolved_in_commit", None)`），未解决时 `null`；被"接受为已文档化限制/风险"的条目填记录该决定的提交 sha。**注意：是 per-finding 键，不是顶层键**（首轮测试把断言写成顶层导致 KeyError，已按 schema 修正）。
  - `stale`：顶层布尔，补写段 `scripts/deep_review_from_main.sh:124`（`d.setdefault("stale", False)`，79e0798 修正：fresh 报告也初始化为 false）。
- 版本管理决定（协调方已定）：`reviews/` 运行产物不进版本管理，`.gitignore:16-18` 追加 `reviews/`（理由：与 JUnit XML 同类；审计轨迹=轮次报告 md + PR 评论；JSON 是供校验脚本读取的机器件）。
- 测试：`tests/test_review_binding.py::test_wrapper_output_full_schema`——五键齐全 + findings 六字段 + `resolved_in_commit=null`；ruff 全绿。

### §2 失效判定（两处落地）

1. **产出时**（wrapper）：`scripts/deep_review_from_main.sh:139-150`——校验后重读 `now_sha=$(git rev-parse HEAD)` 与开头 `review_sha` 比对；不一致 → 打印 `STALE: HEAD moved during review (<old> -> <new>)` 并把报告 `stale` 写成 `true`（python 段 `d["stale"] = True`），不静默当 fresh 用。
2. **读取时**：新脚本 `scripts/check_review_fresh.py`（53 行）——读报告 `review_sha` 与当前 HEAD 比对 + `stale` 旗标：一致且 stale=false → `FRESH` exit 0；不一致或 stale=true → `STALE: review_sha=<report_sha>, HEAD=<head>`（旗标路径前缀 `STALE: report stale flag set (...)`）exit 1。脚本被 pytest 测到：`test_review_binding.py` 三例（fresh/HEAD-moved/stale-flag）。
- 测试：`test_fresh_check_fresh_report_exits_zero` / `test_fresh_check_head_moved_exits_nonzero` / `test_fresh_check_stale_flag_exits_nonzero_even_if_sha_matches`。

### §3 派遣约定

- (a)+(b) `scripts/deep_review_from_main.sh:88-98`（prompt 段）：派遣指令带 base sha（"派遣指令里必须写明被审 base sha：`<review_sha>`"）+ 子代理回报须写明审的哪个 sha（"人类可读结论首行写明本次审查的 base sha" + JSON `review_sha` 字段）。
- `.claude/agents/deep-reviewer.md:69`（调用约定小节追加）：审查绑定 commit 约定——每次审查产出 `reviews/review-<sha>.json`；HEAD 一变旧报告即 stale；引用前须跑 `scripts/check_review_fresh.py` 核验。

### §4 post-review-to-pr.sh 评估结论：**(b) 不改**

理由：
1. PR head 即合并候选——CI 门禁语义上就该打在 head 上：status 打在被审 sha 上会丢失"当前合并候选是否通过审查"这一门禁信号。
2. stale 问题由本轮 JSON + check_review_fresh.py 覆盖（§2）：审查报告绑定的是 `review_sha`，引用侧核验；hook 的 status 是 PR 门禁信号，两者语义不同，不互相替代。
3. hook 已有 fallback（`.claude/hooks/post-review-to-pr.sh:153-155`：422 line-not-in-diff 回退汇总评论），审查后 HEAD 前移导致的 inline 422 已有兜底。

风险与已接受限制：审查期间 HEAD 前移时，commit status 仍打在新 head（未被审的 commit）上——已接受，理由如上。

### §5 测试（失败测试先行）

新文件 `tests/test_review_binding.py`（226 行，9 例，全部 scratch 仓 + fake_claude 替身，不动 worktree HEAD）：
- (a) 校验脚本三例：`test_fresh_check_fresh_report_exits_zero` / `test_fresh_check_head_moved_exits_nonzero` / `test_fresh_check_stale_flag_exits_nonzero_even_if_sha_matches`。
- (b) schema 一例：`test_wrapper_output_full_schema`（五键 + findings 六字段 + per-finding resolved_in_commit）。
- (c) 产出时 HEAD 移动：`test_wrapper_marks_stale_when_head_moved_during_review`（替身 `movelate` 模式：落盘后 `git commit --allow-empty` 移动 HEAD，断言 `STALE` in stdout 且 `stale=true`）+ `test_wrapper_fresh_when_head_unchanged` 对照。

## 测试数字（实测）

- `tests/test_review_binding.py + tests/test_deep_review_from_main.py`：**14 passed**（9 新 + 8 既有 - 3 合并重跑）。
- ruff：`tests/test_review_binding.py` + `scripts/check_review_fresh.py` 全绿（All checks passed）。
- `bash -n scripts/deep_review_from_main.sh`：SYNTAX-OK。

## 审查 findings 清单与处理

审查路径：`bash scripts/deep_review_from_main.sh "b833b66..HEAD"`（R2 落地的唯一入口；本地 main 滞后 origin/main 时脚本自带护栏）。findings 逐条 double-check（带复现步骤）：

（待审查完成后回填）

## 未决清单

1. `M7-GATE-R3-REPORT.md` 本身未 review（本报告是本轮产物，审查对象是代码改动）。
2. `post-review-to-pr.sh` 不改的取舍是本轮评估结论（§4），后续轮次如门禁语义变化需重新评估。
3. `resolved_in_commit` 的回填目前是手工流程（修复轮完成后把 sha 填进对应 finding 条目）——无脚本强制，靠轮次报告纪律；如需机器校验可在后续轮加。
