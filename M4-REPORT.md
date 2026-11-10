# M4-REPORT — BodyDonor M4 交付报告

> 日期：2026-11-11。M4 各阶段 commits（本地 main，未推）：`6c28f95` 蒸馏数据集 +
> 许可门禁 → `81c94ac` 蒸馏数据准备 → `3fa871f` stage2b 训练包装 → `53178a1`
> ModelVersion 登记 → `12595b4` 阶段 2b 报告 → 阶段 3 11 commits（`bdaa579`…`d35230c`，
> nnInteractive 接线）→ `a735e21`/`df4eead`/`2b7fef0` 关 M3 遗留偏差 → `f26693e`
> n_target_voxels 修复 → `ebb945f` 学生评估脚本 → `3e4674d` orthofix + input 清理 →
> `27387c8` 门禁分口径 → `094562c` 多教师一致性启用 → 本报告。
> 依据：proposal.md §6–§8 M4 行、M3-REPORT §5 遗留、LICENSES-THIRD-PARTY.md §8
> 蒸馏许可书面结论。各阶段详见 M4-STAGE2B-REPORT.md / M4-STAGE3-REPORT.md /
> M4-STAGE4-REPORT.md。非交互运行；全程未 push、未建 PR、未改 eval/holdout.txt。

---

## 1. 各任务交付清单

### 1.1 蒸馏数据集准备（阶段 2a，`6c28f95` + `81c94ac` 等）

- **`src/bodydonor/distill_data.py`**：训练池构建（holdout.txt 逐 case 排除 +
  `check_leak` 以真实训练列表重查，泄漏即中止）；许可门禁（来源白名单
  total + manual-correction，拒绝 nnInteractive ShareAlike / SegVol 待核实 /
  experimental；逐 case source.json 实测，坏 JSON 不猜来源）——LICENSES §8.3
  约束 1 的代码强制。
- **Dataset501_BodyDonorDistill**：imagesTr/labelsTr 各 35 例、15 类（AMOS 15）、
  dataset.json numTraining=35，落盘 1.4G（du 实测）；类别范围收敛为 AMOS 15 类
  （117 类全量每类样本过低，排除清单进 manifest.excluded_classes，不静默丢类）。
- **训练输入只用 TS total 教师 mask + 纯人工修正**（LICENSES §8.1 商用安全路径）；
  教师推理成功后写 source.json（`a0da6d9`）；ts_out 完整性加严
  （`4602463`，缺蒸馏类即拒绝）。

### 1.2 学生蒸馏训练（阶段 2b，M4-STAGE2B-REPORT）

- **正式训练 exit 0**：118968.1s（≈33.04h），2026-11-09 14:28:59 启动 →
  2026-11-10 23:31:40 完成；`nnUNetv2.run.run_training Dataset501_BodyDonorDistill
  3d_lowres 0 -tr nnUNetTrainer`（标准 1000 epochs）。
- **Mean Validation Dice 0.861775797907179**（fold_0 validation summary.json
  foreground_mean；fold 0 = 28 训练 + 7 验证，nnunet-train-fold0.log）。
- **peak_vram_smi_net_mb 8525.0**（基线 599.0，108767 个 nvidia-smi 样本；
  stage2b-train.json）；nohup PID 45196，GPU 锁 job=nnunet-stage2b-45196 全程
  持有、结束释放。
- **短训先行**（5 epochs 已验证 fp16 稳定：loss 单调下降，Mean Validation Dice
  0.0506 是 5 epochs 早期现象，非稳定性问题）；五次短训修复固化 WSL2 内存红线
  （multiprocessing worker 必须串行 =1，并行 2 worker 即 OOM）。
- **ModelVersion 登记**（`53178a1`）：id `student-nnunet-v2-distill-20261109T062920Z`，
  weights_key 训练完成后回填（§1.6）。

### 1.3 nnInteractive 交互修正后端（阶段 3，M4-STAGE3-REPORT）

- **`nni_runner.py` + `nninteractive_backend.py` + CLI/API 接线**：独立 venv
  （.venv-nninteractive，torch 2.5.1 + nnInteractive 2.6.0），JSON 任务 runner
  照抄 ts_runner 契约；GPU 锁短会话模式 + nvidia-smi 双保险。
