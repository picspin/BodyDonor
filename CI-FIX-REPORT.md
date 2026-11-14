# CI-FIX-REPORT — 主线 CI 红修复（fix/ci-m6e-eval-file）

**日期**：2026-11-14　**分支**：fix/ci-m6e-eval-file（HEAD 7d644af 起步）　**故障**：CI run 43537247807，test job 16 秒失败、lint 绿。

## 1. 故障

- `uv run pytest -v`（.github/workflows/ci.yml:35，Python 3.12 / ubuntu-latest）collection 阶段中断：collected 596 items / 1 error，exit 2。
- 直接原因：`tests/test_m6e_wiring.py:25` 模块 **import 时**读 `(REPO / "eval" / "student_teacher_consistency.json").read_text(...)`。该文件被 `eval/.gitignore:3` 的 `*` 规则忽略（仅 `holdout.txt` / 构建评估脚本入仓），CI 干净检出无此文件 → FileNotFoundError。
- 本地 WSL2 600 点全过是因为该文件在本地存在（1.4K，2026-11-11 eval 产物）。

## 2. 根因

测试模块把 **gitignored 运行产物当成了 import 期硬依赖**。同类模式已核对不存在的场景：CI 上 `~/data/BodyDonor` 真实产物缺失，`_real_products_available()` skipif（`tests/test_m6e_wiring.py:267`）在 collection 期就返回 False——但模块级 `EVAL_BASELINE = json.loads(...)` 在 skipif 求值之前就执行并炸掉，skipif 根本没机会生效。

## 3. 修法（两个提交）

### 069c9c9 — 惰性读取（核心修复）

- 删模块级 `EVAL_BASELINE = json.loads(...)`，改为回归测试 `test_regression_419_pairs_flag_baseline` 内惰性读取（`json.loads(baseline_path.read_text(...))["overall"]["mean"]`，取值路径与原完全一致，deep-reviewer 已核实写端 `evaluate_student_holdout.py:272` 写 `overall.mean`）。
- `EVAL_BASELINE` 全文件只有该回归测试用；3 个接线单测（`without_case_id` / `missing_prediction` / `student_mask_runs_rule`）不依赖该文件，无文件时仍跑。
- CI 干净检出场景：`_real_products_available()` 因 `~/data/BodyDonor` 产物缺失返回 False → 回归测试 skip → collection 干净。走 PR #1（2026-11-07 TS-venv CI 修）同一模式：缺失外部依赖就 skip。

### 6d5735f — deep-reviewer W1（实跑复现后修复）+ W4

- **W1**：`_real_products_available()` 不检查 eval 基线 json 本身。有真实数据但缺该 json 的机器上，探测通过 → 回归测试跑完 30 例 run_qc → FileNotFoundError **fail 而非 skip**——"json 缺失→skip"只在数据也缺失时成立。修复：探测 paths 补 `(REPO / "eval" / "student_teacher_consistency.json")`，reason 文案同步更新；baseline 读取前移到函数开头（W4，缺文件早失败不白跑 30 例推理）。

## 4. 缺失条件复现

- 修前：临时移走 `eval/student_teacher_consistency.json` → `uv run pytest --collect-only -q` 在 `pathlib.py:1013 io.open` FileNotFoundError，collection error。
- 修后（069c9c9）：同条件 collection 干净（exit 0）。
- W1 修后（6d5735f）：同条件 `.venv/bin/python -m pytest tests/test_m6e_wiring.py -rs` → **SKIPPED [1]**（skipif reason：~/data/BodyDonor 真实产物或 eval 基线缺失）+ 3 passed in 0.87s——修前此条件是 FAILED（FileNotFoundError 于 :318）。本地 json 已原样移回。

## 5. pytest / ruff 数字

| 条件 | 结果 |
|---|---|
| 无 eval json：`pytest tests/test_m6e_wiring.py -rs` | **3 passed, 1 skipped**，exit 0 |
| 无 eval json：全套 `pytest -q` | **599 passed + 1 skipped**（600 items，1 个 s = 回归测试 skip），exit 0 |
| 有 eval json：全套 `pytest -q` | **600 passed**（600 items 全 dots），exit 0 |
| `uvx ruff check src tests` | **All checks passed**（两个提交前均验） |

