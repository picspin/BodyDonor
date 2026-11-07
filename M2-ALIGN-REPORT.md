# M2-ALIGN-REPORT — M2 对齐补丁（用户 2026-11-07 08:19 六条决定落地）

> 日期：2026-11-07。基线：M2 第一轮已交付（本地 main `3527352`，209 passed，
> 真卡基线 amos_0004：5472 MiB nvidia-smi 全局峰值 / 118.8s / 117 类）。
> 本轮不重做 M2，只把六条新决定落到已交付代码上。依据：工作指令 M2 对齐补丁。
> 非交互运行，每步本地验证；全部测试通过 + ruff 全绿后提交。

---

## 1. 逐条对齐记录

### 3.1 M2 拆分为 M2-A（后端推理与 NL）+ M2-B（前端 niivue 与交互）

- **落实位置**：`M2-REPORT.md` 新增 **§10 M2-A/M2-B 拆分说明**（两子里程碑的
  边界、接口契约、各自验收点，见该文件）。
- M2-A = worker（segmentation.py + ts_runner.py + gpu_lock.py）+ 本体映射
  （ontology.py + map.yaml 117 类）+ `bodydonor segment` / `bodydonor ontology`
  CLI + `/segments` / `/ontology/*` API。
- M2-B = niivue 审阅页（static/review.html + /review 端点）+ TUI（tui.py +
  /monitor 端点）。
- 不改 proposal.md（提案是规格，拆分是执行记录）。

### 3.2 Worker 层 JSON 契约（主进程 ↔ TS 子环境）

- **通道选择**：**stdin/stdout**（任务 JSON 经 stdin，结果 JSON 经 stdout，
  纯 JSON——日志一律走 stderr）。理由：不落盘中间文件、无文件残留清理问题、
  管道天然串行（与 GPU 锁的互斥语义一致）。
- **runner**：`src/bodydonor/ts_runner.py`，由 `.venv-totalseg/bin/python` 执行
  （主 `.venv` 不含 totalsegmentator，import 链隔离不变，Q7）。
- **任务 JSON**：`{input_nii, task, device, output_dir, job_id}`。白名单校验仍在
  主进程先做（`check_worker_task`）；runner 收到任务后再查一次 blocked 表
  （**纵深防线**，runner 不信任 stdin——单测锁定）。
- **结果 JSON**：`{status, exit_code, mask_path, classes, wall_seconds,
  peak_vram_mb, error}`。status ∈ ok | error；peak_vram_mb 不可用时（CPU 回退）
  填 null 并在 error/reason 注明，不猜数字。
- **显存峰值主值**：runner 内 `torch.cuda.reset_peak_memory_stats()` 起始 +
  `torch.cuda.max_memory_allocated()` 结束，换算 MiB。主进程保留 nvidia-smi
  轮询作**交叉核对**（全局口径），两者都记录。
- **CLI/API 带新字段**：`bodydonor segment` rich.table 增加 combined_mask /
  labels_json / 显存峰值（主值 + nvidia-smi 交叉核对）行；`/segments` 返回
  `peak_vram_mb`（实测）、`peak_vram_nvidia_smi_mb`（交叉核对）、
  `combined_mask_key`、`labels_json_key`、`wall_seconds`。
- **旧 CLI 直调路径删除**：`ts_venv_cli` 从 TotalSegmentator CLI 改为 TS venv
  python（`build_runner_cmd()` 组装 `python ts_runner.py`），不留两套。

### 3.3 Worker 层全局 GPU 锁

- **`src/bodydonor/gpu_lock.py`**：文件锁，锁文件 `~/data/BodyDonor/.gpu.lock`
  （数据目录，WSL2 原生盘；不放 /tmp）。
  - **互斥**：`O_CREAT|O_EXCL` 原子创建即抢锁；持有者身份（pid + reason +
    task + job_id + acquired_at）写进锁文件（JSON）。
  - **批量推理**：`acquire(reason="batch", wait=3600)` 排队等待（轮询 2s +
    超时 TimeoutError，错误带持有者身份与持有时长）；**持有全程直至推理结束
    释放**（segmentation.segment_series 的 with 上下文）。
  - **交互会话**：`acquire(reason="interactive", wait=0)` **立即失败返回 None
    （不排队）**，错误信息明确"有批量推理任务在跑（pid=… task=… job=…）"。
  - **stale 锁**：持有进程已死（`os.kill(pid, 0)` 存活检查）则可回收，回收记
    日志（stderr）；锁文件损坏（空/坏 JSON）也视为 stale。
  - **空闲释放**：`release_on_idle(idle_seconds=600, on_save=None)`——交互持有者
    闲置 10 分钟或收到"保存修正"信号时立即释放。M2 落机制 + 单测 + 文档；
    nnInteractive 本体是 M4 的事（本轮不接）。
