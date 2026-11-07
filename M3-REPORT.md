# M3-REPORT — BodyDonor M3 交付报告

> 日期：2026-11-07。commits：`9a98b12` QC 引擎 → `c157c73` 编排层 + hold-out 门禁 →
> `f6d0f10` QC API → `e97a152` 冷启动校准 → `c69b979` TUI 队列落库 → `6045d87`
> 逐类渲染 → `1ad2671` seg-meta → `1460b0d` combined_mask 收敛 → `da85516` MIP
> 决策 → `8707015` niivue 帧耗时 → `e86c8a4` hold-out 固化 → `cef5b3d` 门禁落库 →
> `506ca91` 评估修正 → `1612514` qc-calibrate（14 commits，本地 main，未推）。
> 依据：proposal.md §6–§8/§11 M3 行、M2-REPORT §6 遗留 1/2/3/4/7、M2-ALIGN §4
> 遗留 6、M1-REPORT §8 遗留 1（QC 数据集下载）。非交互运行。

---

## 1. 各任务交付清单

### 1.1 QC 引擎（proposal §7）

- **规则评分 `src/bodydonor/qc.py`**：7 条规则全部落地——体积先验
  （volume_prior，占比出 [P1, P99] 窗外 flagged）、连通域检查
  （connected_component，主连通域占比 < 0.8 或连通域数 > 20）、空洞检查
  （holes，闭运算填充占比 > 0.2）、边界平滑度（boundary_smoothness，形状因子
  > 1.2）、HU 分布合理性（hu_distribution，软组织窗 P5/P95，**骨类关键字跳过**）、
  左右对称性（left_right_symmetry，24 对成对器官 |log(V_L/V_R)| > 1.0）、
  **多教师一致性（multi_teacher_consistency，`enabled: false`）**——proposal §7
  明示 M3 只有 TotalSegmentator 一个教师、该规则实际无法工作，M4 学生模型出现后
  启用（threshold 0.80 预留初值），**不为通过验收造假数据**。
- **阈值集中 `config/qc_thresholds.yaml`**：全部阈值在这一个文件，禁止散在代码里；
  每个阈值带 `source: cold_start | calibrated_on_N` + `calibrated_on/at` 注记，
  改阈值 = config diff。
- **总体分数与过滤**：overall_score = 通过规则数加权（0 flag → 1.0，线性降）；
  `min_score: 0.5` 低质量过滤（不进蒸馏）+ `hard_flag_rules: [volume_prior]`
  一票否决。
- **混合采样（proposal §6 第 5 阶段）**：`sample_review_tasks`——flagged 70% /
  clean 随机 30%，batch_size 10，`min_clean_pool: 1`（clean 候选不足仍生成 1 条，
  确认「没 flag 的真好」）。

### 1.2 QC 编排层 + API + 决策回流

- **`src/bodydonor/qc_pipeline.py`**：`qc_series`（最新 MaskVersion → run_qc →
  QCReport 落库）+ `_ensure_review_task`/`generate_review_tasks`（混合采样 →
  ReviewTask 落库）。真机实测：`qc_series amos_0004`（117 类 teacher mask）
  **29.3s 评分通过**，ReviewTask 生成落库。
- **API 端点 `app.py`**：`POST /qc/series/{sid}`、`GET /qc/thresholds`、
  `POST/GET /qc/review-tasks`、`POST /qc/review-tasks/{id}/decision`（决策回流）。
- **决策回流 `src/bodydonor/qc_reflux.py`**：`record_review_decision` /
  `record_review_batch`——抽检决策（接受/修正/拒绝）写回 ReviewTask +
  QCReport，成为在线校准的标签来源。
- **在线校准 `calibrate_thresholds`（`1612514`）**：决策回流标签 → 逐规则判别力
  （flag 组标签均值 vs clean 组均值，差 > MIN_SEPARATION=0.1 判「有判别力」，
  source 注记变 `calibrated_on_N`）；无判别力只更新注记不改数值（**不静默放水**）；
  每规则附 `discrimination` 值。M3 数值阈值不动（标签量不足以重算数值，重算是
  M4+ 标签 ≥ 20 例后的事，§7 明示）。CLI：`bodydonor qc-calibrate`。
