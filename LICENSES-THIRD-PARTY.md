# LICENSES-THIRD-PARTY — BodyDonor 第三方许可核查（M0）

> 核查日期：2026-11-06（全部条目当日经网络读取官方原文核实）。
> 核查方式：GitHub raw / GitHub API / HuggingFace API / Zenodo API / PyPI API 读取原文，
> 原文引用为逐字摘录（verbatim quote），标注 URL 与访问日期。
> 本文件是 M0 验收物之一；配套：`config/settings.yaml` 的 `teacher.allowed_tasks` 白名单 + CI 白名单负测试。
> 商用合规审计（M7-LICENSE-CHECK 商用列 + 本文 §1.3/§1.4/§2.2/§7 商用约束的汇总）抽在内部附件
> `COMMERCIAL-CHECK-internal.md`（已 gitignore，不入仓——R3 ④）。
> 结论如有变更（上游改许可、新增任务），更新本文件并跑 `bodydonor license` 复核。

---

## Checklist 总览（GRILL-REVIEW §1.4 六项）

| # | 判定项 | 结论 | 状态 |
|---|---|---|---|
| 1 | `total` 基础任务权重许可 | Apache-2.0，任意用途可用（含商用） | ✅ 已核实 |
| 2 | 高级任务授权边界 | 18 个任务需 license（免费非商业授权），另有 1 个任务 CC BY-NC 4.0 且无商业授权渠道 | ✅ 已核实（逐任务清单见 §2） |
| 3 | 商业授权持有主体 | University Hospital Basel（作者方），联系 jakob.wasserthal@usb.ch | ✅ 已核实 |
| 4 | nnInteractive 许可 | 代码 Apache-2.0；**模型权重 CC BY-NC-SA 4.0（非商业，ShareAlike）** | ✅ 已核实 |
| 5 | nnU-Net v2 许可 + 蒸馏传染 | 代码 Apache-2.0；教师权重许可决定学生权重传染性（分析见 §5） | ✅ 已核实 |
| 6 | SegVol / SAT 许可 | SegVol 代码 MIT；BAAI/SegVol HF 权重无 license 标签（部分待核实）；SAT 代码 Apache-2.0 | ⚠️ 主体已核实，一处待核实 |

**对 BodyDonor 的核心结论**（详见 §7）：

- 商业安全路径只有一条：教师用 TotalSegmentator **`total` 任务**（Apache-2.0 权重）蒸馏，学生可商用。
- nnInteractive 权重 CC BY-NC-SA 4.0：**只用于人工标注辅助（交互式修正），其输出 mask 不进商用蒸馏训练集**。
- TotalSegmentator 18 个 licensed 任务：非商业免费，但学生蒸馏传染性无书面结论前，其伪标签**不进商用训练集**。
- `teacher.allowed_tasks` 白名单（M0–M4 科研期）= 全部 open 任务 + 非商业 licensed 任务均可跑；**商用蒸馏只允许 `total`**。

---

## 1. TotalSegmentator（wasserth/TotalSegmentator）

### 1.1 代码许可：Apache-2.0

**原文引用**（`LICENSE` 文件头，https://raw.githubusercontent.com/wasserth/TotalSegmentator/master/LICENSE ，访问日期 2026-11-06）：

```
                                 Apache License
                           Version 2.0, January 2004
                        http://www.apache.org/licenses/
```

> 注：该 LICENSE 文件为未填参数的 Apache 2.0 模板（版权行为 `Copyright [yyyy] [name of copyright owner]` 占位符）。
> 版权归属以 README 自述为准：「Created by the department of [Research and Analysis at University Hospital Basel]」。

**PyPI 元数据佐证**（https://pypi.org/pypi/TotalSegmentator/json ，访问日期 2026-11-06）：
`"license": "Apache 2.0"`，当前版本 `2.18.0`。

### 1.2 `total` 基础任务权重：Apache-2.0，任意用途（Checklist #1）