- **单测**（`tests/test_gpu_lock.py` 15 条）：两持有者互斥、批量排队等持 release、
  超时 TimeoutError、交互 fast-fail（不排队，<0.5s）、stale 回收（死 pid）、
  损坏锁回收、锁文件身份、with 上下文释放、释放幂等、保存信号立即释放、
  闲置超时释放（acquired_at 外部可更新）、未闲置仍持锁、默认 600s 常量。

### 3.4 本体词表 117 类 + 分层 NL 解析 + 可插拔 LLM

- **扩表（v1 原地扩，不发 v2）**：`ontology/v1/map.yaml` 从 38 条目扩到
  **117 条目**，TS `total` 任务全部类（`class_map['total']` 实物清单，与
  TS 2.10 输出一致）。**选择 v1 原地扩的理由**：v1 尚无下游依赖（overrides.yaml
  为空、无外部消费者），发 v2 会复制 117 条目增加评审负担；拆分记录见本报告 §4。
- 每条含 `nl_label`（规范解剖中文名）/ `synonyms`（**每类 ≥1 中文名 + ≥1 英文
  别名 + totalseg_class 原形**）/ `snomed_ct` / `uberon` / `totalseg_class` /
  `status`。
- **编码口径（不编造）**：SNOMED CT 取 **TotalSegmentator 自带官方映射**
  （`.venv-totalseg/.../resources/totalsegmentator_snomed_mapping.csv`，36 条
  补齐，其余沿用 v1 人工核对口径，全部非空）；Uberon 沿用 v1 人工核对 +
  新增结构拿不准的填**空串**（**待核对编码清单**见 §4，M5 引全量图谱后机校）。
- **解析分层**（`src/bodydonor/ontology.py`，先离线后可插拔）：
  1. **离线精确查表**：label/synonym 精确命中（overrides 优先）。
  2. **离线模糊查表**：difflib SequenceMatcher，阈值 `FUZZY_CUTOFF=0.6`（代码
     常量，测试锁定）；并列最高分多候选 → ambiguous，不静默单解。
  3. **简单规则解析**：分隔符多标签（"肝和脾"、"肝、脾"、"liver and spleen"，
     正则 `MULTI_LABEL_SPLIT`）；左右修饰（"左肾"/"左X"/"left kidney"/"右X"，
     `LEFT_RIGHT_PATTERNS` + ABBREVIATIONS 侧别拼类）；常见缩写口语
     （"胆"→{gallbladder, liver} 双候选 ambiguous，不静默单解）。
  4. **可插拔 LLM 意图解析**：`IntentParser` Protocol（NL 串 → 候选
     totalseg_class + 置信度），`RuleIntentParser`（1–3 层）为默认实现；
     `register_intent_parser()` 注册点留给 LLM 实现（**本轮不调任何 LLM、
     不联网**，单测锁定默认离线）。
- **离线测试集**：`tests/data/ontology_nl_cases.json` **22 条**（中英各 10+
  覆盖：精确命中 7、左右修饰 5、多标签 4、ambiguous 缩写 3、模糊命中 2、
  unresolved 1）；单测 `TestNLCaseSet` 断言断网全通过（不依赖网络/LLM）。
  内容见 §5。
- **三态与消歧写回**（overrides.yaml）行为不变；逃生通道开关/标记/隔离不变。

### 3.5 M2-B 协议：合并 mask + labels.json

- **worker 合并产物**：推理后 `combined_mask.nii.gz`（**uint8**，多标签单文件，
  0=背景，1..N=类别索引；117 类 < 255）+ `labels.json`（`{n_classes, labels:
  [{index, totalseg_class, name_cn, name_en, color}]}`）。
- **uint8 溢出检查**：类别数 > 255 即 `SegmentWorkerError`，不静默截断
  （`merge_ts_output` 单测锁定）。原 uint16 dtype 收敛为 uint8（117 类足够）。