- **来源命名 v2**（用户 2026-11-09 决定）：`nninteractive_guided` 规范名，
  `human` 绝不用于 nnInteractive 产物；legacy 别名保留拒绝（门禁不倒退）。
- **provenance sidecar**：sessionId（uuid4 每次新）/ baselineTeacherVersion
  （查不到写 null + 原因）/ prompts 全量 / acceptedClasses。
- **license 展示**：meta 端点 license 字段 + review.html 页脚；模型权重
  nnInteractive_v1.0（393 MB）实测 LICENSE = CC BY-NC-SA 4.0。
- **`MaskVersion.source` String(16) 列宽问题留给 M5-e**（`nninteractive_guided`
  20 字符塞不进，本轮不建行不改列宽，见 §5）。

### 1.4 关 M3 遗留偏差（阶段 4，`a735e21`/`df4eead`/`2b7fef0`，M4-STAGE4-REPORT）

- **check_per_class_coverage 接进评估路径**：低频类清单显式进门禁结果
  （39/117 类 <10 cases），语义 = 默认告警 + 列清单，不改 pass/fail
  （硬 fail 会使门禁结构性不可通过；`fail_on_low_coverage=True` 收紧已实现 + 单测锁定）。
- **评估接显存采样**：SmiPoller 全程轮询（M2 口径：峰值 − 基线）；
  `ts_teacher_inference` 返回 torch 分配器峰值。
- **qc-calibrate 数值重算门槛 20 例**：`MIN_LABELS_FOR_RECALIBRATION=20`，
  不够记 pending 不写回（0<n<20 Exit 1 不动 yaml）。
- **prostate 聚光**：总 Dice + case 数 + dice=0 清单 + AMOS/TS 分源均值 +
  漏检与性别映射注记进 summary。

### 1.5 n_target_voxels 计数 bug 修复（阶段 3 补 fix，`f26693e`，2026-11-11）

- **真 bug**：`src/bodydonor/nni_runner.py` 旧代码
  `int((mask_arr == target_label).sum())`；修正 mask 是二值 0/1，target_label
  是语义类号（如 liver=44），**target_label != 1 时恒报 0**。
- **发现路径（GPU 验证 2026-11-11 00:02，job nni-gpu-verify-amos0004）**：
  mask 非零 16754 体素，sidecar n_target_voxels 却报 0。CPU 验证（阶段 3 §8.2）
  target_label=1 时 `==1` 与 `>0` 等价，4055 恰好正确——**同一口径，不同结果，
  不掩盖**。
- **修复**：抽 `_count_target_voxels`（前景 = `(mask > 0).sum()`）；runner 内
  删 target_label 赋值（ruff F841）；补 3 项纯函数回归单测 + CPU 真跑 sidecar
  与落盘 mask 交叉核对。
- **重跑 GPU 验证**（job nni-gpu-verify-amos0004-fixed，exit 0）：load 9.61s /
  inference 1.76s / wall 11.37s / torch 峰值 4094.5MB（与修复前一致）/
  nvidia-smi 6413.0MB（修复前 8365.0MB——nvidia-smi 全局口径含基线波动，
  如实并列，不解释成改进）/ **n_target_voxels 16751 == mask 非零 16751**
  （修复前那次 mask 非零 16754，两次运行差 3 体素是 nnInteractive 推理本身的
  运行间波动）；提示点 (318,255,66) 在修正内；GPU 锁 acquire→release 正常；
  sidecar source=nninteractive_guided、license CC BY-NC-SA 4.0、provenance 完整。
- **产物持久化**：修正后 sidecar + mask 到
  `~/data/BodyDonor/nni-gpu-verify/amos_0004/`（correction_source.json +
  correction_mask.nii.gz）；修复前报 0 的 sidecar 存为同目录
  correction_source.json.pre-fix-buggy-n0。/tmp 下的原验证产物不作持久化依据。

### 1.6 学生 hold-out 门禁评估（阶段 6，`ebb945f`/`3e4674d`/`27387c8`）

