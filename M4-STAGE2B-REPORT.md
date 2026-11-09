# M4 阶段 2b 报告：nnU-Net 学生蒸馏训练（Dataset501，3d_lowres，fold 0）

日期：2026-11-09。执行 agent 续跑（接手被循环检测中断的上一轮会话）。
铁律遵守：全部数字实测（无 mock/估算）；affine 守卫未触发（无重采样场景）；
训练输入只用 Dataset501（TS total 教师 mask + 纯人工修正）；学生输出未回灌；
训练期间无 TotalSegmentator 推理并行（GPU 锁互斥）；未 push、未建 PR、未改
eval/holdout.txt。

## 1. 复核（上一轮交付，本轮核对后确认）

| 项 | 结论 |
|---|---|
| Dataset501 完整性 | imagesTr/labelsTr 各 35；dataset.json numTraining=35、labels background+15 类；nnUNetPlans_3d_fullres 与 3d_lowres 各 105 文件（35×3） |
| manifest label_policy | allowed_sources [manual-correction, total]；rejected [experimental, manual-correction-nninteractive, segvol]；amos/ct_lite never_in_training=true |
| 泄漏门禁重查 | `check_leak` 以真实训练列表（35 例 imagesTr）重查 PASS（交集空） |
| plans 关键参数 | patch [80,160,160]、batch 2、spacing [3.2349,1.6865,1.6865]、median shape [142,237,237]、CTNormalization、PlainConvUNet 6 stages（batch_dice=false） |
| 短训失败修复 | `nnUNet_compile=false`（包装脚本 NNUNET_ENV 文档化开关；本机 gcc/cc/clang 实测均 MISSING，inductor "Failed to find C compiler"） |

## 2. 预处理（上一轮完成，本轮复核）

- **3d_lowres**：`nnUNetv2_plan_and_preprocess -d 501 -c 3d_lowres`，exit 0，
  145.3s（stage2b-preprocess.log）。
- **3d_fullres**：同命令 `-c 3d_fullres -np 1`，exit 0，1326.1s（247 个 nvidia-smi
  样本全部在基线附近）。`-np 1` 是关键修复：默认多 worker 两次尝试（preprocess2/3
  日志）均以 batchgenerators "Some background worker is 6 feet under" 失败（WSL
  15GB 内存下 worker OOM），`-np 1` 后一次跑通。
- **peak_vram_smi_net_mb=null 的原因（实测，不编造）**：nnU-Net 预处理是纯 CPU
  流水线——fingerprint 提取、experiment planning、重采样全部在 CPU；1247 个
  nvidia-smi 样本（1s 间隔轮询全程）无一偏离基线 599MB，GPU 全程零负载，
  峰值−基线 ≤ 0 故记录 None（SmiPoller 契约：无正峰值返回 None，不猜数字）。
  显存记录口径说明：本包装只记 nvidia-smi 全局口径（segmentation.gpu_peak_memory_mi_b
  同源），torch 分配器口径不可从包装进程读，两个口径不混写。
- plans 三个配置齐备（2d / 3d_lowres / 3d_fullres / 3d_cascade_fullres），
  nnUNetPlans.json 落盘实测读回。

## 3. 短训（nnUNetTrainer_5epochs，5 epochs，3d_lowres，fold 0）

两次失败与修复：

1. **第一次（12:37）**：torch.compile inductor "Failed to find C compiler"——
   nnU-Net 默认 `nnUNet_compile` 开启，本机无 C 编译器（gcc/cc/clang 实测均
   缺失，dpkg 无 root 装不了）。修复：包装脚本 `nnUNet_compile=false`（nnU-Net
   文档化开关），eager 模式等价训练（仅速度差异），fp16 AMP autocast 不受影响。
2. **第二次（12:40，exit 1）**：5 epochs 全部跑通后 `perform_actual_validation`
   FileNotFoundError 缺 `nnUNetPlans_3d_fullres` 目录——2a 只跑了 3d_lowres
   预处理。修复：补跑 3d_fullres 预处理（§2）。
3. **第三次（13:22，exit 1）**：5 epochs 全部跑通（epoch 4: train_loss 0.1927、
   val_loss 0.2038、epoch time 109.41s），validation 4/7 例时导出线程池 worker
   全灭——"Some background workers are no longer alive"。根因（dmesg 实测）：
   `default_num_processes` 默认 8（nnunetv2/configuration.py:5），validation
   导出池 spawn 8 worker，WSL 15.8GB 内存下 OOM。修复：文档化开关
   `nnUNet_def_n_proc=2`（bfd5c03）。