**原文引用**（`README.md`，https://raw.githubusercontent.com/wasserth/TotalSegmentator/master/README.md ，访问日期 2026-11-06）：

> "Openly available for any usage (Apache-2.0 license):
> * **total**: default task containing 117 main classes
> * **total_mr**: default task containing 50 main classes on MR images
> * **lung_vessels**: lung_arteries, lung_veins, lung_airways, lung_airways_wall
> * **lung_vessels_LEGACY**: ...
> * **body**: body, body_trunc, body_extremities, skin
> ..."

**结论**：`total`（117 类 CT）与 `total_mr`（50 类 MR）权重 "Openly available for **any usage**"——
明确覆盖商业用途。BodyDonor CT 主路径依赖的正是 `total`。✅ 无传染风险。

**其余 open 任务清单**（同上 README，Apache-2.0 权重，访问日期 2026-11-06）：
`total`、`total_mr`、`lung_vessels`、`lung_vessels_LEGACY`、`body`、`body_mr`、`vertebrae_mr`、
`cerebral_bleed`、`hip_implant`、`pleural_pericard_effusion`、`head_glands_cavities`、`head_muscles`、
`headneck_bones_vessels`、`headneck_muscles`、`liver_vessels`、`oculomotor_muscles`、`lung_nodules`、
`kidney_cysts`、`breasts`、`liver_segments`、`liver_segments_mr`、`craniofacial_structures`、
`abdominal_muscles`、`teeth`、`trunk_cavities`、`ventricle_parts`、`liver_lesions`、`liver_lesions_mr`、
`vertebrae_pp`、`vertebrae_pp_refined`、`vertebrae_body`。

（代码级佐证：`totalsegmentator/map_to_binary.py` 的 `commercial_models` 字典外的任务均为 open，
https://raw.githubusercontent.com/wasserth/TotalSegmentator/master/totalsegmentator/map_to_binary.py ，访问日期 2026-11-06。）

**与注册表的差异注记**（M7-S3F ② 安装包实测，M7-S3F ⑤ W13 同步；`license_registry.py`）：
上表 README 清单与注册表 `research_distill` 状态（M7-S3P R4 M-2 统一：单一资格字段，原 research_use 已删）并非一一对应——

- **注册表 `allowed`（双证据齐全：README open + 本地 TS 2.10.0 安装包 `class_map` 含该任务
  且 `commercial_models` 不含）**：`lung_vessels`、`body`、`cerebral_bleed`、`hip_implant`、
  `pleural_pericard_effusion`、`head_glands_cavities`、`head_muscles`、`headneck_bones_vessels`、
  `headneck_muscles`、`liver_vessels`、`oculomotor_muscles`、`lung_nodules`、`kidney_cysts`、
  `breasts`、`liver_segments`、`craniofacial_structures`、`abdominal_muscles`、`ventricle_parts`
  （18 个）。
- **注册表 `unverified`（仅 README 单证据，本地安装包 `class_map` 不含，结构未核实）**：
  `teeth`、`trunk_cavities`、`liver_lesions`、`vertebrae_pp`、`vertebrae_pp_refined`（5 个）。
- **注册表 `unverified`（安装包实测 `commercial_models['vertebrae_body']=305` 需 license，
  与 README open 清单出入已注记）**：`vertebrae_body`。
- **不登记（README 标注 "..." 含义不明，本地安装包 `class_map` 不含，不准猜）**：
  `lung_vessels_LEGACY`（仅 `settings.yaml` 历史记录）。

### 1.3 需 license 的任务清单（Checklist #2）

**原文引用**（`README.md`，访问日期 2026-11-06）：

> "Available with a license (free licenses available for non-commercial usage [here](https://backend.totalsegmentator.com/license-academic/). For a commercial license contact jakob.wasserthal@usb.ch):
> * **heartchambers_highres**: ...
> * **appendicular_bones**: ...
> * **tissue_types**: subcutaneous_fat, torso_fat, skeletal_muscle
> ..."