- **`eval/evaluate_student_holdout.py`**（复用教师评估的 dice_one_case /
  SmiPoller / prostate / weakest_classes / summary 结构，能 import 就 import）：
  - 学生推理 `nnUNetv2_predict -d 501 -c 3d_lowres -f 0 -tr nnUNetTrainer`，
    默认 TTA，-npp 1 -nps 1（WSL 内存红线），每 case 持 GPU 锁
    （job=student-holdout-<case>），已有预测跳过。
  - **30/30 完成，failed_cases 0**；24 例 fresh 推理 + 6 例复用首轮 smoke 产物
    （s0045/s0071/s0147/s0209/s0319/s0321）。
  - 总耗时 **1957.8s**（首次全量运行，~/m4-logs/student-holdout.log）；
    CT-Lite 21.7–99.4s/例，AMOS 60.9–150.1s/例（最慢 amos_0015 150.1s、
    amos_0007 146.0s）。
  - VRAM：**peak_vram_nvidia_smi_mb 5569.0**（SmiPoller 全程轮询峰值 − 基线，
    M2 口径）；peak_vram_mb=None（nnUNetv2_predict 是子进程，torch 分配器峰值
    不可读，不编造，note 已写明）。
- **首轮三坑（`3e4674d`，实测修复）**：
  1. `-m nnunetv2.inference.predict_from_raw_data` 跑的是模块 `__main__` 示例
     （Dataset004_Hippocampus）——必须用 `nnUNetv2_predict` console_script。
  2. s0209 方向矩阵非严格正交（列间 max|cos|=1.2e-4）→ SimpleITK 拒读 →
     worker 全灭；修 SVD 极分解 header 正交化（世界坐标偏差 0.00011mm；
     QR 符号翻转偏差 3.28mm 不可用）；s0209 pred affine == orthofix 副本
     affine（母代理核实）。
  3. `-i` 目录是批量预测，残留 symlink 会重复预测全部 case——每次推理前清空。
- **门禁口径修正（`27387c8`，重点来龙去脉）**：首轮按 117 类评得 mean 0.1027
  FAIL 并已落库（gate_status=failed），这是**口径错误**——学生只训了 15 类，
  102 个超范围类天然 dice=0，完美学生也永远 FAIL。修正为分口径：
  - **in-scope**（gate_scope="student-15-classes"）：mean **0.8014**、
    passed **True**、failures 空、整类漏检空。
  - **naive 117 类对照**保留在 summary["scope_naive"]（mean 0.1027、passed
    False、63 条每类下限失败）——不删，留作口径对照。
  - DB 已纠正为 gate_status=passed / mean 0.8014（读回核对）；旧值
    failed/0.1027 写明在报告与提交信息里，不掩盖。
- **ModelVersion 回填（读回核对）**：weights_key=/home/gogvf/data/BodyDonor/
  nnUNet_results/Dataset501_BodyDonorDistill/nnUNetTrainer__nnUNetPlans__3d_lowres/
  fold_0/checkpoint_final.pth、gate_status=passed、gate_holdout_file=eval/holdout.txt。

### 1.7 多教师一致性 QC 规则启用（阶段 5，`094562c`）

- **`rule_multi_teacher_consistency` 真逻辑**（qc.py）：教师 mask vs 学生 mask
  **按类名对齐**（教师 TS 标准值 vs 学生 Dataset501 值 1–15，编号不同不能按值比）；
  只比名字交集类，学生范围外教师类跳过（detail 记
  skipped_teacher_only_classes/names）；逐类 Dice 只算两边任一非空的类；
  flagged = 参与类 mean Dice < threshold。
- **阈值决策**：阈值保持 yaml 0.80 初值不改；**校准的是聚合方式**（mean 不是
  最差类），依据是 30 例实测：最差类聚合在 0.80 下 flag 24/30 例（无判别力），
  均值聚合 flag 5/30 例（s0209 0.1181、amos_0005 0.6592、s0338 0.7081、
  amos_0006 0.7567、amos_0016 0.7862）。config 里 enabled 改 true，note 写清
  校准数字与日期 2026-11-11；source 保持 cold_start、calibrated_on 保持 0
  （数值未变，不谎称 calibrated_on_N）。