- **默认配色**：`LABEL_PALETTE_RGB`（16 色周期，可区分配色）写进 labels.json，
  **不硬编码进页面 JS**（页面只在 labels.json 缺失时用 FALLBACK_PALETTE 兜底）。
- **审阅页（review.html）**：**只加载 1 张原图 + 1 个合并 mask**（nvAxial/
  Coronal/Sagittal 各 `[img, mask]` 两个 volume；第 4 视口体绘制只有原图）；
  labels.json 驱动逐类开关/换色/体素数统计（类别显示名 中文名优先）；逐类删除
  按合并 mask 的 label 索引操作；保存写回前 affine 守卫继续强制。
  **旧的多文件加载路径删除，不留两套**（`PALETTE` 硬编码表、seg-meta 驱动
  颜色的旧路径已移除）。
- **API**：`/review/{sid}/labels.json` 新端点；`/review/{sid}/seg-meta` 并上
  labels.json 的 totalseg_class/中英名/颜色；`/review/{sid}/meta` 带
  `has_combined_mask`；`/segments` 返回带 combined_mask_key/labels_json_key。
- **单测**：`test_review_api.py` 22 条（labels.json 端点、combined 优先 fallback、
  affine 篡改 422 等）；**CDP Edge headless 实测**见 §6。

### 3.6 显存峰值实测写回任务报告

- 与 3.2 一体：`peak_vram_mb` 主值 = runner 内 `torch.cuda.max_memory_allocated()`
  （分配器口径）；nvidia-smi 轮询作交叉核对（全局口径，峰值 − 推理前基线）。
  任一不可用填 null + reason，不猜数字。
- CLI rich.table 显存峰值行带主值 + 交叉核对值标注；API 同构（to_dict）。
- MaskVersion/任务记录沿用 M2 的 MaskVersion 表（无 schema 变更）。

---

## 2. 真卡端到端重跑实测（铁律：改动推理路径后必须重跑）

> 病例：amos_0004（AMOS22 真实 CT，512×512×78，78 层，`bodydonor segment
> nifti-b63b1d93 --task total -d gpu`）。三次实测（每次全量推理 + JSON 契约 +
> GPU 锁 + 合并落盘）：

| 项 | M2 基线（3527352） | 本轮重跑 #1 | 本轮重跑 #2 | 本轮重跑 #3（最终） |
|---|---|---|---|---|
| 状态 | exit 0 | exit 0 | exit 0 | exit 0 |
| **显存峰值（主值：torch 分配器）** | 未测（只有 nvidia-smi 全局值） | **8580 MiB** | **8580 MiB** | **8580 MiB** |
| **显存峰值（交叉核对：nvidia-smi 峰值 − 推理前基线）** | 5472 MiB（全局峰值，口径不同） | None（WSL2 nounits bug，已修） | None（同左，已修） | **4409 MiB** |
| **墙钟耗时** | 118.8s | 119.6s | 121.8s | **116.2s** |
| 类别数 | 117 | 117 | 117 | 117 |
| 合并产物 | mask.nii.gz（uint16） | + combined_mask.nii.gz（uint8）+ labels.json | 同左 | 同左 |
| GPU 锁 | 无 | 全程持锁（.gpu.lock，释放正常） | 同左 | 同左 |

- **主值与交叉核对的口径差异**：torch.cuda.max_memory_allocated() 是**分配器
  内分配峰值**（8580 MiB，含推理工作区）；nvidia-smi --query-gpu=memory.used 是
  **全局显存占用**（峰值 − 推理前基线 = 4409 MiB，只含推理贡献）。两口径都
  是实测；报告以 torch 分配器为主值（进程内、精确），nvidia-smi 减基线后作
  交叉核对。M2 基线的 5472 MiB 是 nvidia-smi 全局峰值（未减基线，口径不同）。
- 本轮新增实测：**8580 MiB（torch 分配器峰值，三次一致）** + 交叉核对
  4409 MiB（nvidia-smi 增量）——与 M2 基线推理增量 4641 MiB 同量级，16 GB
  卡余量充足。

## 3. 测试结果

| 项 | 结果 |
|---|---|
| pytest | **258 passed**（209 基线 + 49 新增），`uv run --extra dev --extra ingest pytest tests/` |
| ruff | `ruff check .` **All checks passed!** |
| 白名单负测试 | 全部继续通过（brain_aneurysm BLOCKED、runner 侧纵深拒绝） |
| NL 测试集 | 22 条断网全通过（`TestNLCaseSet.test_all_cases_pass_offline`） |