（全套日志尾部 summary 行被 rtk pytest 包装器截断；exit code 0 + 100% 点阵逐字符核对为准。）

## 6. deep-reviewer findings（agent a92be1fac7ada7fc4，对抗式静态深审）

| # | 级别 | 位置 | 内容 | 复现步骤 | 处置 |
|---|---|---|---|---|---|
| W1 | Warning | tests/test_m6e_wiring.py:318 | skipif 探测不检查 eval 基线 json，有数据缺 json 机器上 fail 而非 skip | 有真实数据机器：`mv eval/student_teacher_consistency.json /tmp/` 后 `pytest tests/test_m6e_wiring.py -rs`——预期 skip，实际 FAILED | **实跑复现属实，已修（6d5735f）** |
| W2 | Warning | tests/test_m6e_wiring.py:252 | skipif 探测 collection 期经 `Settings()` 默认相对 URL 隐式访问 cwd 下 `./bodydonor.db`；库缺表时 OperationalError 不被 `except StudentPredictionError` 捕获 → collection error（同款探测见 test_m6c_priority.py:363-371、test_m6d_gating.py、test_m6f_trace.py:818-831） | 有数据机器：`./bodydonor.db` 换成空 sqlite 文件后 `pytest --collect-only tests/test_m6e_wiring.py`——预期 OperationalError | 属实，**预存模式跨 4 文件**，审查者建议另起修复；本轮不动，见 §7 |
| W3 | Warning | tests/test_m6e_wiring.py:92 | `_make_settings` 只改 database.url，`_seed_mask_payload` 经 `resolved_data_dir/"storage"` 写真实 `~/data/BodyDonor/storage/case-wired/` 且不清理（预存，非本 diff） | 本地跑接线单测后 `ls ~/data/BodyDonor/storage/case-wired` 应有残留 | 属实，**预存**，审查者建议另起修复；本轮不动 |
| N4 | Nit | tests/test_m6e_wiring.py:318 | baseline 读取在 30 例推理后，缺 json 白跑推理 | —— | **已修（6d5735f，前移到函数开头）** |

**Blocker：无**。审查者确认 CI 干净检出场景修复有效（`_real_products_available()` 在 paths 探测处返回 False → skip）。

## 7. 遗留（不在本轮范围，建议另起修复）

- W2：collection 期隐式访问 cwd 数据库（跨 test_m6e_wiring / m6c_priority / m6d_gating / m6f_trace 4 文件的 skipif 探测同款）。
- W3：接线单测写真实数据目录残留。
- CI 绿后开 M7-a。

## 8. 提交 hashes

| hash | 内容 |
|---|---|
| 069c9c9 | fix(m6e): 惰性读取 eval/student_teacher_consistency.json——干净检出 collection error 修复 |
| 6d5735f | fix(m6e): skipif 探测补 eval 基线 json + baseline 惰性读取前移 fail fast |
| 379c94e | docs: CI-FIX-REPORT 第 1 轮 |
| 2c1e2a6 | test+ci: local_artifacts 标记 16 项本地产物依赖测试 + CI -m 过滤跳过（第 2 轮 S1） |

git diff 全程只动 `tests/test_m6e_wiring.py`；eval/、config/qc_thresholds.yaml、M4 门禁结果零改动（git status 每次提交前核对，eval json 移走后已原样移回）。

---

# 第 2 轮（S1 细化，M7 计划 v3.2）

**日期**：2026-11-14　**分支**：fix/ci-m6e-eval-file（HEAD 379c94e 起步）

## 9. S1-1 本地产物依赖测试打标记（2c1e2a6）

- `pyproject.toml` `[tool.pytest.ini_options]` 注册 `local_artifacts` marker（避免 unknown-mark warnings）。
- `.github/workflows/ci.yml:35` Unit tests 步：`uv run pytest -v -m "not local_artifacts"`（job 其余步骤零重构）。

### 标记清单（16 项测试 / 12 处装饰器）