- **`run_qc` 加 student_mask_arr / student_classes 可选参数**：有学生 mask
  真跑、无则显式 skipped（`{"skipped": "no student mask"}`，不进 flags、
  不计入 enabled 分母）；qc_pipeline 调用点无学生 mask 可用，显式不传、
  不造假（单图 QC 与 117 类教师 mask 无学生 mask 可比）。

### 1.8 M4-STAGE4 报告（阶段 4，`18cdae5`，前置已交付，本报告只汇总）

- `a735e21` 低频类进门禁结果、`df4eead` 评估接显存采样 + 覆盖检查 +
  prostate 聚光、`2b7fef0` qc-calibrate 20 例门槛。M3 偏差关闭情况见
  M4-STAGE4-REPORT.md §5。

---

## 2. 实测验证表（每项写清在真机上怎么验的）

| 验证 | 方式 | 结果 |
|---|---|---|
| **正式蒸馏训练** | 实测：nohup `run_nnunet_stage2b.py train --epochs 1000`，118968.1s，GPU 锁 job=nnunet-stage2b-45196 全程持有（108767 个 nvidia-smi 样本） | **exit 0，Mean Validation Dice 0.861775797907179**（fold_0 validation summary.json），peak_vram_smi_net 8525.0MB（基线 599.0） |
| **短训 fp16 稳定** | 实测：nnUNetTrainer_5epochs 3d_lowres fold 0 五次运行（前四次失败均修复，stage2b 报告 §3/§4） | 第五次全程 exit 0，loss 单调下降无 NaN；Mean Validation Dice 0.0506 是 5 epochs 早期现象 |
| **Dataset501 完整性** | 实测：imagesTr/labelsTr 各 35 读回、dataset.json numTraining=35、du 落盘 1.4G | 35 例 × 15 类齐备；训练输入只用 TS total + 纯人工修正（manifest 可审计） |
| **n_target_voxels 修复** | 实测：GPU 验证两轮（job nni-gpu-verify-amos0004 / -fixed）+ CPU 交叉核对 | 修复前 sidecar 报 0（mask 非零 16754）；修复后 **n_target_voxels 16751 == mask 非零 16751**；torch 峰值 4094.5MB 两轮一致 |
| **学生 hold-out 推理** | 实测：`evaluate_student_holdout.py` 30 例全量（nohup + GPU 锁逐 case 持锁），~/m4-logs/student-holdout.log | **30/30 完成，failed_cases 0**；1957.8s；CT-Lite 21.7–99.4s/例、AMOS 60.9–150.1s/例；peak_vram_nvidia_smi_mb 5569.0 |
| **学生门禁（in-scope 口径）** | 实测：evaluate_gate 分口径重跑（--skip-inference，30 例预测全在）+ DB 读回核对 | **in-scope 15 类 mean 0.8014 passed True**；naive 117 类对照 0.1027 FAIL 留 scope_naive；DB 纠正为 passed/0.8014 |
| **教师-学生一致性分布** | 实测：同一 30 例教师 mask vs 学生 mask 逐类体素 Dice（`eval/student_teacher_consistency.json`） | **419 对：mean 0.8560 / median 0.9220 / p10 0.6986 / p25 0.8521 / min 0.0**；低于 0.80 的类 5 个 |
| **多教师一致性规则** | 实现 + 单测：按名对齐（教师 TS 值 vs 学生 1–15 编号不同反证）、范围外跳过、无学生 mask 显式 skipped | 5 个新语义测试（一致不 flag / 不一致 flag / skipped 两态 / 按名跳过） |
| **阈值聚合方式校准** | 实测：30 例 case 级聚合对比（母代理独立实测） | 最差类聚合 flag 24/30（无判别力）vs 均值聚合 flag 5/30（有判别力）——聚合方式定 mean，阈值 0.80 不动 |
| **qc_pipeline 集成边界** | 实现 + 注释：调用点无学生 mask 显式不传 | 单图 QC 该规则 skipped（不进 flags、不计分母），不造假 |
| **s0209 orthofix** | 实测：方向矩阵 max|cos|=1.2e-4 拒读 → SVD 极分解（偏差 0.00011mm，QR 3.28mm 不可用）；pred affine == orthofix 副本 affine（母代理核实） | s0209 可读可评，无需重跑 |
| **门禁结果落库** | 实测读回：ModelVersion 三字段 | weights_key=checkpoint_final.pth 绝对路径、gate_status=passed、gate_holdout_file=eval/holdout.txt |