代码级逐任务清单（`totalsegmentator/map_to_binary.py` 的 `commercial_models` 字典，逐字摘录，访问日期 2026-11-06）：

```python
commercial_models = {
    "heartchambers_highres": 301,
    "appendicular_bones": 304,
    "appendicular_bones_mr": 855,
    "tissue_types": 481,
    "tissue_types_mr": 925,
    "tissue_4_types": 485,
    "face": 303,
    "face_mr": 856,
    "brain_structures": 409,
    "thigh_shoulder_muscles": 857,
    "thigh_shoulder_muscles_mr": 857,
    "coronary_arteries": 509,
    "coronary_arteries_LEGACY": 507,
    "aortic_sinuses": 920,
    "renal_arteries": 710,
    "aorta_annulus": 713,
    "aortic_dissection": 716,
    "pulmonary_artery_landmarks": 514
}
```

（佐证：`totalsegmentator/registry.py` 的 `requires_license(task)` 即 `task in commercial_models`，
https://raw.githubusercontent.com/wasserth/TotalSegmentator/master/totalsegmentator/registry.py ，访问日期 2026-11-06。）

**授权边界**：

- 这些任务的权重**非商业用途免费**，需在 https://backend.totalsegmentator.com/license-academic/ 申请
  （该页为 Streamlit JS 应用，M0 以 README 原文引用为准；license 号形如 `aca_...`，
  用 `totalseg_set_license -l <number>` 设置——README 原文："After acquiring a license number for the non-open tasks you can set it with the following command"）。
- **商业用途**需另行购买 license。
- **特殊：`brain_aneurysm`**（README 原文，访问日期 2026-11-06）：
  > "**brain_aneurysm**: aneurysm (only works with TOF MRI images) (CC BY-NC 4.0 license, no commercial license available)"

  即 CC BY-NC 4.0，**无商业授权渠道**——连付费都不行，BodyDonor 禁用该任务。

- 权重下载走官方 license 服务器校验（`totalsegmentator/libs.py` 的 `download_model_with_license_and_unpack`
  POST `https://backend.totalsegmentator.com:443/download_weights`，带 `license_number`，访问日期 2026-11-06）。
  离线场景 README 提供复制 `~/.totalsegmentator` 的官方流程。

### 1.4 商业授权持有主体（Checklist #3）

**原文引用**（`README.md`，访问日期 2026-11-06）：

> "For a commercial license contact jakob.wasserthal@usb.ch"
> "Created by the department of [Research and Analysis at University Hospital Basel](https://www.unispital-basel.ch/en/radiologie-nuklearmedizin/forschung-radiologie-nuklearmedizin)."

**结论**：商业授权持有主体是**模型作者方（University Hospital Basel，USB）**，联系邮箱
jakob.wasserthal@usb.ch。非商业免费授权从 backend.totalsegmentator.com 申请。
BodyDonor 如需商用 licensed 任务，走 USB 商业授权，不是「使用者自行承担责任」的灰区。

### 1.5 免责声明（顺带核实）

README 原文（访问日期 2026-11-06）："Note: This is not a medical device and is not intended for clinical usage.
However, it is part of several FDA-approved products, where it has been certified as a component of the overall system."

——与 BodyDonor「科研/标注工具，非诊断」定位一致。

---

## 2. nnInteractive（MIC-DKFZ/nnInteractive）— Checklist #4

### 2.1 代码许可：Apache-2.0

**原文引用**（`LICENSE` 文件，经 GitHub license API 读取，https://api.github.com/repos/MIC-DKFZ/nnInteractive/license ，spdx_id=Apache-2.0，访问日期 2026-11-06）：

```
                                 Apache License
                           Version 2.0, January 2004
                        http://www.apache.org/lic...

   Copyright [2019] [Division of Medical Image Computing, German Cancer Research Center (DKFZ), Heidelberg, Germany]
```