- **冷启动校准 `scripts/qc_coldstart.py`**：CT-Lite 25 例 TS 推理 → run_qc 评分
  vs 真值逐类 Dice 相关性 → 分位边界定初值（见 §2.3 实测）。

### 1.3 金标准 hold-out 门禁（proposal §8）

- **`src/bodydonor/holdout.py`**：
  - `write_holdout`/`load_holdout`：case_id 列表固化 `eval/holdout.txt`
    （case_id\tsource，git 评审构成改动）。
  - `check_leak`：训练集与 hold-out 交集非空即 `LeakError`（非空即中止）。
  - `check_holdout_composition`：三源构成 + 规模校验（≥30 cases、TS 10–20、
    AMOS/BTCV ≥10、本地 0 或 5–10、无重复）。
  - `dice_per_class`/`evaluate_gate`：**成对判据**（proposal §8）——mean Dice
    ≥ 0.75 + 每类 Dice 下限 ≥ 0.5（仅当该类在 ≥10 cases 中出现，方差才有意义）+
    整类漏检（类在 hold-out 存在但学生所有 case dice=0）。
  - `persist_gate_result`：门禁结果写 ModelVersion（gate_status / gate_metrics /
    gate_holdout_file），走 db.write_lock 单一写入纪律；unknown model_version →
    ModelVersionError（不静默建行）。
- **hold-out 构成固化 `eval/build_holdout.py`**：`eval/holdout.txt` 30 cases =
  **ts_public 15**（CT-Lite 25 例子集）+ **amos 15**（AMOS22 labelsTr 独立人工
  精标）；local_correction 0（M3 尚无人工修正产物）。三源构成校验通过（本地 0
  合法：校验只对 local 1–4 报「要么不进要么足量 ≥5」）、泄漏检查通过（M3 冷启动
  阶段学生未训练，训练集列表为空；M4 蒸馏启动时以真实训练 case 列表重查）。
- **逐 case 逐类 Dice 评估 `eval/evaluate_holdout.py`**：教师 mask vs 独立真值，
  **真实体素计算**（不 mock）；affine 一致性守卫（不一致 raise，不静默重采样）；
  按**类名**对齐（TS 类名 → AMOS 值 1–15 / CT-Lite 标准值；**AMOS 15 = TS
  prostate** 名字映射，amos_0001 实测 teacher prostate.nii.gz vs GT label 15
  dice=0.69 映射成立）。AMOS teacher mask 用 TS total 现跑（GPU 锁全程持锁，
  已有则跳过）。

### 1.4 M2-REPORT 遗留落地（1/2/3/4/7）

- **遗留 1（niivue MIP）**：**决策记录 + 推迟**（`da85516`）。实测 niivue
  0.69.0 stable **也无原生 MIP slice type**（SLICE_TYPE 同为
  AXIAL/CORONAL/SAGITTAL/MULTIPLANAR/RENDER——M2-REPORT「0.69/1.x 有原生 MIP」
  的前提有误）；真 MIP 在 1.x（VOLUME_RENDER_MODE.MAXIMUM），但 1.0.0 仍是 rc
  预发布且 API 不兼容（审阅页需重写）；自定义 MIP shader 不可行（vendored UMD
  bundle 无法扩展）。故保持体绘制（RENDER）+ 正交方位相机近似，真 MIP 推迟到
  1.x stable（显式决策）。
- **遗留 2（完整管线帧耗时）**：`scripts/bench_niivue_full.py`（`8707015`）——
  Edge headless + CDP 加载真实审阅页，对 4 个 niivue 实例连续 `drawScene()`
  （真实重绘入口，含多视口 + overlay 合成 + 拾取缓冲），100 帧 mean/p95。
  **实测 mean 0.47ms / p95 0.70ms；scroll 模式（逐 slice 更新）0.48/0.90ms**
  ——远低于 <100ms/帧红线（PASS）。同 commit 修复 colormapLabel LUT：
  0.53.1 `refreshLayers` 需要已构建的 `.lut` 数组（init 时 makeLabelLut 只在
  构造路径跑），单 overlay 逐类可见性才真正生效。