**学生 15 类逐类（30 例，eval/student_holdout_results.json）**：liver 0.9433、
spleen 0.9318、kidney_right 0.9262、kidney_left 0.9172、aorta 0.9121、
stomach 0.8817、urinary_bladder 0.8219、inferior_vena_cava 0.8048、
esophagus 0.7795、pancreas 0.7460、gallbladder 0.7315、adrenal_gland_right
0.7068、adrenal_gland_left 0.6724、duodenum 0.6357、prostate 0.6099
（全部 ≥ 0.5）。

**prostate 单项（学生）**：0.6099（18 cases），zero_dice 4 例
（amos_0004/0011/0014/0015）；amos 均值 0.5298（14 例）vs ts_public 0.7120
（5 例）；教师 prostate 0.5914——**学生略高于教师**。

**JSON 数字口径说明（如实写明）**：当前 `eval/student_holdout_results.json`
是 `--skip-inference` 重跑产物，vram 峰值与 elapsed 是重跑值（None / 82.8s）；
**首次全量运行的 VRAM 5569.0 与耗时 1957.8s 以 ~/m4-logs/student-holdout.log
首次 summary 为准**（JSON 被重跑覆盖，数字以日志为准，不混写）。

### 2.1 GPU 资源口径

| 项 | 值 | 来源 |
|---|---|---|
| 训练（1000 epochs，33.04h） | peak_vram_smi_net **8525.0MB**（基线 599.0，108767 样本） | stage2b-train.json |
| 学生 hold-out 推理（30 例） | peak_vram_nvidia_smi_mb **5569.0**（SmiPoller 峰值 − 基线，M2 口径） | student-holdout.log 首次 summary |
| 学生 torch 分配器峰值 | **None**（nnUNetv2_predict 是子进程，无 runner 口径，不编造） | 同上 note |
| nnInteractive GPU 验证 | torch 峰值 **4094.5MB**（两轮一致）/ nvidia-smi 修复前 8365.0、修复后 6413.0（全局口径含基线波动，并列不解读） | f26693e 提交信息 + sidecar |

### 2.2 测试结果

| 项 | 结果 |
|---|---|
| pytest | **394 passed, 51 warnings, 135.23s**（母代理独立复核，exit 0）；基线演进：M3 366 → 阶段 4 后 382 → 阶段 6 首轮 385 → 本轮收尾 394 |
| ruff | 全绿（src/tests/eval/scripts/config） |
| 本轮新增测试 | test_evaluate_student_holdout.py 新文件（口径切分 + evaluate_gate 串联 + JSON 数字锁定）；test_qc.py +5 个多教师语义测试（一致不 flag / 不一致 flag / skipped 两态 / 教师独有类按名跳过含编号不同反证）；test_nninteractive_backend.py +3 项 n_target_voxels 纯函数回归（f26693e）；test_qc_api 断言同步 |

---

## 3. 诚实结论（不粉饰）

### 3.1 学生门禁首轮 FAIL 是口径错误，不是模型失败

首轮按 117 类评：mean 0.1027，FAIL，且已落库（gate_status=failed）。根因是
**口径错误**：学生只训了 15 类（Dataset501 = AMOS 15 类），教师 GT 是 117 类，
102 个超范围类天然 dice=0——**完美学生也永远 FAIL**。`27387c8` 修正为分口径：
in-scope（gate_scope="student-15-classes"）mean 0.8014 passed True；naive 117 类
对照保留在 summary["scope_naive"]（0.1027 / FAIL / 63 条每类下限失败）不删。
DB 从 failed/0.1027 纠正为 passed/0.8014（读回核对），旧值在报告与提交信息里
写明，不掩盖。**与 M3 教师门禁对比必须用同 15 类均值：教师 0.8548 vs 学生
0.8014（教师 117 类 0.9335 与学生 0.1027 不可比）**——教师 15 类均值由 M3 存量
`eval/holdout_results.json` per_class_dice 同 15 类重算（母代理独立复核 0.8548）。