### 2.2 模型权重许可：CC BY-NC-SA 4.0（非商业 + ShareAlike）⚠️ 关键发现

**原文引用**（`README.md` License 节，逐字摘录，https://github.com/MIC-DKFZ/nnInteractive ，访问日期 2026-11-06）：

> "Note that while this repository is available under Apache-2.0 license (see [LICENSE](./LICENSE)), the [model checkpoint](https://huggingface.co/MIC-DKFZ/nnInteractive) is `Creative Commons Attribution Non Commercial Share Alike 4.0`!
>
> Release model folders ship their own `LICENSE` file whose **first line is the license identifier** (e.g. `CC BY-NC-SA 4.0`); any following lines (such as a link to the full license) are ignored by the tool. At load time this first line is read and exposed as `session.license` so applications can display the model's license prominently. If a checkpoint folder has no `LICENSE` file, the official v1 checkpoint is assumed to be `CC BY-NC-SA 4.0` and any other checkpoint reports `!!MISSING!!`."

**原文引用**（`README.md` 代码示例注释，访问日期 2026-11-06）：

> "# License reminder: The official nnInteractive checkpoint is licensed under
> # Creative Commons Attribution Non Commercial Share Alike 4.0 (CC BY-NC-SA 4.0)."

**HuggingFace 元数据佐证**（https://huggingface.co/api/models/MIC-DKFZ/nnInteractive ，访问日期 2026-11-06）：
`tags: [... 'license:cc-by-nc-sa-4.0' ...]`

**结论**：

- 代码（含推理后端）Apache-2.0，可自由集成。
- **权重 CC BY-NC-SA 4.0：NonCommercial + ShareAlike。**
- 对 BodyDonor 的约束（写入 `teacher.allowed_tasks` 相邻的 nnInteractive 使用策略）：
  1. nnInteractive 仅用于**交互式半自动标注/人工修正辅助**（proposal §6 第 3 阶段的定位），此用途为非商业科研使用，NC 条款满足。
  2. **nnInteractive 修正产物（其模型参与生成的 mask）不得进入商用蒸馏训练集**——ShareAlike 条款下衍生权重要求同条款开放，商用即违约。这与 GRILL-REVIEW 1.8 的「蒸馏许可传染」联动：M4 学生模型的训练输入若含 nnInteractive 修正，学生只能科研用途。
  3. 部署时 UI 须展示 `session.license`（上游已内置该机制，照做即可）。

---

## 3. nnU-Net v2（MIC-DKFZ/nnUNet）— Checklist #5

### 3.1 代码与权重许可：Apache-2.0

**原文引用**（`LICENSE` 文件头 + 版权行，https://raw.githubusercontent.com/MIC-DKFZ/nnUNet/master/LICENSE ，访问日期 2026-11-06）：

```
                                 Apache License
                           Version 2.0, January 2004
                        http://www.apache.org/licenses/
   ...
   Copyright [2019] [Division of Medical Image Computing, German Cancer Research Center (DKFZ), Heidelberg, Germany]
```

**结论**：nnU-Net v2 作为训练基线（proposal §9），代码与官方训练框架均 Apache-2.0，**自身无传染问题**。

### 3.2 蒸馏传染分析（Checklist #5 后半）

nnU-Net 本身干净，**传染源只可能来自教师伪标签的许可**。学生模型 = 在教师伪标签上训练的 nnU-Net：

| 教师 mask 来源 | 权重许可 | 学生权重传染 |
|---|---|---|
| TotalSegmentator `total` 任务 | Apache-2.0（"any usage"） | **无传染，学生可商用** |
| TotalSegmentator 18 个 licensed 任务 | 非商业免费（custom license，条款细节未公开全文） | **可能被解释为衍生自 NC 权重 → 学生仅科研**（保守处理） |
| nnInteractive 修正产物 | CC BY-NC-SA 4.0 | **ShareAlike 明确传染 → 学生仅科研** |
| SegVol（若启用实验通道） | 见 §4，权重标签缺失 | 待核实前不进训练集 |