4. **第四次（13:35，exit 1）**：5 epochs 全部跑通，validation 2/7 例后仍 OOM
   ——dmesg 13:49:39 击杀 40367（python，RSS 7.3GB）：`Pool(2)` 两 worker
   **各 ~7GB RSS 并行**对最大例（amos_0050/0067）做 16 类概率图全分辨率
   重采样（float64 中间缓冲），2×7GB + 主进程 > 15.8GB。修复：
   `nnUNet_def_n_proc=1` 串行导出（6b5d962）——单 worker 峰值 ~7GB +
   主进程 ~2GB ≈ 10GB < 15.8GB，7 例 validation 仅慢 1–2 分钟。
5. **第五次（13:55，exit 0）**：见 §4 实测，全程通过。

第二次实测（epoch 耗时/显存基线，日志 stage2b-short-train-run.log）：
- 5 epochs 全部完成，epoch 0 ~127s（含启动），epoch 1–4 ~114/110/116/112s
  （均值 113s/epoch）。
- train_loss 0.4873 → 0.2869 → 0.2192 → 0.1945 → 0.1742；val_loss 0.183；
  无 NaN、无爆炸。
- 显存峰值：nvidia-smi 净增量 10337 MB + 启动前基线 599 MB ≈ 10936 MB
  （16GB Quadro RTX 5000 内，余量 ~5GB）。真卡基线（M0：117 类 116.2s）对照：
  本配置 15 类 batch 2 patch [80,160,160] 显存 ~10.9GB 合理。
- pseudo dice 仅 liver（class 6）0.79、其余 0.0、EMA 0.017——5 epochs 早期
  现象，如实记录，不是稳定性问题。

## 4. 短训第五次（13:55，nnUNet_def_n_proc=1，全程 exit 0）✅

| 项 | 实测 |
|---|---|
| 命令 | nnUNetTrainer_5epochs，Dataset501，3d_lowres，fold 0 |
| exit code | **0**（elapsed 1670.7s ≈ 27.8min，训练 + 串行 validation 全程） |
| 每 epoch 耗时 | 132.42 / 122.72 / 118.63 / 109.68 / **109.21s**（epoch 0–4；稳态 ~109s） |
| train_loss | 0.4965 → 0.2966 → 0.2346 → 0.2134 → **0.1858**（单调下降，无 NaN） |
| val_loss | 0.3687 → 0.2744 → 0.2391 → 0.21 → **0.197**（单调下降） |
| 显存峰值 | nvidia-smi 净增量 8457MB + 基线 599MB ≈ **9056MB**（16GB Quadro RTX 5000 内，余量 ~7GB） |
| validation | 7/7 例全部推理+导出成功（含前两次 OOM 的 amos_0050/0067），14:23:09 "Validation complete" |
| final mean Dice | **0.0506**（foreground_mean；5 epochs 早期现象） |
| per-class | 仅 liver（class 6）0.7583，其余 14 类 0.0——5 epochs 远不足以让小类收敛，如实记录 |

**fp16 稳定性结论**：nnU-Net 默认 AMP autocast fp16，5 epochs 全程 loss 单调
下降、无 NaN/Inf/爆炸；peak VRAM 9056MB 稳定（1587 个 nvidia-smi 样本）。
**PASS**——正式训练同配置可继续。

**WSL2 内存红线（本轮实测固化）**：所有 nnU-Net multiprocessing worker
（预处理 `-np`、validation 导出池 `nnUNet_def_n_proc`）在 15.8GB WSL2 下
必须串行（=1）；并行 2 worker 重采样即 OOM。

## 5. 正式训练启动（已完成启动，训练进行中）

| 项 | 实测 |
|---|---|
| 启动命令 | `nohup .venv-nnunet/bin/python scripts/run_nnunet_stage2b.py train --epochs 1000 > ~/m4-logs/nnunet-train-fold0.log 2>&1 &` |
| 启动时间 | 2026-11-09 14:28:59 +0800 |
| nohup PID（包装进程） | **45196**（GPU 锁持有进程，job=nnunet-stage2b-45196） |
| 训练子命令 | `nnunetv2.run.run_training Dataset501_BodyDonorDistill 3d_lowres 0 -tr nnUNetTrainer`（标准 1000-epoch trainer） |
| 日志 | ~/m4-logs/nnunet-train-fold0.log（包装 stdout/stderr）；nnU-Net 自身日志 fold_0/training_log_2026_11_9_14_29_03.txt |
| Epoch 0 开始 | 14:29:06（实测） |