### 3.2 学生只覆盖 15 类：102 个 TS 类不在门禁范围

学生设计范围 = Dataset501 训练的 15 类（收敛理由：117 类全量每类样本过低，
见 `6c28f95`）。教师 GT 的其余 102 类（肺叶、肋骨、椎体、肌群等）不在学生
预测范围，不进门禁，也不进 in-scope 均值。**M5 若扩类需重训重评**；门禁注记
（gate_note）与 scope_naive 对照让这一局限在产物里可见。

### 3.3 多教师一致性规则：qc_pipeline 侧尚无学生 mask 可用

`rule_multi_teacher_consistency` 已启用（enabled: true），但 **qc_pipeline
调用点无学生 mask 可用**（单图 QC 输入是教师 mask，学生 mask 不在生产路径
上）——run_qc 显式不传学生参数，该规则在单图 QC 里 skipped（不进 flags、
不计入 enabled 分母，不造假）。**真学生 mask 的线上 flag 只在母代理的真机
复核里跑过两例**（即逐 case 教师对比真跑的两例）；规则本体的
语义由 5 个单测锁定。生产路径接入（QC 评分时取同 case 学生预测）后置。

### 3.4 阈值聚合方式校准过，数值没有

30 例实测：最差类聚合在 0.80 下 flag 24/30 例（基本等于全审，无判别力）；
均值聚合 flag 5/30 例（正是分歧最大的 5 例）。**判别力决定聚合方式（mean），
阈值数值保持 0.80 初值不改**——校准的是聚合方式不是数值，source 保持
cold_start、calibrated_on 保持 0，不谎称 calibrated_on_N。数值重算仍等真实
抽检标签（qc-calibrate 门槛 20 例，见 §5）。

### 3.5 学生-教师一致性数字解读

419 对逐类体素 Dice：mean 0.8560、median 0.9220、p10 0.6986、p25 0.8521、
min 0.0。低于 0.80 的类 5 个：duodenum 0.7130、adrenal_gland_left 0.7552、
prostate 0.7430、gallbladder 0.7684、adrenal_gland_right 0.7847。Dice=0 的
pair 12 对（s0209×5、s0338 kidney_right + adrenal_gland_left、s0495 prostate、
s0556 urinary_bladder、s0847 duodenum、amos_0004 prostate、amos_0021
gallbladder）——零对集中在学生漏检 case 与教师漏检类（prostate），与 §3.6
的 s0209 分析一致。

### 3.6 s0209 极端椭球 case 学生漏检未处理

s0209 学生成绩真实差：aorta dice 0.17（GT 105288 体素 vs 预测 11295）；
3d_lowres 在极端椭球 case（74×122×366）上漏严重。**如实进门禁**（in-scope
均值含它），未做任何修正处理；该例是 419 对里 5 个 Dice=0 pair 的来源
（s0209×5，见 §3.5）。后续若处理，路径是 3d_fullres / cascade 或该例的
重采样策略，属 M5 决策。

### 3.7 评估产物 JSON 被重跑覆盖，首次数字以日志为准

当前 `eval/student_holdout_results.json` 是 `--skip-inference` 重跑产物
（口径修正时重算），vram 与 elapsed 是重跑值（None / 82.8s）。首次全量运行
的 VRAM 5569.0 与耗时 1957.8s 在 ~/m4-logs/student-holdout.log 首次 summary
里，两处不混写。这是运行产物不入仓（eval/.gitignore）的固有代价，日志留档。

---

## 4. 非目标清单（M4 不做项）

- 3d_fullres / cascade 配置训练（2b 预算约束，待 3d_lowres 门禁结果决定）
- 学生 mask 接入生产 QC 路径（§3.3，规则本体已就位）
- s0209 极端椭球漏检修正（§3.6）
- 真实医生抽检决策回流（抽检台 UI 与院内流程，M5；qc-calibrate 已就位）
- hold-out 扩容 / 第三源（本地人工修正产物仍缺位，M3 §3.1 遗留不变）
- MaskVersion.source 列宽修改（留给 M5-e）
- nnInteractive server 模式（M3 §9 遗留不变）、review.html 修正入口按钮接线
- HU 分布本院化校准（§7.2 需院内真实脱敏 CT，M5+）
- OHIF 评估（M3 已推迟，独立决策）