**书面结论**（M4 准入项，GRILL-REVIEW R9）：

- **商用蒸馏的安全路径：教师只用 `total` 任务原始 mask + 纯人工修正**，训练输入不含 nnInteractive/licensed 任务产物。此结论基于上游 README 原文 "Openly available for any usage"，是本文件能给出的最强书面结论。
- licensed 任务与 nnInteractive 产物的学生模型**推定为仅科研**，除非另行取得 USB 书面授权（jakob.wasserthal@usb.ch）。
- 该结论为工程侧保守判定，非法律意见；最终商用前由法务复核（与 BodyDonor 自身 BSL Additional Use Grant 同批处理）。

---

## 4. SegVol / SAT（逃生通道，仅实验用）— Checklist #6

### 4.1 SegVol 代码：MIT

**原文引用**（`LICENSE` 文件头，https://raw.githubusercontent.com/BAAI-DCAI/SegVol/main/LICENSE ，访问日期 2026-11-06）：

```
MIT License

Copyright (c) 2023 BAAI-DCAI
```

### 4.2 SegVol 权重

- 训练仓库镜像 `yuxindu/segvol`（HuggingFace）：`tags: [... 'license:mit' ...]`
  （https://huggingface.co/api/models/yuxindu/segvol ，访问日期 2026-11-06）。
- 官方推理权重 `BAAI/SegVol`（HuggingFace）：**无 license 标签**，model card 未标注许可，
  且为 `custom_code` 模型——**待核实**。
  （https://huggingface.co/api/models/BAAI/SegVol ，访问日期 2026-11-06：`tags` 中无 license 项。）
- 训练数据集 M3D-Seg（HuggingFace `GoodBaiBai88/M3D-Seg`）：Apache-2.0（同上 API，访问日期 2026-11-06）。

**结论**：SegVol 代码 MIT 无忧；`BAAI/SegVol` 权重许可**待核实**——启用逃生通道（`--experimental-text-prompt`）
前须向 BAAI 确认权重条款。在核实前，逃生通道结果本就打 `experimental` 标记、不进蒸馏训练集
（proposal §3），风险已被流程隔离。

### 4.3 SAT（SwissArmyTransformer）

- `THUDM/SwissArmyTransformer`：Apache-2.0
  （https://github.com/THUDM/SwissArmyTransformer ，GitHub API spdx_id=Apache-2.0，访问日期 2026-11-06）。
- BodyDonor M0–M4 未实际依赖 SAT；如后续引入 SAT 系文本提示模型，按同流程补登记。

---

## 5. QC 数据集许可（M1 将用到，顺带核实）

| 数据集 | Zenodo 记录 | 许可（API 原文） | 访问日期 |
|---|---|---|---|
| TotalSegmentator dataset v1（1204 CT，104 结构） | zenodo.org/records/6802614 | `cc-by-4.0` | 2026-11-06 |
| TotalSegmentator dataset v2（1228 CT，117 结构） | zenodo.org/records/10047292 | `cc-by-4.0` | 2026-11-06 |
| AMOS22（腹盆腔多器官基准） | zenodo.org/records/7262581 | `cc-by-4.0` | 2026-11-06 |

三者均 Zenodo `access_right: open` + CC-BY-4.0：可自由使用（含商用），**须署名**。
M1 下载登记时在引用文献里完成署名义务。

（Grand-Challenge 镜像 AMOS 的注册流程不适用——直接走 Zenodo 免审批通道。）

### 5.1 M1 下载与登记（2026-11-06）

**下载情况**（M1 执行实录）：

