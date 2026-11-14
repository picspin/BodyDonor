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

git diff 全程只动 `tests/test_m6e_wiring.py`；eval/、config/qc_thresholds.yaml、M4 门禁结果零改动（git status 每次提交前核对，eval json 移走后已原样移回）。