---

## 5. 遗留问题

1. **MaskVersion.source String(16) 装不下 nninteractive_guided**（20 字符）：
   本轮不建行不改列宽，provenance 只落 sidecar JSON；留给 M5-e（已知输入：
   改 String(24)+ 或来源映射表）。
2. **n_target_voxels 计数 bug**：已修复（`f26693e`），修复前报 0 的 sidecar
   留档（.pre-fix-buggy-n0）；同口径教训（`==target_label` vs `>0`）已进
   3 项回归单测。
3. **门禁口径修正**：首轮 failed 落库 → 纠正为 passed（`27387c8`），旧值
   写明；教训（评估口径先于门禁判定）记录在 gate_note 与本报告 §3.1。
4. **学生门禁只覆盖 15 类**：102 个 TS 类不在学生设计范围，不进门禁；M5
   扩类需重训重评（§3.2）。
5. **qc-calibrate 数值重算仍 pending**：抽检决策 0/20，未达门槛
   （M4-STAGE4-REPORT §3.2）；多教师一致性的聚合方式已校准、数值未动（§3.4）。
6. **hold-out 仍是两源**：ts_public 15 + amos 15，local=0（M3 §3.1 遗留）；
   39/117 类 <10 cases（低频类只告警不改 pass/fail，见 M4-STAGE4-REPORT §1.1）。
7. **阶段 5 规则在 qc_pipeline 侧尚无学生 mask 可用**（§3.3）：单图 QC 显式
   skipped；真学生 mask 的线上 flag 只在母代理真机复核跑过两例。
8. **s0209 极端椭球 case 学生漏检未处理**（§3.6）。
9. **本地 main 领先远端 34 提交、未推**（`git rev-list --count origin/main..HEAD`
   实测 34；本报告 commit 后 +1）。push 是显式决策。
10. **M3 遗留不变项**：TotalSegmentator dataset 全量下载、100 MB 三处强制的
    另两处、多指纹部署语义、BSL LICENSE 占位、Uberon 56 条空串待核对、
    niivue MIP 推迟 1.x stable。

---

## 6. 交付物清单

```
src/bodydonor/distill_data.py       蒸馏数据准备 + 许可门禁（6c28f95 + 81c94ac 系）
src/bodydonor/nni_runner.py         nnInteractive JSON runner（阶段 3；f26693e 修 _count_target_voxels）
src/bodydonor/nninteractive_backend.py  交互修正后端（来源 v2 + provenance + 双保险）
src/bodydonor/ontology.py           蒸馏门禁三值拒绝（teacher/nninteractive_guided/human）
src/bodydonor/qc.py                 rule_multi_teacher_consistency + run_qc 学生参数（094562c）；qc-calibrate 20 例门槛（2b7fef0）
src/bodydonor/qc_pipeline.py        调用点显式不传学生 mask（注释写明）
src/bodydonor/holdout.py            低频类清单 + coverage 显式进门禁结果（a735e21）
src/bodydonor/cli.py                nninteractive-correct 命令 + qc-calibrate 门槛三态
src/bodydonor/app.py                /review/{sid}/nninteractive-correct + license 字段
config/qc_thresholds.yaml           multi_teacher_consistency enabled=true + 校准注记（2026-11-11）
scripts/prepare_distill_dataset.py / run_distill_teacher_inference.py / run_nnunet_stage2b.py / register_student_model.py / finalize_student_model.py  蒸馏与登记运维脚本
scripts/install_torch251.sh / download_nni_model.sh / cpu_smoke_verify.sh  阶段 3 运维脚本
eval/evaluate_student_holdout.py    学生门禁评估（ebb945f + 3e4674d + 27387c8 分口径）
eval/evaluate_holdout.py            教师评估接显存采样 + 覆盖检查 + prostate 聚光（df4eead）
eval/.gitignore                     +evaluate_student_holdout.py（结果 JSON 不入仓）
tests/test_evaluate_student_holdout.py  新文件（口径切分 + gate 串联 + 数字锁定）
tests/test_qc.py                    +5 多教师语义测试 + 校准门槛用例
tests/test_nninteractive_backend.py +3 n_target_voxels 纯函数回归（f26693e）
tests/test_holdout.py / test_evaluate_holdout.py / test_qc_api.py  阶段 4 新增 12 条 + 断言同步
~/data/BodyDonor/nnUNet_raw/Dataset501_BodyDonorDistill/  蒸馏数据集（1.4G，不入仓）
~/data/BodyDonor/nnUNet_results/.../fold_0/checkpoint_final.pth  学生权重（不入仓）
~/data/BodyDonor/nni-gpu-verify/amos_0004/  GPU 验证产物 + pre-fix-buggy-n0 留档
~/m4-logs/student-holdout.log       首次全量运行 summary（VRAM 5569.0 / 1957.8s 的数字来源）
eval/student_holdout_results.json   运行产物（--skip-inference 重跑值；不入仓）
eval/student_teacher_consistency.json  运行产物（419 对一致性分布；不入仓）
```