新增测试分布：GPU 锁 15（互斥/排队/超时/fast-fail/stale/空闲释放）+
ts_runner 契约 9（stdin/stdout JSON/blocked/损坏输入/字段/路径/隔离）+
本体 NL 分层 20（多标签/左右/缩写/模糊/IntentParser/注册点/测试集）+
本体 117 类扩表 1（snomed 全非空/中文唯一/类覆盖）。

## 4. 遇到决定没写清的细节（最小合理选择）

1. **map.yaml 发 v2 vs 原地扩**：原地扩 v1（无下游依赖，避免 117 条目 ×2 的
   评审负担）；overrides.yaml 与主表的分离语义不变。
2. **SNOMED/Uberon 编码口径**：SNOMED 用 TS 官方 CSV 补齐 36 条新增类（不
   编造）；Uberon 拿不准的填空串，**待核对编码清单**（56 条）单列 §7。
3. **通道选择（stdin/stdout vs 任务文件+结果文件）**：stdin/stdout（无中间
   文件、管道串行、与 GPU 锁语义一致）；runner stdout 是纯 JSON，日志走
   stderr——单测断言 stdout 无 Traceback 混入。
4. **GPU 锁排队超时默认**：3600s（TS 一例 ~2min × 余量）；交互 fast-fail
   wait=0。超时报错带持有者身份与已持有时长。
5. **空闲释放的"保存修正"信号**：`on_save` 可调用回调（返回 truthy 即视为
   信号）；M2 只定义约定与机制，nnInteractive 本体是 M4。
6. **combined_mask 与 mask.nii.gz 的关系**：同内容双落盘（mask.nii.gz 与
   MaskVersion 历史兼容；combined_mask.nii.gz 是审阅页 uint8 协议产物）。
   M3 可收敛为单产物（记遗留）。
7. **NL 测试集条数**：指令要 20 条中英各半；实际 **22 条**（中 12 英 10，
   多 2 条冗余覆盖英文精确命中与模糊），断言中英各 ≥10（宽断言，容许混合输入）。

## 5. 离线 NL 测试集内容（tests/data/ontology_nl_cases.json，22 条）

| id | 输入 | 期望 | 层 |
|---|---|---|---|
| exact_cn_liver | 肝 | resolved liver | exact |
| exact_en_spleen | spleen | resolved spleen | exact |
| exact_cn_synonym | 肝脏 | resolved liver | exact_synonym |
| exact_en_synonym | large intestine | resolved colon | exact_synonym |
| exact_cn_pancreas | 胰腺 | resolved pancreas | exact |
| exact_en_bladder | urinary bladder | resolved urinary_bladder | exact |
| exact_en_gallbladder | gallbladder | resolved gallbladder | exact |
| left_cn_kidney | 左肾 | resolved kidney_left | left_right |
| right_en_kidney | right kidney | resolved kidney_right | left_right |
| left_cn_femur | 左股骨 | resolved femur_left | left_right |
| right_cn_adrenal | 右肾上腺 | resolved adrenal_gland_right | left_right |
| left_en_femur | left femur | resolved femur_left | left_right |
| multi_cn_he_spleen | 肝和脾 | [liver, spleen] | multi_label |
| multi_cn_dunhao | 肝、脾 | [liver, spleen] | multi_label |
| multi_en_and | liver and spleen | [liver, spleen] | multi_label |
| multi_three | 肝、脾、胰腺 | [liver, spleen, pancreas] | multi_label |
| abbr_cn_dan | 胆 | ambiguous {gallbladder, liver} | abbreviation |
| abbr_cn_shen | 肾 | ambiguous {kidney_left, kidney_right} | abbreviation |
| abbr_en_femur | femur | ambiguous {femur_left, femur_right} | abbreviation |
| fuzzy_en_livr | livr | resolved liver | fuzzy |
| fuzzy_en_stomac | stomac | resolved stomach | fuzzy |
| unresolved_xyz | 不存在的器官xyz | unresolved | unresolved |

## 6. CDP Edge headless 实测（3.5 审阅协议）

> 方法沿用 M2（Windows 侧 msedge.exe --remote-debugging-port=9333，CDP
> websocket 驱动，WSL2 无 Linux 浏览器）。脚本：`scripts/bench_review_combined.py`。
> 对象：amos_0004（nifti-b63b1d93，117 类 teacher mask + 合并产物）。