**Epoch 数推算（自算）**：短训第五次实测稳态 ~109s/epoch（epoch 3–4：
109.68/109.21s；四次重跑 epoch 1–4 全部在 109–116s 区间，取保守 113s）。
1000 epochs × 113s = 113,000s = 113,000/3600 = **31.4h** < 48h 上限
→ 用标准 1000 epochs（nnUNetTrainer）。
（若 >48h 才缩减并写明理由；此处未触发。）

预计完成：14:29 + 31.4h ≈ 2026-11-10 21:53 +0800（±1–2h，视 epoch 耗时波动）。

**trainer 类名修正（本轮实测发现）**：nnU-Net 的标准 1000-epoch trainer 类名
就是 `nnUNetTrainer`（num_epochs=1000 默认）；`nnUNetTrainer_1000epochs` 类
不存在（recursive_find_trainer_class_by_name 实测 RuntimeError）。包装脚本
cmd_train 已修正并提交（12e4fbb）：`-tr nnUNetTrainer`，--epochs 固定 1000。

## 6. ModelVersion 登记

| 字段 | 值 |
|---|---|
| model_version_id | **student-nnunet-v2-distill-20261109T062920Z** |
| name | nnU-Net v2 3d_lowres student (M4 distillation, TS total teacher) |
| base_model | nnU-Net v2 |
| weights_key | null（训练完成后回填 checkpoint_final.pth） |
| gate_status | **pending**（hold-out 评估后由 persist_gate_result 覆盖） |
| gate_metrics | train_manifest=~/data/BodyDonor/nnUNet_raw/Dataset501_BodyDonorDistill/manifest.json（实测读文件）、n_train_cases=35、source_constraint=LICENSES §8.1 商用安全路径、configuration=3d_lowres、fold=0、epochs=1000 |
| gate_holdout_file | null（门禁评估后写入 eval/holdout.txt 路径） |
| created_at | 2026-11-09 06:29:20 UTC（SQLite 实测读回） |

登记脚本 scripts/register_student_model.py（提交 53178a1）；ModelVersion 由
蒸馏训练侧创建（persist_gate_result 只更新门禁字段不建行，holdout.py docstring
实测约定）；走 db.write_lock 单一写入纪律。

## 7. 提交清单（本轮）

| hash | 内容 |
|---|---|
| 3fa871f | feat(m4): stage2b 训练包装脚本（预处理/短训/正式训练 + GPU 锁 + nnUNet 环境变量） |
| 12e4fbb | fix(m4): 正式训练 trainer 类名修正——nnUNetTrainer 即标准 1000 epochs |
| bfd5c03 | fix(m4): nnUNet_def_n_proc=2——validation 导出池 8 worker WSL OOM 实测修复 |
| 6b5d962 | fix(m4): nnUNet_def_n_proc=2→1——导出池 2 worker 并行重采样 WSL OOM 实测 |
| 53178a1 | feat(m4): 学生模型 ModelVersion 登记脚本（蒸馏训练侧建行，M3 落库纪律） |

上一轮已交付（本轮复核通过）：81c94ac（蒸馏数据准备）、4111d31、a0da6d9、
4602463（distill 三连 fix）。

**pytest**：全量 **347 passed**（71.29s）；提交后 gpu_lock/distill/holdout 子集
复跑 71 passed（6.54s）。
**ruff**：scripts/run_nnunet_stage2b.py、scripts/register_student_model.py 全绿。

## 8. GPU 锁持有方式（全程）

包装进程 `run_nnunet_stage2b.py` 持锁（`gpu_lock.acquire` batch 模式，任务名
nnunet，job_id=nnunet-stage2b-<pid>）；nohup 后台运行时锁同样由包装进程全程
持有——文件锁按 pid 存活判定，包装进程活着锁就有效；命令结束显式释放，
atexit 兜底，异常退出由 stale 回收兜底。正式训练锁由包装进程 45196 持有
（~31.4h 全程）。训练期间无 TotalSegmentator 推理（GPU 锁互斥保证）。

## 9. 遗留

1. **正式训练完成后的回填**（阶段 2c/3）：weights_key（checkpoint_final.pth
   路径）→ ModelVersion；hold-out 评估 → persist_gate_result 写 gate_status/
   gate_metrics/gate_holdout_file。
2. **3d_fullres 配置未训**：本阶段只训 3d_lowres（短训验证 + 31.4h 预算约束）；
   3d_cascade_fullres 级联是否需要，待 3d_lowres 门禁结果决定。
3. **nnU-Net 提示 old plans**：resenc preset 是新推荐（日志 INFO），本阶段用
   default plans（plans 已固化、短训验证过），升级留后续里程碑。
4. **短训 5 epochs 的 14 类 Dice=0**：非稳定性问题（早期现象）；1000 epochs
   正式训练后以 hold-out 门禁评估为准。
