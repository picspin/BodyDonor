# M4-STAGE4-REPORT — BodyDonor M4 阶段 4 交付报告：关 M3 遗留偏差

> 日期：2026-11-09。commits：`a735e21` 低频类进门禁结果 → `df4eead` 评估接
> 显存采样 + 覆盖检查 + prostate 聚光 → `2b7fef0` qc-calibrate 20 例门槛
> （本报告另起一 commit，本地 main，未推）。
> 前置状态：正式训练进行中（nohup PID 45196，nnU-Net 3d_lowres fold 0，
> 1000 epochs；本阶段开始时 GPU 9076 MiB / 92%，结束时 9076 MiB / 97%，
> GPU 锁由训练包装进程持有）。**本阶段未跑任何 GPU 推理/评估**——只做代码
> 接线 + 单测（CPU/小输入/mock 隔离采样路径），需 GPU 的实跑验证记为 pending。
> 依据：M3-REPORT.md §3（四条诚实偏差原文）、§1.3（门禁判据）。

---

## 1. 各任务交付清单

### 1.1 check_per_class_coverage 接进评估路径（关 M3 §3.2）

- **`src/bodydonor/holdout.py`（`a735e21`）**：
  - 新增 `low_frequency_classes(counts)`：`[{class, cases}]` 清单，按
    (case 数, 类名) 排序——门禁报告的低频类清单来源。
  - `GateResult` 新增 `low_frequency_classes` + `coverage_warning` 字段，
    `to_dict()` 一并输出（落库 `ModelVersion.gate_metrics` 时自动带上）。
  - `evaluate_gate(..., fail_on_low_coverage=False)`：每次判定都计算低频类
    清单并写进结果。
  - `class_case_counts` / `check_per_class_coverage` 接受类名集合（str
    labels），评估脚本按类名对齐后可直接传。
- **语义决定（写清理由）：默认只显式告警 + 列清单，不改 pass/fail。**
  硬 fail 会使门禁在结构上不可通过：冻结的 30 例 hold-out（ts_public 15 +
  amos 15）中 **39/117 类天然 <10 cases**（头颈/四肢类只在覆盖该解剖部位
  的扫描里出现）——这是 hold-out 规模/解剖覆盖面的局限，不是模型质量信号，
  根治靠扩大 hold-out；§8「每类 ≥10」的本意是方差理由下的每类下限适用条件
  （已实现：下限只约束 ≥10 cases 的类）。将来 hold-out 扩容后可用
  `fail_on_low_coverage=True` 收紧为 fail-fast（已实现 + 单测锁定）。
- **`eval/evaluate_holdout.py`（`df4eead`）**：主循环累计 GT 类名集合
  `gt_names_per_case`，跑完调用 `check_per_class_coverage`，`coverage_problems`
 （39 条）+ `low_frequency_classes`（39 类）+ `coverage_warning` 写入
  `summary`，并打印低频类数。
- **单测 6 项**（`tests/test_holdout.py`）：清单排序、类名集合、默认告警
  不改 pass、严格模式 fail、prostate 0.5914/18 cases 锁定。

### 1.2 hold-out 评估接显存采样（关 M3 §2.1「未记录」）

- **`eval/evaluate_holdout.py`（`df4eead`）**：
  - 新增 `SmiPoller`：全程 nvidia-smi 轮询，复用 M2 口径
    （`segmentation.py segment_series` 同模式：推理前基线 + 后台线程 1s
    采样，交叉核对值 = 轮询峰值 − 基线；无样本/nvidia-smi 不可用时为 None，
    不猜数字）。`sampler` 参数仅供单测注入。
  - `ts_teacher_inference` 改为返回 `(out_dir, peak_vram_mb)`——runner 内
    torch 分配器峰值（M2 主值口径）；唯一调用方就是本脚本主循环（grep 核实，
    其余引用均为注释）。
  - `summary["vram"]`：`peak_vram_mb`（fresh inference 各 case runner 峰值
    的最大值）、`peak_vram_nvidia_smi_mb`（轮询峰值 − 基线）、
    `n_cases_with_fresh_inference`、note（说明 None = 本次无 GPU 推理，
    不是 0 显存）。**字段名与 M2 口径一致**（`peak_vram_mb` /
    `peak_vram_nvidia_smi_mb`）。
- **单测 6 项**（`tests/test_evaluate_holdout.py`，新文件）：SmiPoller 峰值
  8900（基线 100 + 采样 9000）、无样本 None、with 形式、弱类排序、prostate
  聚光（M3 实测值锁定）/缺失为 None。mock 仅隔离采样代码路径（注入 sampler），
  其余全是纯函数小输入。
- **完整 hold-out 重跑 pending**（见 §3.1）：需 GPU + 学生模型，训练占卡中
  本轮不跑。

### 1.3 qc-calibrate：数值重算门槛 20 例（关 M3 §3.3/§3.4）