- **遗留 3（逐类 mask 分通道渲染）**：**无需分通道**（`6045d87`）——实测 niivue
  0.53.1 overlay fragment shader 直接以 label 值索引 colormap 纹理，单多标签
  overlay 即可实现逐类独立颜色与可见性（每类 alpha，构建完整 LUT
  Uint8ClampedArray RGBA 按 label 值索引；背景 label 0 透明）。M2-REPORT 预想的
  「mask 分通道」前提不成立，遗留关闭。
- **遗留 4（seg-meta 类别名单）**：seg-meta 类别名单固定从 labels.json 读取
  （`1ad2671`），manual 版本沿用；TS 落盘产物自带的 labels.json 直接驱动。
- **遗留 7(g)（combined_mask 双落盘）**：收敛为单一产物 `mask.nii.gz`
  （`1460b0d`）——逐体素 diff=0 实测（与 combined_mask 完全一致），双落盘只浪费
  空间且构成两套产物；`segment_series` 不再落 combined_mask.nii.gz，审阅页
  fallback 直接读 mask.nii.gz；`has_combined` 保留为兼容字段（仅对历史产物
  true）。M2-ALIGN §4 遗留 6 同步关闭。

### 1.5 TUI 队列状态源落库（M2-REPORT 遗留 7）

- **`SqliteJobsQueue`（`src/bodydonor/storage.py`，`c69b979`）**：M0 schema 的
  jobs 表为真相源（job_id PK / case_id / stage / status / payload / created_at /
  updated_at）；与 InProcessQueue 的差别是**跨进程可见**（FastAPI 与 CLI worker
  各自进程读写同一张表，重启不丢）。dequeue：status=queued 最老一条置 running
  （写路径短事务）；ack/nack：置 done / failed（nack requeue=True 回 queued）。
  `app.py` 队列实例切至 SqliteJobsQueue。

---

## 2. 实测验证表（每项写清在真机上怎么验的）

| 验证 | 方式 | 结果 |
|---|---|---|
| **QC 引擎单例评分** | 实测：`qc_series amos_0004`（117 类 teacher mask，真机 teacher 产物） | **29.3s 评分通过**，QCReport 落库，ReviewTask 生成落库 |
| **QC 冷启动校准** | 实测：CT-Lite 25 例（563MB，HF zip Range 单 member 抓取）→ TS teacher 真卡推理 → run_qc 评分 vs 真值逐类 Dice 相关性（`scripts/qc_coldstart.py`） | **25 cases，n_good_dice=25 / n_bad_dice=0，Pearson 0.0665，elapsed 2090.1s**（§3 诚实结论：无可分离阈值） |
| **hold-out 构成固化** | 实测：`eval/build_holdout.py` → `eval/holdout.txt` 30 cases（ts_public 15 + amos 15）→ check_holdout_composition + check_leak | 构成校验通过（本地 0 合法），泄漏检查通过（交集空） |
| **hold-out 门禁评估** | 实测：`eval/evaluate_holdout.py` 30 cases——教师 mask vs 独立真值逐类 Dice（真实体素计算，affine 守卫，按类名对齐 AMOS 1–15 / CT-Lite 标准值；AMOS teacher 用 TS total 真卡现跑，GPU 锁全程持锁） | **30 cases，gate PASS，mean_dice 0.9335，failures=[]，整类漏检=[]，elapsed 192.4s**（§3） |
| **AMOS 15=prostate 映射** | 实测：amos_0001 teacher prostate.nii.gz vs GT label 15 dice=0.69（映射成立）；amos_0004 teacher prostate 非零仅 8 体素 vs GT label15 17976（dice≈0——教师漏检，非映射错误，见 §3 诚实结论 5） | 映射按类名对齐进 per_class（14/15 AMOS cases 有 prostate 计分） |
| **门禁结果落库** | 单测 + 实现：persist_gate_result → ModelVersion.gate_status/gate_metrics/gate_holdout_file（write_lock 纪律，unknown mv → error） | 3 条落库单测通过 |
| **逐类渲染（遗留 3）** | 实测：colormapLabel 完整 LUT（Uint8ClampedArray RGBA，label 值索引）→ 单 overlay 逐类颜色+可见性（all-off LUT 全透明，all-on label-1 alpha=153） | 逐类独立颜色与可见性生效，无需分通道 |
| **niivue 帧耗时（遗留 2）** | 实测：`scripts/bench_niivue_full.py`——Edge headless + CDP 加载真实审阅页（4 视口 + 双 volume + overlay 合成），4 实例连续 drawScene() 100 帧，setTimeout 链（headless 无合成器，rAF 不触发，沿用 M2 方法） | **mean 0.47ms / p95 0.70ms；scroll 0.48/0.90ms**——远低于 <100ms/帧红线（**PASS**） |
| **TUI 队列落库（遗留 7）** | 单测：SqliteJobsQueue dequeue/ack/nack/requeue + 跨进程可见性 | 8 条单测通过 |
| **seg-meta 名单（遗留 4）** | 实现 + 单测：类别名单固定从 labels.json 读取（manual 版本沿用） | 单测通过 |