| 数据集 | 下载源 | 实际下载 | 用途 | 存放 |
|---|---|---|---|---|
| AMOS22 | **HuggingFace 镜像 `MedOtter/amos22-ct-dataset`**（CC-BY-4.0，per-case NIfTI） | **40 cases 子集**（imagesTr + labelsTr，1.3 GB，`amos_0001`–`amos_0067` 非连续 ID） | 独立金标准 hold-out | `~/data/BodyDonor/downloads/amos22_subset/` |
| TotalSegmentator dataset | **Zenodo 6802614 (v1) / 10047292 (v2) 已核实，未全量下载**（单流 ~218 KB/s，28.4 GB 全量需 >30h） | QC 冷启动延后至 M3 前执行；候选镜像 `YongchengYAO/TotalSegmentator-CT-Lite`（CC-BY-4.0，1228 CT）已核实 | QC 冷启动（M3 前） | — |
| AMOS22 全量 500 例 | Zenodo 7262581 amos22.zip 24.2 GB | **未全量下载**（对笔记本负担过大，见下） | — | — |

**40-case 子集的理由（文档化，proposal §7.1 / 任务 8）**：

- M1 期间仅需满足 hold-out 规模下限（≥30 cases、覆盖 15 个腹盆腔器官、每类 ≥10 cases——
  AMOS22 每 case 均含全部 15 类精标，40 cases 即每类出现 40 次）。
- 全量 500 例（24.2 GB Zenodo / 21.6 GB×2 HF 分卷）下载需 6–31 小时且占盘 >50 GB，
  笔记本（WSL2，952 GB 可用）无即时需求——M3/M4 蒸馏训练才需要更多训练数据。
- 子集镜像逐条核实为 CC-BY-4.0（HF API tags `license:cc-by-4.0`），上游为 Zenodo 7262581
  同一数据集；许可链无分叉。

**署名义务（CC-BY-4.0 Attribution）**：