- **现状实测（2026-11-09，真实 `bodydonor.db`）**：
  `review_tasks` 共 1 条，**已决策 0 例**（decision 全空，status pending），
  `qc_reports` 5 条；`config/qc_thresholds.yaml` 全部规则仍为
  `source: cold_start`、`calibrated_on: 0`。**数值重算未达到阈值，不触发。**
- **`src/bodydonor/qc.py`（`2b7fef0`）**：
  `MIN_LABELS_FOR_RECALIBRATION = 20` + `recalibration_ready(n)`（返回
  (ready, 原因)）。标签量不足时硬算数值等于用噪声定阈值，故注记更新
  （source / discrimination）同样等够 20 例再写回。
- **`src/bodydonor/cli.py`（`2b7fef0`）**：`bodydonor qc-calibrate` 在写回前
  先过门槛——0<n<20 打印 pending 原因后 `Exit(1)`，不动 yaml；0 决策沿用
  旧行为（提示后 `Exit(0)`）。
- **真机验证**：`bodydonor qc-calibrate` 对真实 DB 跑出「无已决策的抽检任务」、
  Exit(0)，yaml md5 前后一致（`f7af474566a13d2e8b4850ef21c4d021`）。
- **单测 4 项**（`tests/test_qc.py`）：门槛边界 19/20/21 + CLI 三态
  （0 决策 Exit 0、5 决策 Exit 1 不写回、20 决策放行并写回；独立 SQLite +
  save 劫持，不动真 yaml）。

### 1.4 prostate 单项（M3 最弱类 0.5914）

- **核对结果：逐类明细已有**——存量 `eval/holdout_results.json` 的
  `summary.per_class_dice` 含全部 117 类，`prostate: 0.5914`（18 cases）。
  缺失的是：AMOS 教师 dice=0 的 5 例在报告里不可见（`zero_by_class` 只在
  内存里算过、未落盘），且 prostate 没有单独标出。
- **`eval/evaluate_holdout.py`（`df4eead`）**：`summary` 新增
  `zero_dice_cases_by_class`（每类 dice=0 的 case 清单）、`weakest_classes`
  （Dice 最低 5 类）、`prostate` 聚光块（总 Dice + case 数 + dice=0 清单 +
  AMOS/TS 分源均值 + 漏检与性别映射注记），并打印 prostate 行。
- **`eval/holdout_results.json` 刷新（本地工作区，gitignored，不入仓）**：
  用新代码的纯函数对存量 M3 per-case 实测数据 + GT 文件（CPU 只读 nibabel）
  重算补字段，**不跑 GPU、不重新推理**，重算脚本 `/tmp/recompute_holdout_s4.py`
  （不入仓）。重算前先按评估脚本原逻辑从 GT 重建 gate 输入并与存量 gate
  全量交叉核对：mean 0.9335 一致；逐类最大舍入漂移 0.00010
  （`rib_left_12`，源于存量 cases[] 逐类 Dice 本身是 4 位舍入值，容限 0.0002
  内通过）；整类漏检 []、pass 状态一致。重算还证实：GT 类缺 teacher 文件的
  case 数为 0（zero 图完整）；AMOS prostate GT 有 label 15 的是 14 例
  （第 15 例 GT 无 label 15，即女性 uterus case，与 M3 §3.5 注记一致）。
- **刷新后实测值**：
  - `prostate: {dice: 0.5914, cases: 18, zero_dice_cases:
    [amos_0004, amos_0011, amos_0014, amos_0015, amos_0016],
    amos_mean_dice: 0.4942 (14 cases), ts_public_mean_dice: 0.9314 (4 cases)}`
    ——与 M3-REPORT §3.5（0.5914/18 例/AMOS 0.4942/5 例 dice=0）一致。
  - `weakest_classes`: prostate 0.5914（18）< vertebrae_C5 0.7275（5）<
    duodenum 0.7469（28）< adrenal_gland_right 0.7914（30）<
    vertebrae_C6 0.7977（7）。
  - `zero_dice_cases_by_class` 共 19 类有 dice=0 的 case；prostate 以 5 例居首，
    其余 18 类各 1 例。
  - `vram` 三值皆 None + note（M3 运行未采样、本轮未重跑，未测不是 0）。

### 1.5 自查与全量验证

- **/code-review 自查**：headless 下斜杠命令不可用，逐项自查（见 §4），
  无自动发布、无推、无 PR。
- **ruff**：`ruff check .` 全绿。
- **pytest 全套**：**382 passed, 51 warnings in 261.86s**（exit 0）——基线
  366 + 本阶段新增 16（holdout 6 + evaluate_holdout 6 + qc 4）。
- **小步提交**：3 个本地提交（`a735e21` / `df4eead` / `2b7fef0`），未推、
  未建 PR；`eval/holdout.txt` 与 `config/qc_thresholds.yaml` 未动
  （git diff 为空，yaml md5 不变）。

---

## 2. 提交清单