| 位置 | 测试 | 依赖 |
|---|---|---|
| tests/test_m6c_priority.py | test_ranking_consistency_30_holdout_top20 | 30 例 hold-out 教师+学生 mask + eval/holdout_results.json（低频名单） |
| tests/test_m6d_gating.py | test_gating_three_tiers_30_holdout | 同上 |
| tests/test_m6e_wiring.py | test_regression_419_pairs_flag_baseline | 30 例 hold-out + eval/student_teacher_consistency.json 基线 |
| tests/test_m6f_trace.py | test_real_holdout_trace_back_30_cases | 30 例 hold-out + eval/holdout_results.json |
| tests/test_qc_interaction_e2e.py | TestReplayRealPath（类级，2 测试） | 真实 amos_0004 patch（真实 nnInteractive CPU 推理） |
| tests/test_interaction_metrics.py | TestCorrectSubmitE2E（类级，3 测试） | 真实 amos_0004 patch |
| tests/test_nninteractive_backend.py | TestBboxScribbleRealRun（类级，2 测试） | 真实 amos_0004 patch |
| tests/test_nninteractive_backend.py | TestRunnerContractCpu 的 test_cpu_small_input_real_run / test_session_id_unique_per_run（函数级） | real_patch fixture |
| tests/test_review_api.py | test_risk_queue_order | eval/student_holdout_results.json（缺文件 503） |
| tests/test_evaluate_student_holdout.py | test_holdout_results_json_scope_numbers | eval/student_holdout_results.json |
| tests/test_interaction_metrics.py | test_teacher_eval_product_loads_117_classes | eval/holdout_results.json（load_teacher_eval） |

**精度修正**：TestRunnerContractCpu 只函数级标记两个 real_patch 消费者——同类的 `test_empty_prompts_rejected` 是纯逻辑测试（`b"stub"` 桩文件），类级标记会让它被 CI 白跳过。

### 逻辑分支合成覆盖（不取真实 holdout 内容）

已有合成 fixture 未重复造：接线单测（test_m6e_wiring `_seed_mask_payload` 合成 mask）、qc_series 过程规则（test_qc_interaction_e2e TestSyntheticBoundary 合成 provenance/tracker）、门禁/采样/tier（test_qc/test_m6c_priority/test_m6d_gating 合成 rows）、trace 构造与回流（test_m6f_trace 合成 QCReport+DecisionTrace）。真实 hold-out 依赖只剩 5 个验收测试（上表前 4 行 + TestReplayRealPath），均已标记。

## 10. S1-2 真实 holdout JSON 不入库核查

- `git log --all --diff-filter=A -- "eval/*.json"` → **空**（无任何历史提交）。
- `git ls-files eval/` → 仅 `.gitignore`、`build_holdout.py`、`evaluate_holdout.py`、`evaluate_student_holdout.py`、`holdout.txt`（固化的 case_id 列表，proposal §8 允许入仓）。
- eval/.gitignore `*` 规则 + git add 默认不进 untracked → 第 1 轮无误提交。本轮 diff 零触碰 eval/。

## 11. S1-3 Alembic roundtrip + License CLI 实测（CI 原命令）

| 步骤 | 结果 |
|---|---|
| `uv run alembic -x db_url=sqlite:///./ci_mig.db upgrade head` | exit 0（a4f9e2c81d03） |
| 同 `downgrade base` | exit 0 |
| 同 `upgrade head` | exit 0；`rm -f ci_mig.db` |
| `uv run bodydonor license check total` | ✔ allowed，exit 0 |
| `! uv run bodydonor license check brain_aneurysm` | ✘ BLOCKED，exit 1（`!` 反转后过） |
| `! uv run bodydonor license check heartchambers_highres` | ✘ not in whitelist，exit 1（同） |

顺序性核实：三步同 job 顺序执行，GitHub 默认 shell `bash -e`，前一步失败中止后续——Unit tests 绿则后两步按序跑。ci.yml job 结构零改动。

## 12. S1-4 「全量 flag 即通过」审计

grep 全套 tests/ 找 `len(flagged) == len(holdout)` 类写法 → **无此模式，零改动**。三处候选逐一核实为真断言：