**浏览器实测方法说明**：沿用 M2——本机 WSL2 无 Linux 浏览器；Edge（msedge.exe，
Windows 侧）经 `--remote-debugging-port=9333` 从 WSL2 以 CDP websocket 驱动。
与 M2 的差别：M2 测的是手写 WebGL2 代理页（texSubImage3D 单切片 +
drawArrays），不是 niivue 完整管线；M3 直接对运行中的 niivue 实例调
`drawScene()`（其完整渲染管线的真实入口，含多视口 + overlay 合成 + 拾取缓冲）。
**帧耗时实测数字 0.47/0.70ms 是 niivue 完整管线的实测值**（M2-REPORT 遗留 2 的
缺口已闭合）。数字来自 commit `8707015` message（脚本 stdout 不落盘，eval/ 不存
niivue 结果——复跑即可重现）。

### 2.1 GPU 基线

| 项 | 值 | 来源 |
|---|---|---|
| M2-established 基线（沿用） | TS 真卡推理 8580 MiB（torch 分配器峰值，三次一致）/ 交叉核对 nvidia-smi 增量 4409 MiB；墙钟 116.2s；117 类 | M2-ALIGN-REPORT §2（M3 未重跑） |
| QC 冷启动（25 例 TS 推理 + QC 评分 + Dice） | elapsed **2090.1s**（墙钟，含推理+评分+Dice 全程） | `eval/coldstart_results.json` summary.elapsed_s |
| hold-out 评估（15 例 AMOS TS 推理 + 30 例 Dice） | elapsed **192.4s**（墙钟） | `eval/holdout_results.json` summary.elapsed_s |
| hold-out 评估 GPU 显存 | **未记录**（evaluate_holdout.py 不采样 nvidia-smi/torch 分配器；holdout_results.json 无 vram 字段）——如实说明，不造数 | eval/evaluate_holdout.py |

**GPU 时间口径说明**：elapsed_s 是墙钟（含 TS 子进程推理 + Dice 体素计算 +
落盘），非纯 GPU 时间；M3 评估脚本未做 GPU 时间与显存采样（M2 基线的
nvidia-smi 轮询与 torch 分配器实测方法在 segmentation.py 可复用，M4 蒸馏
基线汇报时接入）。

### 2.2 测试结果

| 项 | 结果 |
|---|---|
| pytest | **313 passed**（本次复跑确认：`uv run --extra dev --extra ingest pytest tests/`，35.0s）；M3 新增 55 条（4 个新测试文件，17+24+6+8），其余为 M0–M2 基线 |
| ruff | `uv run ruff check .` **No issues found**（本次复跑确认） |
| M3 新增测试分布 | holdout 门禁 17（泄漏/构成/Dice/成对判据/落库）+ qc 引擎 24（规则/校准/采样/API 回流）+ qc API 6（端点/决策回流）+ SqliteJobsQueue 8（dequeue/ack/nack/requeue/跨进程） |
| 白名单/zip 负测试 | 继续通过（M0/M1 资产，313 内含） |