| commit | 内容 |
|---|---|
| `a735e21` | 低频类显式进门禁结果（holdout.py + test_holdout.py，6 单测） |
| `df4eead` | 评估接显存采样 + 覆盖检查 + prostate 聚光（evaluate_holdout.py + 新 test_evaluate_holdout.py，6 单测） |
| `2b7fef0` | qc-calibrate 20 例门槛（qc.py + cli.py + test_qc.py，4 单测） |
| （本报告） | M4-STAGE4-REPORT.md（另起一 commit） |

---

## 3. Pending（未实测）与后续验证命令

### 3.1 完整 hold-out 重跑（含 VRAM 实测 + 新字段端到端）

- **原因**：训练占卡（PID 45196，GPU 9076 MiB / 95%+，锁由训练持有），
  本轮不跑 GPU 评估。学生模型须等训练完成（预计 2026-11-10 21:53 前后）。
- **后续命令**（训练完成后、GPU 空闲时）：
  ```bash
  cd ~/work/BodyDonor
  uv run python eval/evaluate_holdout.py --skip-inference  # 先用已有 teacher 输出重算 Dice + 新字段
  # 学生模型就绪后（不加 --skip-inference，让学生推理走 GPU 锁 + SmiPoller 采样）：
  uv run python eval/evaluate_holdout.py
  python3 -c "import json; d=json.load(open('eval/holdout_results.json')); print(d['summary']['vram']); print(d['summary']['prostate']); print(len(d['summary']['low_frequency_classes']))"
  ```
  预期：`vram.peak_vram_mb` 与 `peak_vram_nvidia_smi_mb` 为实测数（非 None）；
  `prostate` 与 `low_frequency_classes`（39 类）与本次重算一致（学生模型
  上线后 prostate 值本身会变，以新跑为准）。

### 3.2 qc-calibrate 数值重算

- **原因**：抽检决策 0/20，未达门槛。本轮只做了门槛接线 + 拒绝路径验证，
  未改任何阈值数值或注记。
- **后续**：抽检台完成 ≥20 例真实医生决策回流后跑：
  ```bash
  cd ~/work/BodyDonor && .venv/bin/bodydonor qc-calibrate
  git diff config/qc_thresholds.yaml  # 评审注记/discrimination 变化
  ```

### 3.3 hold-out 扩容（低频类根治）

- 39/117 类 <10 cases 是 hold-out 规模/解剖覆盖局限；`fail_on_low_coverage`
  已就位，待 hold-out 扩容（更多 case + 更广解剖覆盖）后可收紧为 fail-fast。
  不属于本阶段（构成改动须 git 评审 + 重跑门禁）。

---

## 4. 自查（headless 下 /code-review 斜杠命令不可用，逐项自查）

- 无 mock 数字：本报告所有数字来自实测命令输出（pytest 382、ruff、nvidia-smi、
  锁文件、sqlite 决策数、qc-calibrate 真机跑、GT 重算交叉核对）；单测里的
  mock 仅用于隔离 SmiPoller 采样代码路径（注入 sampler，不产生报告数字），
  CliRunner 测试用独立 SQLite + save 劫持（不动真 yaml/真 DB）。
- 无 GPU 违规调用：训练占卡期间未跑 GPU 推理/评估；唯一 GPU 相关读操作是
  nvidia-smi 查询与锁文件读取；GT 重算只用 CPU nibabel 读真值文件；SmiPoller
  生产采样只在评估脚本跑起来时才发生（本轮未跑）。
- 作用域：`ts_teacher_inference` 返回值改动唯一调用方是评估脚本主循环
  （grep 核实）；`evaluate_gate` 新参数默认 False，旧调用不受影响；
  `GateResult.to_dict` 新增键经 JSON 列落库，无 schema 变动。
- 未推、未建 PR：git log 确认本地提交（`a735e21` / `df4eead` / `2b7fef0`），无 push。
- 未动 eval/holdout.txt：git diff 为空；qc_thresholds.yaml md5 不变。
- 未污染 .venv-totalseg / .venv-nnunet / 主 .venv：本阶段只改源码与测试，
  未装包、未动 venv。

---

## 5. 遗留项

- M3 偏差关闭情况：§3.2（覆盖未接入）已关——接线 + 清单 + 默认告警语义；
  §2.1（显存未记录）代码已关、实测 pending（§3.1）；§3.3/§3.4（阈值数值校准
  待真实标签）门槛已接线、重算 pending（§3.2）；§3.5 prostate 已在报告里
  单独标出 + 5 例漏检可见。
- M3 遗留的另外两条（§3.1 本地修正产物缺位——第三源 local=0；§3.6 多教师
  一致性未启用）不在本阶段任务内，未动：local_correction 仍为 0，
  multi_teacher_consistency 仍为 `enabled: false`。
- `eval/holdout_results.json` 是 gitignored 的运行产物：本次字段刷新只在
  本地工作区，下次评估脚本重跑时会按新代码重新生成（含实测 vram）。