> **AMOS22**: Ji, Yuanfeng et al. (2022).
> *Amos: A large-scale abdominal multi-organ benchmark for versatile medical image segmentation*.
> arXiv:2206.08016. Dataset: [doi:10.5281/zenodo.7262581](https://zenodo.org/records/7262581)
> (CC-BY-4.0; Zenodo creators: JI YUANFENG et al.). 40-case subset via HuggingFace mirror
> `MedOtter/amos22-ct-dataset` (CC-BY-4.0, upstream = Zenodo 7262581).
> Used by BodyDonor as independent gold-standard hold-out (never enters training).
>
> **TotalSegmentator dataset**: Wasserthal, J., Meyer, D., & Hannemann, R. (2022).
> *Dataset with segmentations of 104 important anatomical structures in 1204 CT images*.
> [doi:10.5281/zenodo.6802614](https://zenodo.org/records/6802614) (CC-BY-4.0);
> v2: [doi:10.5281/zenodo.10047292](https://zenodo.org/records/10047292).
> Planned for QC cold-start calibration (M3 prerequisite).

（Grand-Challenge 镜像 AMOS 的注册流程不适用——直接走 Zenodo 免审批通道。）

---

## 6. M0 结论落实：config 白名单 + 负测试

### 6.1 `teacher.allowed_tasks` 白名单（`config/settings.yaml`）

- **科研期（M0–M4）白名单**：`total` + 全部 open 任务（§1.2 清单）。
  licensed 任务（§1.3 的 18 个）**默认不在白名单**——需要时显式加白 + 登记 license 号来源。
- **商用蒸馏白名单（M4 起生效的约束）**：仅 `total`。
- **禁用**：`brain_aneurysm`（CC BY-NC 4.0 且无商业授权渠道，连付费路径都不存在）。
- nnInteractive 不走任务白名单（它是交互式修正工具不是批量任务），约束记在 §2.2。

### 6.2 CI 负测试（`tests/test_license_whitelist.py`）

推理 worker 的任务分发层拒绝执行白名单外任务：`brain_aneurysm`（禁用任务）、
`heartchambers_highres`（licensed 未加白）、任意未知任务名——三者调用必须 raise，不得进入权重下载/推理路径。

---

## 7. 汇总决策表

| 组件 | 许可 | BodyDonor 用途 | 商用约束 |
|---|---|---|---|
| TotalSegmentator 代码 | Apache-2.0 | 教师推理管线 | 无 |
| TS `total`/`total_mr` + open 任务权重 | Apache-2.0 | 教师伪标签 | 无；**商用蒸馏唯一安全教师** |
| TS 18 个 licensed 任务权重 | 非商业免费（USB custom） | 可选科研任务 | 商用需 USB 授权；蒸馏传染推定存在 |
| TS `brain_aneurysm` | CC BY-NC 4.0 | — | **禁用**（无商业渠道） |
| nnInteractive 代码 | Apache-2.0 | 交互式修正后端 | 无 |
| nnInteractive 权重 | CC BY-NC-SA 4.0 | 半自动标注辅助 | 非商业可用；**产物不进商用训练集**；UI 展示 session.license |
| nnU-Net v2 | Apache-2.0 | 蒸馏训练基线 | 无 |
| SegVol 代码 | MIT | 实验逃生通道 | 无 |
| SegVol BAAI/SegVol 权重 | **待核实** | 实验逃生通道 | 核实前不商用、不进训练集 |
| SAT 代码 | Apache-2.0 | （未依赖） | 无 |
| TS dataset v1/v2、AMOS22 | CC-BY-4.0 | QC 冷启动 / hold-out | 署名即可 |

**遗留待核实项**：
1. `BAAI/SegVol` HF 权重许可标签缺失（§4.2）——逃生通道启用前核实。
2. TS licensed 任务的非商业 custom license 全文条款（backend.totalsegmentator.com 为 JS 应用，
   M0 以 README 原文为准；申请 license 时会看到全文，届时补录本文件）。
3. TS 商业授权的价格/条款：联系 jakob.wasserthal@usb.ch（仅商用化时需要）。

---

## 8. M4 蒸馏许可传染书面结论（准入项，2026-11-08）

> 本节为 M4 准入项的正式书面结论：M4 蒸馏训练数据流在结论签发前不得启动。
> 复核日期 2026-11-08：上游原文重新读取，TotalSegmentator README
> "Openly available for any usage (Apache-2.0 license)"（`total` 列首位）与
> M0 核实（2026-11-06）一致，无改动；nnInteractive README License 节
> "the model checkpoint is `Creative Commons Attribution Non Commercial Share
> Alike 4.0`!" 原文一致，无改动；nnU-Net v2 Apache-2.0 不变。
> （来源：https://raw.githubusercontent.com/wasserth/TotalSegmentator/master/README.md；
> https://github.com/MIC-DKFZ/nnInteractive，访问日期 2026-11-08。）

### 8.1 结论

| 学生权重训练输入 | 许可传染 | 学生权重能否商用 |
|---|---|---|
| TotalSegmentator `total` 教师 mask + 纯人工修正 | 无传染 | **可以商用**（M4 商用路径，唯一安全路径） |
| 上述输入 + nnInteractive 修正产物 | CC BY-NC-SA 4.0 ShareAlike 传染 | **仅科研** |
| 上述输入 + TS 18 个 licensed 任务教师 mask | 推定传染（custom NC 授权） | **仅科研**，除非取得 USB 书面商业授权 |
| 上述输入 + SegVol 逃生通道产物 | 权重许可待核实 | 不进训练集（与 M0 一致） |

### 8.2 推理（为什么 `total` 路径无传染）

1. Apache-2.0 许可的是作为作品分发的代码与权重本身（"the Work"）；教师推理
   输出的 mask 是运行软件产生的数据，不含从权重中复制的表达性内容，不构成
   Apache-2.0 下的 Derivative Work。
2. 上游作者以 README 明示 `total` 等任务 "Openly available for any usage
   (Apache-2.0 license)"——商业用途在明示授权范围内。
3. 纯人工修正是项目自有原创劳动，不携带第三方许可。
4. 因此学生权重是 BodyDonor 自有训练产物，**不继承 Apache-2.0 的义务**
   （Apache-2.0 非 copyleft，无传染条款）；其许可按项目自身分层执行
   （Q1–Q10：后端 BSL 1.1，开源层 Apache 2.0；蒸馏流水线属专有层）。
5. nnU-Net v2 代码本身 Apache-2.0，训练框架无传染问题（§3.1）。

### 8.3 商用路径的约束（必须执行）

1. **训练输入来源强制**：蒸馏数据准备必须拒绝 nnInteractive 修正产物与
   licensed 任务教师 mask，只接受 `total` 教师 mask + 纯人工修正；来源须
   在训练集登记中逐 case 可审计（ModelVersion + 训练集清单）。
2. **署名与引用**：沿用 TotalSegmentator 论文引用要求（Radiology AI 论文 +
   nnU-Net 引用）；AMOS22 / TS 数据集 CC-BY-4.0 署名见 §5（训练数据若用
   AMOS 图像，署名义务同样适用；hold-out 金标准永不进训练集）。
3. **混合输入即降级**：一旦训练输入混入 §8.1 表中任一受限产物，该学生权重
   自动降级为仅科研用途，并在 ModelVersion.gate_metrics 与 M4-REPORT 中明标，
   不得事后追认为商用。
4. **nnInteractive 仍可作修正辅助**：其权重 CC BY-NC-SA 4.0 满足科研期非商业
   使用；修正产物只用于人工审阅环节，不进商用训练集（§2.2 约束不变）。

### 8.4 效力与边界

- 本结论为工程侧保守判定，**不是法律意见**；BodyDonor 商用化前由法务复核
  （与 BSL LICENSE 定稿同批处理，见 §3.2）。
- 上游改许可时本结论失效，须重新核实并更新本文件。
- M4 训练数据流自本节落盘起解锁，仅限商用路径输入；研究用混合输入另立
  训练集并标注仅科研。

---

## 9. 许可两轴与准入显式化（M7-S3P2 ⑤，2026-11-18 用户确认法务事实）

> §7 汇总决策表之后、软件准入判定（`src/bodydonor/license_registry.py`
> TEACHER_REGISTRY 两轴字段 + `src/bodydonor/ontology.py`
> mask_allowed_in_distill_training）的许可事实基线。本节不是法律意见。

**法务事实（用户 2026-11-18 给出）**：

1. research（研究用途）只化解 NC 条款，不化解 ShareAlike。
2. nnInteractive（CC BY-NC-SA 4.0）蒸馏出的学生权重是衍生作品，须以
   BY-NC-SA 分发。
3. SynthSeg（FreeSurfer SLA v1.0）蒸馏出的学生权重同理是衍生作品，须以
   BY-NC-SA 分发；若 repo 出站许可为不带 SA 的纯 NC，这些权重必须隔离为
   独立制品并单列许可。
4. 软件内不另建合规门禁，依据只留文档（本文件与注册表 restriction_basis）。

**注册表字段语义（M7-S3P2 ⑤ 迁移）**：

- `attribution_required`（恒 True）：署名义务——research 不化解署名。
- `share_alike`：SA 义务——仅 nnInteractive 为 True（CC BY-NC-SA 4.0
  ShareAlike 传染，§2.2）；SynthSeg 的 FreeSurfer SLA 不是 ShareAlike 许可
  （用户 2026-11-18 确认），Moose 权重 CC BY 4.0 亦非 SA。
- `license_verified`：准入证据强度（双证据 true / 单一来源或出入 false）——
  两轴描述义务不能单独编码准入（7 个 unverified 与 25 个 allowed 同为
  Apache-2.0，两轴值相同），准入判定只读本字段：
  未核实 → 阻断待核实；已核实且研究可用 → 放行，SA 派生物按
  output_license_inherits 保留条款。
- `output_license_inherits`：派生物保留条款登记（share_alike=True 条目
  强制非空——validate_entry 拒绝静默丢条款）。