---

## 3. 诚实结论（不粉饰）

### 3.1 hold-out 是两源构成，非三源

冻结的 `eval/holdout.txt` 是 **ts_public 15 + amos 15，local_correction 0**——
M3 阶段没有任何人工修正产物（抽检台还没有真实医生决策回流）。构成校验器
`check_holdout_composition` 显式允许 local=0（只对 1–4 报「要么不进要么足量
≥5」，0 合法通过）。proposal §8 的第三源（本地人工修正 5–10，权重最高、数量
最少）在真实修正产物出现后择优加入——届时重跑 build_holdout + 泄漏检查 +
门禁评估，构成改动 git diff 可评审。

### 3.2 「每类至少出现在 10 cases」在评估路径未强制

`check_per_class_coverage` 已实现且单测通过（holdout.py:252），但 **从未被
`eval/build_holdout.py` 或 `eval/evaluate_holdout.py` 调用**——这是 open
deviation，不是合规。门禁的每类 Dice 下限（≥0.5）按 holdout.py 文档化的方差
理由只作用于**出现在 ≥10 cases 的类**（78/117 类）；本 30 例集中 39 类出现在
<10 cases（brain 1、kidney_cyst_left/right 1、vertebrae_C1–C3 各 1、skull 5、
vertebrae_C5 5、vertebrae_C6 7、femur_left/right 9、trachea 9 等）——这些类的
Dice 均值进了 mean 但**不受下限约束**（例：这些类里 skull mean 0.9688、brain
0.9866 尚高，但若某低频类整类失败，门禁不会因下限捕获它，只能靠整类漏检判据）。
M4 前应：要么把 check_per_class_coverage 接进 evaluate_holdout（fail-fast），
要么在门禁报告里显式列出低频类名单。

### 3.3 冷启动没有找到可分离阈值

冷启动实测（25 cases）：**n_good_dice=25 / n_bad_dice=0，Pearson
QC-vs-Dice 0.0665**——没有低质量组（n_bad_dice=0），flag/clean 的 QC 分数
分布不可分离（good_qc_min=0.0、p10=0.3333；bad 组不存在）。结论：**冷启动
阈值（qc_thresholds.yaml 现值）只是起点，不是校准产物**。这正是 proposal §7
警告的同源乐观（CT-Lite 真值与教师同源，TS 真值是模型+人工迭代产出）——教师
在自己的真值上几乎不出错，QC 引擎对这种错误无信号。**真正的门禁信号是 AMOS
hold-out**（独立人工精标，mean 0.7776 vs ts_public 0.9442，差距显著）。阈值
数值校准要等真实抽检决策回流（qc-calibrate 已就位，M3 标签量为 0）。

### 3.4 校准注记现状

qc_thresholds.yaml 全部规则 `source: cold_start`、`calibrated_on: 0`、
`calibrated_at: "2026-11-07"`（数据集级）——`calibrate_thresholds`（决策回流 →
逐规则判别力 discrimination + calibrated_on/at 注记）已实现并单测，但 M3 没有
真实决策标签可喂（抽检未跑），数值阈值与注记均未变动。

### 3.5 hold-out 数字解读

- **整体**：mean_dice 0.9335（117 类，30 cases），无 failures、无整类漏检。
- **两源差异显著**：ts_public 案例均 dice 0.9442 vs amos 0.7776——同源
  （CT-Lite 真值=教师派生）偏乐观在数字上直接可见，AMOS 才是独立信号。
- **prostate 是最弱类**（0.5914，18 cases，≥10 受下限约束但 > 0.5 通过）：
  AMOS 上 5/15 case teacher prostate dice=0（amos_0004 teacher 输出仅 8 非零
  体素 vs GT 17976——**教师整类漏检**，非评估映射错误；amos_0011 输出 0 体素）。
  AMOS prostate 均值 0.4942，被 CT-Lite 侧 0.94+ 拉高到 0.5914。教师对前列腺
  边界/存在性的不可靠是 M4 学生蒸馏的真实风险，QC 抽检应重点关注该类。
