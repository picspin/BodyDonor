# M7-S2 Rebase 深度审查（deep-reviewer）

- 对象：worktree `/home/gogvf/work/BodyDonor-S2`，分支 `feat/m7-s2-three-set-split`，HEAD `a99e7a1`，rebase 基线 origin/main `421ef0c`。
- 方式：deep-reviewer 子代理（只读对抗性审查，无 Bash）+ 调用方实测复现（本记录）。
- 结果：**0 Blocker，0 CONFIRMED Warning，2 nit**。两条 nit 均实测复核完毕，结论：**均不修**（理由见下）。

## Findings 与处理

### nit-1（PLAUSIBLE）`src/bodydonor/distill_data.py:652-653` — `__all__` 中 `"ALLOWED_TRAIN_SOURCES"` 重复两次

- **复现**（调用方实测）：`grep -n '"ALLOWED_TRAIN_SOURCES",' src/bodydonor/distill_data.py` → 652、653 两行。
- **归属核查**：`git show origin/main:src/bodydonor/distill_data.py` 同样两行（main 侧 646-647 行）→ **main 自带，非 rebase/S2 引入**。
- **处理**：不修。非本轮改动产生（rebase 0 冲突完整重放，未触碰该处）；属 CLAUDE.md §3「不删既有问题，只提及」范围。已记入报告未决清单。

### nit-2 `src/bodydonor/qc_priority.py:239` — 低频判断硬编码 `< 10` 未引用 `MIN_PER_CLASS`

- **复现**（亲读）：`per_class_case_count.get(c, 0) < 10`（`component_low_freq_presence` 内）。
- **归属核查**：该行不在 S2 的 7 行 diff（diff 只有 `component_disagreement` docstring 聚合理由段）→ **main 自带**。
- **口径核查**：`interaction_metrics.MIN_PER_CLASS = 10`（:46）、`holdout.MIN_PER_CLASS = 10`（:32）、此处硬编码 10 —— 当前三处同值。仓库既有模式即「常量 + 注释标注同口径」（如 `qc_priority.py:81` `_HIGH_RISK_TEACHER_CLASSES` 注释「interaction_metrics.HIGH_RISK_TEACHER_CLASSES 同口径」）。
- **处理**：不修。当前值一致且与仓库既有口径注释模式一致；改动属「improve adjacent code」。已记入报告未决清单。

## 已查无问题（调用方逐项补测/补读）

| 检查项 | 结论 | 实测依据 |
|---|---|---|
| distill_data.py 结合处变量作用域 | ✓ 无问题 | `prepare_dataset` 内 `pool`、`holdout_path`、`job_id` 均在作用域（reviewer 亲读） |
| `load_holdout(holdout_path)` 参数类型 | ✓ 无问题 | `Path \| None`，与 `build_train_pool` 同口径（holdout.py:142）；协调方探针实测传 str 才会在 :145 报错，此处传的是 Path 局部变量 |
| S2 import 行与 main 侧冲突 | ✓ 无冲突 | diff 只有 1 行 import 改动（`check_leak, load_holdout` → 加 `case_ids_hash`），main 侧 `holdout.py` 模块完整在位 |
| holdout.py `PROJECT_ROOT` 推导 | ✓ 指 worktree 根 | `Path(__file__).resolve().parents[2]` → `/home/gogvf/work/BodyDonor-S2`，`EVAL_DIR` 正确；实测 `load_holdout(Path('eval/holdout.txt'))` 读到 30 例 |
| `case_ids_hash` 格式两侧一致 | ✓ 同格式 | 写侧 `distill_data.py:608-610` 与函数定义 `holdout.py:55-62`（排序后 `\n` 连接 utf-8，16 hex）；实测重算 manifest 35 例 = `b6f90c1fe6ccc16c` |
| 许可清单（ALLOWED_TRAIN_SOURCES/REJECTED_SOURCES） | ✓ 未被本轮改动 | diff 无触碰；nit-1 为 main 自带重复项 |
| yaml source 标记读侧 | ✓ 不会 raise | `qc_trace.py` 用 `str(sp.get("source", "cold_start"))` 读，不做枚举校验，`holdout_30_dev` 兼容；qc.py:825 写侧 `calibrated_on_N` 与新标记互不冲突（reviewer 亲读） |
| interaction_metrics / holdout 口径 | ✓ 同值 | `MIN_PER_CLASS` 两处均 10；`HIGH_RISK_TEACHER_CLASSES` = `{"prostate"}` 与 `qc_priority._HIGH_RISK_TEACHER_CLASSES` 同口径 |
| tests monkeypatch 目标 | ✓ 在位 | `dd.AMOS`、`dd.CT_LITE` 在 `distill_data.py:42-43`（reviewer 亲读）；实测 test_m7s2_split.py 53 passed |
| 真实 manifest 测试 skip 条件 | ✓ 显式 skip 非空壳 | manifest 缺失时 `pytest.skip("本机真实 manifest 缺失（local_artifacts）")`；本机在位，实测 1 passed |
| **调用方补**：`component_convergence` 签名与调用点 | ✓ 一致 | `qc_priority.py:136`（`interaction_metrics: dict \| None, interaction_cfg: dict`）；调用点 `:341` 传 `(im, icfg)`，`im` 为 dict \| None（:341-342 isinstance 守卫） |
| **调用方补**：`record_review_decision` 签名与调用点 | ✓ 一致 | `qc_reflux.py:25`（`settings, decision, path=None`）；调用点 `:68` 传 `(settings, d, path=p)` |
| **调用方补**：import 环风险（holdout → models） | ✓ 无环 | `models.py` 不 import holdout；`interaction_metrics`/`qc_priority` 顶层只引 numpy/内部模块，实测 import 成功（53 passed 含两模块全路径导入） |
| **调用方补**：w3/w4 调用链 | ✓ 一致 | `qc_priority.py:344-345`：`w3, per_class = component_disagreement(...)` → `w4 = component_low_freq_presence(per_class, im, per_class_case_count)`，nit-2 所在函数被正常调用 |

## 未覆盖项

- 测试运行（无 Bash）：由调用方补——`test_m7s2_split.py` + `test_qc.py` 53 passed、四 API 调用方文件 94 passed、全量后台运行见报告。
- M6-C/D/F-REPORT.md / M7-S2-REPORT.md 引用行号准确性：报告引用的代码行由调用方亲读核实（见 M7-S2-REBASE-REPORT.md 七项意图证据表，全部为亲读行号）。