| 位置 | 写法 | 定性 |
|---|---|---|
| tests/test_qc.py:353 | `len(plan.flagged_ids) == min(len([r for r in rows if not r["passed"]]), expected_flagged)` | 70/30 采样语义断言：flag 上限受 failed 数约束，非全量通过 |
| tests/test_m6e_wiring.py:319 | `abs(len(flagged) - len(baseline)) <= 1` | M6-e 回归 ±1 例**容差**（用户明确不算全量通过），不误伤 |
| tests/test_m6d_gating.py:96-99 | `all(tier_for_rank(i, 5, cfg) == "forced"/"auto")` | tier_for_rank 边界 config（1.0/0/0、0/0/1.0）的函数契约断言 |

## 13. 第 2 轮验收数字

| 条件 | 结果 |
|---|---|
| 移走 eval/student_teacher_consistency.json：`uv run pytest --collect-only -q` | **600 项，exit 0，无 collection error**（文件已原样移回） |
| `uv run pytest -m "not local_artifacts"` | **584 passed, 16 deselected**，exit 0（71.9s） |
| `pytest -m "local_artifacts" --collect-only` | 16 项（= 被排除清单，无一在 CI 跑） |
| 全套 `pytest -q`（有文件条件） | 见 §15 |
| `uvx ruff check src tests` | **All checks passed** |

## 14. deep-reviewer findings（agent 对抗式静态深审，第 1+2 轮全范围）

| # | 级别 | 位置 | 内容 | 复现步骤 | 处置 |
|---|---|---|---|---|---|
| W1 | Warning | tests/test_review_api.py:487 | test_risk_queue_unknown_series_404 未标记；client() 不带 with，lifespan 不跑 init_db，404 依赖 isolate_db_url 建表 | `pytest tests/test_review_api.py::TestReviewRiskQueue::test_risk_queue_unknown_series_404 -v` | **实跑 1 passed**——非问题（预期 404 断言成立） |
| W2 | Warning | tests/test_nninteractive_backend.py:143/165/198 | TestLockMutex 三测试经 correct_image 访问 Settings() 默认 DB；CI 通过依赖 .venv-nninteractive 缺失抛 NnInteractiveError，环境变化断言语义变 | 本机（nni venv 存在）`pytest tests/test_nninteractive_backend.py::TestLockMutex -v` | **实跑 4 passed**——本机有 nni venv 时仍过（stub 输入在 runner 输入校验层 fail-fast，三测试断言的是锁层语义不依赖推理成败）；CI 无 venv 同样过。预存环境依赖如实记录，不属本轮 diff |
| W3 | Warning | pyproject.toml:57 | addopts 未加 --strict-markers：marker 拼错只告警不报错，-m 过滤失效 | 把某测试 local_artifacts 写成 local_artifact 后 `pytest -m "not local_artifacts"`，无 strict 时该测试照跑 | 属实；不加 --strict-markers 是**预存 addopts 形态**（改 addopts 影响全套测试默认行为，超出本轮 surgical 范围）。当前 12 处标记拼写已逐一核对（§9 清单 + collect-only 16 项交叉验证），实际无拼错 |
| N1 | Nit | .github/workflows/ci.yml:35 | `-v` 与 addopts `-q` 互相抵消，CI 日志详细程度不合预期 | `pytest -v -m "..." --co -q` 对比 | 预存形态（`-v` 第 1 轮前就有），不动 |
| N2 | Nit | tests/test_evaluate_student_holdout.py:29 | esh fixture 按路径加载 eval 脚本，CI 导入未核实 | `pytest tests/test_evaluate_student_holdout.py -v` | **实跑 4 passed**——脚本顶层仅 argparse/json/subprocess 等标准库 + numpy + repo 内模块（sys.path.insert 后），DATA_ROOT 是常量不在 import 期读取，CI 干净导入无问题 |

**Blocker：无**（审查者结论 + 调用方逐条实跑复核）。

## 15. 全套 pytest（有文件条件）

后台 10m 时限被 kill 一次（管道化缓冲），改前台后台化重跑收尾——结果以 log 尾行 + exit code 为准（真实 holdout 验收 30 例现算历史基线 951s）。

## 16. 遗留（同 §7，另起修复）

- 第 1 轮 W2（collection 期隐式访问 cwd 数据库）、W3（接线单测写真实数据目录残留）。
- 第 2 轮 W3（--strict-markers 不在 addopts）如要收紧另起。
- CI 绿后开 M7-a。