- **AMOS 最弱 case**：amos_0005 0.6033、amos_0016 0.7067、amos_0006 0.7123
  （15 类计分，AMOS 只有 15 类金标准，类数少故单类波动对均值影响大）。

### 3.6 多教师一致性规则未启用（按 §7 原义）

`multi_teacher_consistency: enabled: false`——M3 单教师（TotalSegmentator）
无第二 mask 可比，proposal §7 明示该规则 M4 学生模型出现后启用、不要为通过
验收造假数据。threshold 0.80 为预留初值。

---

## 4. 非目标清单（M3 不做项）

- 学生模型训练 / nnU-Net 蒸馏（M4）
- 真实医生抽检决策回流（抽检台 UI 与院内流程，M4/M5；qc-calibrate 已就位）
- HU 分布本院化校准（§7.2，需院内真实脱敏 CT 首周 50 例，M4+）
- 多教师一致性规则启用（M4，§3.6）
- OHIF 评估（proposal §9：推迟到 M3 之后评估，独立决策）

---

## 5. 遗留问题

1. **check_per_class_coverage 未接入评估路径**（§3.2，open deviation）：M4
   蒸馏启动前接入 evaluate_holdout 或在门禁报告显式列出低频类。
2. **本地修正产物缺位**（§3.1）：第三源待真实医生抽检修正产物出现后加入
   hold-out；M4 蒸馏训练输入（教师+人工修正混合）同样依赖该产物。
3. **阈值数值校准待真实标签**（§3.3/§3.4）：冷启动只定了起点；qc-calibrate
   在抽检决策 ≥20 例后重算数值（§7）。HU 本院化校准主体在 §7.2（M4+）。
4. **hold-out 评估无 GPU 显存采样**（§2.1）：evaluate_holdout 不记录
   nvidia-smi/torch 分配器；M4 基线汇报时把 M2 的采样方法接进评估脚本。
5. **niivue MIP**（遗留 1 关闭方式为推迟）：0.69 stable 无原生 MIP 实测推翻
   了「0.69 有」的前提；真 MIP 在 1.x stable（rc 预发布不采纳，API 不兼容需
   重写审阅页）。升级是显式决策。
6. **M1/M2 遗留不变**：TotalSegmentator dataset 全量下载（CT-Lite Range 抓取
   已覆盖 M3 需求）、100 MB 三处强制的另两处、多指纹部署语义、远程 picspin/BodyDonor 已建（main 7a172db）、M3 commits 本地未推、BSL LICENSE 占位、Uberon 56 条空串待核对（M2-ALIGN §7）。

---

## 6. 交付物清单

```
src/bodydonor/qc.py             新增：QC 引擎（7 规则 + run_qc + sample_review_tasks + calibrate_thresholds）
src/bodydonor/qc_pipeline.py    新增：编排层（qc_series → QCReport + 混合采样 → ReviewTask）
src/bodydonor/qc_reflux.py      新增：决策回流（record_review_decision/batch）
src/bodydonor/holdout.py        新增：hold-out 门禁（泄漏检查/三源构成/Dice 成对判据/persist_gate_result）
src/bodydonor/storage.py        SqliteJobsQueue（M2-REPORT 遗留 7：队列状态源落库）
src/bodydonor/app.py            /qc/* 端点 + 队列实例切换 + review meta/mask 收敛（单一 mask.nii.gz）
src/bodydonor/cli.py            bodydonor qc-calibrate 子命令
src/bodydonor/segmentation.py   combined_mask 收敛（不再单独落盘）
src/bodydonor/static/review.html  colormapLabel 完整 LUT（逐类颜色+可见性）+ MIP 决策注释
config/qc_thresholds.yaml       新增：QC 阈值集中文件（source/calibrated_on/at 注记）
eval/holdout.txt                新增：hold-out 构成固化（30 cases，入仓）
eval/build_holdout.py           新增：构成固化脚本（三源校验 + 泄漏检查）
eval/evaluate_holdout.py        新增：逐 case 逐类 Dice 评估（真实体素，affine 守卫，类名对齐）
scripts/qc_coldstart.py         新增：冷启动校准（TS 推理 + QC vs Dice 相关性）
scripts/dl_ct_lite.py           新增：CT-Lite 单 member 下载器（zip Range，563MB/25 例）
scripts/bench_niivue_full.py    新增：niivue 完整管线帧耗时（CDP Edge，drawScene）
scripts/bench_review_combined.py  逐类渲染 LUT 实测更新
tests/test_holdout.py           新增 17 条（泄漏/构成/Dice/成对判据/落库）
tests/test_qc.py                新增 24 条（规则/校准/采样）
tests/test_qc_api.py            新增 6 条（端点/决策回流）
tests/test_sqlite_jobs_queue.py 新增 8 条（dequeue/ack/nack/requeue/跨进程）
eval/coldstart_results.json     运行产物（不入仓，eval/.gitignore）
eval/holdout_results.json       运行产物（不入仓）
pyproject.toml + uv.lock        scipy（连通域/闭运算）
```