---

## 7. Blockers（单列）

1. **【已解决】WSL2 15.8GB 下 nnU-Net 多 worker OOM（五次短训实测）**
   - 现象：预处理 / validation 导出池并行 worker OOM（"Some background
     workers are no longer alive"；dmesg 实测击杀记录）
   - 已试：`-np 1`（预处理）+ `nnUNet_def_n_proc=1`（导出池串行）+ 关
     nnUNet_compile（无 C 编译器）
   - 结果：全部跑通；红线固化——WSL2 下 nnU-Net multiprocessing worker 必须串行
2. **【已解决】torch.compile inductor 无 C 编译器**
   - 本机 gcc/cc/clang 实测均 MISSING，dpkg 无 root 装不了
   - `nnUNet_compile=false`（nnU-Net 文档化开关），eager 等价训练，fp16 不受影响
3. **【已解决】nnInteractive venv 安装（pypi.nvidia.com DNS 失败）**
   - torch 2.5.1+cu124 wheel 下载完成但 nvrtc 依赖装不上，首装失败
   - resolver 顶到 torch 2.15.0+cu132（torchvision 硬 pin）→ 装回 2.5.1
     （PyPI fallback，同 +cu124 build），import 三连校验通过
4. **【已解决】s0209 方向矩阵非严格正交 → SimpleITK 拒读**
   - SVD 极分解 header 正交化（世界坐标偏差 0.00011mm；QR 符号翻转偏差
     3.28mm 不可用）
5. **【无 blocker 遗留】**：M4 无未解决 blocker。

---

## 8. 工作方式记录（提案精神的最小合理选择）

- **门禁口径先于判定**：评估口径（学生 15 类 vs 教师 117 类）必须在门禁
  判定前显式声明——首轮教训已固化为 gate_scope/gate_note 字段 + scope_naive
  对照（不删旧口径，让口径差异可见）。
- **同类对比才可比**：教师-学生对比必须同 15 类均值（0.8548 vs 0.8014），
  跨口径数字（0.9335 vs 0.1027）不得出现在结论里。
- **旧值写明，不掩盖**：门禁 DB 纠正（failed→passed）、n_target_voxels
  （0→16751）、短训五次失败——全部在报告/提交信息留旧值与原因。
- **规则启用不为验收造假**：多教师一致性在学生模型出现后才启用（M3 按原义
  disabled）；qc_pipeline 无学生 mask 就显式 skipped，不传假数据。
- **聚合方式用判别力选**：最差类 24/30 无判别力 vs 均值 5/30 有判别力——
  校准记录进 yaml note（带日期与数字），阈值数值不动。
- **eval/ 产物不入仓**：结果 JSON 进 .gitignore（含 evaluate_student_holdout.py
  白名单反向排除），脚本入仓——结果可复跑；被覆盖的首次数字以日志留档为准。
- **许可纪律贯穿**：训练输入只用 TS total + 纯人工修正（LICENSES §8.1）；
  nnInteractive 产物（CC BY-NC-SA 4.0）只作交互修正辅助、不进蒸馏训练集
  （三值拒绝门禁 + legacy 别名保留拒绝）。