| 检查 | 结果 | 明细 |
|---|---|---|
| **volume 数 = 2**（原图 + 合并 mask） | **PASS** | nvAxial.volumes.length=2 |
| **labels.json 类别数 == seg-meta 类别数** | **PASS** | seg-meta=117，labels.json n_classes=117 |
| **逐类开关（全关 -> overlay 隐藏）** | **PASS** | anyVisible=False，overlay.opacity=0 |
| **逐类开关（全开 -> overlay 恢复）** | **PASS** | overlay.opacity=0.6 |
| **保存触发（toast 已保存 MaskVersion）** | **PASS** | 新 version，source=manual |
| **保存 roundtrip（类别数一致）** | **PASS** | before=117，after=117 |

**逐体素正确性（Python 侧实测）**：MaskVersion 最新版 mask 与 worker 的
combined_mask.nii.gz 逐体素 **diff = 0**（512×512×78，背景 18450366 体素 +
非零 1996866 体素完全一致）；保存 roundtrip 后 dtype uint8、69 个非空 label
（117 类中 69 类在本序列非空，labels.json 全类列出、空类体素数 0——协议契约）。

## 7. 待核对编码清单（Uberon 空串，56 条）

SNOMED CT 全部非空（TS 官方 CSV + v1 人工口径）；以下 56 条 Uberon 暂空
（不编造，M5 引全量 Uberon 图谱后机校）：

kidney_cyst_left/right、vertebrae_S1、vertebrae_L1–L5、vertebrae_T1–T12、
vertebrae_C1–C7、brachiocephalic_trunk、subclavian_artery_left/right、
common_carotid_artery_left/right、brachiocephalic_vein_left/right、
atrial_appendage_left、iliac_artery_left/right、iliac_vena_left/right、
humerus_left/right、scapula_left/right、clavicula_left/right、
gluteus_maximus_left/right、gluteus_medius_left/right、gluteus_minimus_left/right、
autochthon_left/right、iliopsoas_left/right、costal_cartilages。
（hip_left/right、sacrum、skull、rib_*、femur_* 沿用 v1 的 UBERON 编码。）

## 8. Blockers（单列）

1. **【已解决】WSL2 nvidia-smi 不认 `nounits`**
   - 现象：`subprocess` 里 `--format=csv,noheader,nounits` → exit 2 "Option
     csv,noheader,nounits is not recognized"；交互 bash 里同样的命令却成功
     （带引号的 shell 解析差异）；`gpu_peak_memory_mi_b` 恒 None，交叉核对值缺失
   - 已试：① 交互 bash 复测（成功）→ ② 逐行复现 → 发现 **query 串被拼成单个
     argv**（`[exe, "--query-gpu=... --format=..."]`）——nvidia-smi 把空格后的
     token 当未知选项；③ WSL nvidia-smi 确实不认 `nounits`（返回 "841 MiB"）
   - 结果：改为两个独立 argv（`[exe, "--query-gpu=memory.used",
     "--format=csv,noheader"]`）+ `float(val.split()[0])` 取数字部分；
     `gpu_peak_memory_mi_b()` 实测返回 841.0（MiB）
2. **【已解决】torch 分配器峰值与 nvidia-smi 全局峰值口径差异**
   - 现象：主值 8580 MiB（分配器）vs M2 基线 5472 MiB（nvidia-smi 全局峰值，
     未减基线），数字不可直接比
   - 处理：报告以 torch 分配器为主值（进程内精确），nvidia-smi 轮询减推理前
     基线后作交叉核对；两值都记录并写明口径（§2 表 + segmentation.py docstring）

## 9. 实测记录（最终数字回填）

- **端到端重跑 #3（最终，真卡 amos_0004）**：exit 0；torch 分配器峰值
  **8580 MiB**（主值，三次 8580/8580/8580 一致）；nvidia-smi 交叉核对增量
  **4409 MiB**（峰值 − 推理前基线 841 MiB）；墙钟 **116.2s**（基线 118.8s 一致）；
  类别数 **117**；合并产物 combined_mask.nii.gz（uint8，304K）+ labels.json
  （117 条）落盘；GPU 锁全程持锁 + 正常释放（.gpu.lock）。
- **CDP Edge headless 实测**：6 项检查全 PASS（§6 表）；逐体素 diff=0（§6）。