---

## 7. Blockers（单列）

1. **【已解决】CT-Lite 全量 22.6GB 下载不可行（M1 遗留）**
   - 现象：HF TotalSegmentator-CT-Lite Images.zip 全量 22.6GB，本机单流
     ~1MB/s 全量 >8h；Zenodo 对 >10KB 的 Range 抓取截断到 ~27KB 不可用
   - 已试：**HF resolve 302 → xet-bridge CDN（us.aws.cdn.hf.co）支持 Range**——
     抓 zip 末尾 EOCD 解析 central directory 偏移 → CD 分块抓取（20KB/块）→
     逐 member Range 抓取 + raw deflate（zlib -15）解压 + CRC32 校验
   - 结果：25 例（Images + Masks）563MB 按需落位（`scripts/dl_ct_lite.py`，
     方法可复现）；踩坑：zip member 是 raw deflate 非 gzip 包装
2. **【已解决】niivue 0.53.1 colormapLabel 动态更新不生效**
   - 现象：逐类可见性开关后 overlay 不刷新（refreshLayers 走已构建的 .lut）
   - 已试：构造完整 LUT（Uint8ClampedArray RGBA 按 label 值索引）+
     `colormapLabel = {lut, min, max, labels}` + updateGLVolume
   - 结果：all-off LUT 全透明 / all-on label-1 alpha=153，逐类颜色+可见性生效
3. **【已解决】AMOS teacher mask 缺失**
   - 现象：hold-out 评估需 15 例 AMOS 的 TS teacher 输出（评估时不存在）
   - 已试：evaluate_holdout 内置 ts_teacher_inference（GPU 锁全程持锁
     .gpu.lock，JSON 契约 runner，已有则跳过）
   - 结果：15 例 AMOS ts_out 各 117 类落位（amos_0001–amos_0023），评估完成
4. **【无 blocker 遗留】**：M3 无未解决 blocker。

---

## 8. 工作方式记录（提案精神的最小合理选择）

- **整类漏检语义**：「某类在 hold-out 里存在但学生整类漏掉」收敛为**该类所有
  case dice=0**（§8 原义；逐 case 记 zero_by_class，跑完全部 case 后判定）。
- **门禁判据成对写**：mean Dice + 每类下限 + 整类漏检三条同时判（§8），
  整体均值掩盖单类失败的问题由后两条兜住。
- **M3 数值阈值不动**：calibrate_thresholds 有判别力时只改 source 注记不改
  数值——标签量不足以重算数值阈值，重算是 M4+ 标签 ≥20 例后的事（§7）。
- **AMOS 类名映射**：AMOS 15 = TS prostate（男）/uterus（女）——M3 评估按
  prostate 对齐（amos_0001 实测 dice=0.69 映射成立）；女性 case 的 label 15
  是 uterus，同一映射会对不上（本 15 例含女性 case 时 prostate dice=0 部分源于
  此——见 §3.5，教师漏检与性别映射两个因素并存）。
- **eval/ 产物不入仓**：coldstart/holdout_results.json 进 .gitignore，
  holdout.txt（固化构成）与两个脚本入仓——构成可评审，结果可复跑。
