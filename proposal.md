# BodyDonor — Medical Imaging Segmentator Agent 方案提案（工作稿 v2）

> 来源：2026-11-06 用户提案 + Steering 评审改版 + grill-me（GRILL-REVIEW.md，R1–R12）+ 用户对 Q1–Q10 的拍板。
> 仓库名 BodyDonor。开发/测试机：WSL2（Win11 笔记本，Quadro RTX 5000 16GB）。
> 本文件是给 Claude Code 的工作稿，不是对外文档。

## 1. 定位与对标

- 产品：medical imaging segmentator agent。前端用自然语言标签/文本指定器官组织做分割，人工评判、审核、校正。
- 对标：3D Slicer（含 TotalSegmentator 插件）、MONAI Label、nnInteractive。
- 形态：TUI + Web UI 前端；后端 GPU 计算；可院内容器化部署。
- 模态：CT、MRI、X-ray。**首版只做 CT，跑通再扩 MRI/X-ray。**
- 首版定位：科研/标注工具，不写诊断用途。
- **标签语种：中英双语**（Q1 已定）。同义词进同一张映射表的 `synonyms` 字段。

## 2. 许可证：分层开源（Q2 已定）

| 层 | 许可证 | 内容 |
|---|---|---|
| Web UI、TUI、API 契约、导入、基础 QC | **Apache 2.0** | 公开源码，吸引用户和贡献者 |
| GPU Worker 容器、Edge Agent、批量调度、蒸馏流水线、授权模块 | **专有商业许可，仅发布镜像** | 付费价值所在，license 校验放在这里 |

- 后端核心（非镜像层）采用 **BSL 1.1，change date 3 年**；Additional Use Grant 覆盖：个人学习、评估、研究，以及学术和非营利机构的非临床科研使用；单一机构内部限定规模使用（例如每年处理病例数不超过 200 例，或仅支持单节点处理）。
- 这样 license 校验代码不会暴露，也不会产生“BSL 代码里藏授权锁”的合规问题。
- **不要自行生成最终 LICENSE 文本**，用官方 BSL 模板只填参数；Grant 最终文本需法务把关，产出前 LICENSE 文件先放占位说明。
- **蒸馏学生权重商用策略：科研 only**（Q4 已定）。若 TotalSegmentator 为 NC 类许可且传染成立，学生模型仅科研；商用需另谈授权或改用可商用教师。M4 准入项：蒸馏许可传染性书面结论，无结论则 M4 产物仅限科研用途。

### 2.1 License Key 指纹激活（离线激活设计）

- 支持离线激活：签名的 license 文件（Ed25519 签名的 JWT），机器指纹由用户导出，签发后导回。
- 指纹要容错：硬件变更、虚拟机迁移、容器重建都不能让激活失效。别绑太死，否则运维会被拖累。
- 激活信息只传指纹哈希与 license ID，不传影像或 PHI，与数据最小化原则一致。
- 授权模块属于专有镜像层，激活校验代码不进开源仓。

## 3. 自然语言分割的两种机制

- v1 主路径：**本体查表映射**。自然语言标签 → SNOMED CT / Uberon → TotalSegmentator 类别。
- 映射服务对每个 NL 标签返回**三态**：`resolved`（命中）、`ambiguous`（多候选，如“肝”命中 liver 与 hepatic vessel）、`unresolved`（零命中或低于置信阈值）。
- `ambiguous` → 交回前端让用户在候选列表里点选（候选附同义词与 SNOMED 概念 ID）；用户选择**写回映射表**（按租户或全局），下次直接 `resolved`。这是产品资产，不是补丁。
- `unresolved` → **不路由到 SegVol/SAT 逃生通道当默认行为**。逃生通道必须由用户显式开启（`--experimental-text-prompt` 或 UI 开关），结果打 `experimental` 标记，**不进蒸馏训练集**。
- 映射数据本身：v1 不引重型本体服务，**一个版本化的静态 YAML/JSON 查表**（`ontology/{version}/map.yaml`，条目含 `nl_label / synonyms / snomed_ct / uberon / totalseg_class / status`），git 管，代码评审可 diff。全量 SNOMED/Uberon 图谱是 M5 之后的事。
- 逃生通道：真文本提示模型（SegVol / SAT 类）只作实验通道，不把“任意文本都能分割”写成 v1 能力。

## 4. 架构

### 4.1 两层

- **云端 Control Plane**：Web UI、Auth/租户、项目与任务元数据、模型/许可证注册表、Agent 编排（LLM 只见元数据）、计费、更新分发、云端 Demo 数据面（A 形态）。
- **院内 Data Plane + Edge Agent**：Edge Agent 出站轮询、不开入站端口；Ingest；De-ID 与像素烧录检测；Job Runner → GPU Worker 池；QC 引擎 → 伪标签 → 抽检 → 蒸馏训练；存储；本地 Web UI / TUI（与云端同一 REST API 契约，两类瘦客户端）。
- 院内与云端之间走出站 HTTPS/mTLS，只传元数据、指标、缩略图。
- **mTLS：M0–M4 完全不碰，推迟到 M5**（Q9 已定：院内单机交付后、真正需要连接云端控制面时再实现）。MVP 先 HTTPS + token。

### 4.2 云端 demo 与“像素不出院”的关系

- “像素永不离院”指院内部署形态。云端 demo（A 形态）**只收公开或已去标识数据**。
- 上传页加强制勾选声明；后端对 DICOM 头做 PS3.15 去标识 + 像素烧录检测，**不通过则拒收**，不是隔离留存。
- 云端数据 TTL 到期自动删除。
- **云 demo 范围：M5 只做公开数据 + 上传页**（Q3 已定），Auth/租户/计费延后。

### 4.3 限制策略（配置化，代码不分叉）

```yaml
limits:
  cloud_demo: { max_upload_mb: 100, retention_hours: 24, pixel_egress: allow_public_only }
  on_prem:    { max_upload_mb: null, ingest: [upload, folder_import] }
deployment_mode: cloud_demo | on_prem
max_upload_mb: 100  # 仅 cloud_demo
```

### 4.4 前端分工（按数据通道，不做功能重复）

- **TUI/CLI（本地盘通道）**：`bodydonor import /path --recursive`（大批量绕 HTTP）、流水线一键化（§6 的 8 阶段触发/续跑/状态）、显存与耗时基线汇报、QC 抽检任务分配的领取命令。**不做人机逐片修 mask**。
- **Web UI（浏览器通道）**：niivue 审阅与人工修正（M2）、Series 勾选选择器、QC 抽检的通过/拒绝/修正操作、上传页与声明勾选。
- **共享的只有 API**：两个前端都是 FastAPI 的瘦客户端；“同一前端代码”改写为“同一 REST API 契约”。
- TUI 技术栈（Q5 已定，分阶段解耦）：**M0/M1 纯 CLI 起步**，用 argparse 或 Typer 建立命令树（`bodydonor import`、`bodydonor license`、`bodydonor status`），用 rich.table 在终端打印漂亮的导入自检报告；**M2 再嵌 Textual**：新增子命令 `bodydonor tui`（或 `bodydonor dashboard`），专门启动 Textual 应用，只负责通过 HTTP 轮询本地 FastAPI 后端，展示实时 GPU 队列、显存占用与流水线 ①→⑧ 状态机。CLI 批处理与交互式 TUI 互不耦合。

### 4.5 Agent 编排 LLM 只见元数据的具体边界

- **白名单字段**：LLM 可见 = `case_id/series_id`、Series 技术元数据（模态、层厚、方向、层数、HU 范围——即导入报告里的字段）、QC 分数与 flag、任务/项目结构、映射表（标签语 → SNOMED 概念）。
- **黑名单（硬边界，序列化层强制）**：像素数据、原始 DICOM 头（含患者字段）、文件路径中的真实姓名/ID、De-ID 映射表本身。
- **实现机制**：编排层有一个 `AgentContext` 构造器，只从上述白名单字段拼 prompt；像素与原始 DICOM 在类型上就不进入该构造器能访问的对象。CI 测试：构造 AgentContext 后对其序列化产物跑 PHI 关键词与二进制检测，命中即 fail。
- LLM 的角色边界：编排（任务拆解、失败重试决策、映射消歧建议、QC 抽检排序）——**不做诊断、不生成报告文本、不直接写 mask**。

## 5. Ingest：Import Bundle（MVP 单一入口）

MVP 砍掉 Orthanc。Ingest 收敛成一个入口 **Import Bundle**，底层是同一个函数：

```
ingest(path_or_stream) -> [Series]
```

| 输入形态 | MVP 支持 | 说明 |
|---|---|---|
| 单个 NIfTI（`.nii/.nii.gz`） | ✅ | 直接入库 |
| DICOM 文件夹或 zip | ✅ | 自动按 `SeriesInstanceUID` 拆分为多个 Series |
| 多序列批量（文件夹树或 zip） | ✅ | 递归扫描，每个 Series 独立成一条记录 |
| 文件夹监听、Orthanc DICOMweb、PACS C-FIND | ❌ 后置 | v1.1 的 Ingest 插件 |

实现要点：

- **100 MB 限制三处强制**（仅 cloud_demo，见 §4.3）：前端预检、反向代理（`client_max_body_size`）、后端流式计数。**解压后大小也要限**，防 zip 炸弹（限制解压总量、文件数、嵌套深度）。
- zip 炸弹负测试要具体化：高压缩比（>100×）、嵌套 zip（>3 层）、超多文件（>10k）三个 case 各一条测试，M1 验收“zip 炸弹负测试通过”才算可判定。
- 100 MB 对 CT 很紧：512×512×300 的 DICOM 约 150 MB，压缩后可能刚好够。demo 预置 2~3 个示例病例（**公开 CT 样例**，Q8 已定，不用真实患者数据），并支持“下采样或裁剪后上传”提示。
- **本地大批量不要走 HTTP 上传**：提供 TUI 或 CLI 的 `bodydonor import /path --recursive`，直接读磁盘路径。这正是 TUI 的价值点。
- **Series 选择器不能省**：一个 Study 里常有定位像、MPR 重建、薄层加厚层。规则过滤：**排除 `ImageType[0] == 'LOCALIZER'`；DERIVED 不一刀切排除，改为排序置后 + 报告标注**（MPR 重建、多数后处理序列恰恰是 DERIVED）；层数 < 30 排除；“非轴位”排除保留但允许用户勾回（规则过滤 ≠ 硬删）。用户在列表里勾选。
- **NIfTI 单文件没有 Series 概念**：`ingest() -> [Series]` 返回单元素列表即可，但**几何校验必须做**（affine 各向异性、非正交方向、非毫米 spacing 的警告），M1 验收里点名。
- **自检门**：导入后先输出“导入报告”（Series 数、被跳过原因、层厚、方向、HU 范围合理性），而不是直接开跑。

## 6. 零标注伪标签流水线（8 阶段，一键化）

教师模型 → 自动质控 → 抽检 → nnU-Net 蒸馏 → 新模型重生成伪标签 → 再抽检，循环。

1. Ingest（Import Bundle，见 §5）
2. De-ID 与像素烧录检测（Ingest 第一步）
3. 教师推理：TotalSegmentator v2（CT）出初版 mask；nnInteractive 作半自动标注/修正
4. 自动质控（QC 引擎，见 §7），过滤低质量 mask
5. 小规模人工抽检与修正
6. nnU-Net 蒸馏训练（默认基线 **nnU-Net v2**）
7. 新模型重生成伪标签
8. 再抽检，循环迭代

防误差放大的硬约束：

- **每轮蒸馏的训练输入永远是「教师（原始）mask + 人工修正」的混合，而非「上轮学生输出」**；学生输出只用于对比评估（vs hold-out），不回灌训练集。否则错误模式会被放大。
- 抽检队列强制混合采样：flagged 70% / clean 随机 30%，clean 组用于确认“没 flag 的真好”。

## 7. QC 规则与校准（阈值均为待校准初值，不要当真值）

规则：体积先验、连通域检查、空洞检查、边界平滑度、HU 分布合理性（CT）、多教师一致性（Dice < 0.80 则 flagged）、左右对称性（适用器官）、空 mask / 全 mask 检查。

- **多教师一致性在 M3 只有 TotalSegmentator 一个教师，该规则实际无法工作；M3 验收中注明“该规则在 M4 学生模型出现后启用”，不要为通过验收造假数据。**
- 阈值全部集中在一份 config（如 `qc_thresholds.yaml`），禁止散在代码里；改阈值是 config diff，不是代码 diff。
- **冷启动（无本地数据时）**：在 TotalSegmentator 公开集（含真值 mask）上跑 QC 引擎，拿「QC 分数 vs 与真值 Dice」的相关性来定初值——每个规则画出它的 ROC/分布，选 separating threshold。这能在 M3 之前完成，不依赖抽检。该数据集真值与教师同源偏乐观，所以冷启动阈值只当起点。
- **在线校准（M3 之后）**：人工抽检结果（接受/修正/拒绝）回流为标签，定期重算各阈值；每个阈值在 config 里带 `source: cold_start | calibrated_on_N_cases` 注记，报告里可见。

### 7.1 QC 数据集（Q6 已定）

- **TotalSegmentator Dataset**：直接通过 Zenodo 下载（记录号 `zenodo.org/records/6802614` 为 v1，或 `zenodo.org/records/10047292` 为 v2 扩展集），全公开免审批，提供标准 NIfTI 格式。
- **AMOS22（推荐）**：含 500 例多中心临床 CT，15 个核心腹盆腔器官，全部为纯人工精标，作为**独立金标准 hold-out**，检验蒸馏后的 nnU-Net 是否真正消除了教师偏差。下载：Zenodo（`zenodo.org/records/7262581`）或 Grand Challenge（`amos22.grand-challenge.org`）。
- 下载与许可登记在 M1 期间处理，进 `LICENSES-THIRD-PARTY.md`。

### 7.2 本院化校准（离线部署策略）

- HU 分布与边界平滑度**必须本院化**：强依赖本院 CT 设备厂商（GE/西门子/联影）、管电压（kVp）、重建卷积核（软组织核 vs 骨核）、层厚（1mm vs 5mm）以及造影剂期相（平扫 vs 动脉期/门脉期）。
- 上线首周先用公开集基线跑 50 例本院真实脱敏 CT，由院方医生在抽检台完成首轮 20–30 例确认后，写一个 **`bodydonor qc-calibrate` 命令**自动重算本院专属的 HU 分位数与层厚容差并生成新版本 QC 配置。

## 8. 金标准 hold-out 与晋级门禁（防误差放大的承重件）

- 小金标准 hold-out 集**永不进训练**，每轮学生模型晋级必须过门禁。
- **规模下限**：≥ 30 cases、覆盖全部目标器官类别；每类至少出现在 10 cases 中（低于此数 Dice 均值方差大到没有门禁意义）。
- **构成**：三源混合，防单一偏置——① TotalSegmentator 公开集抽 10–20（冷启动用，标注“与教师同源”）；② AMOS 或 BTCV 抽 10–20（独立来源）；③ 若 M3 后有本地抽检修正产物，择优 5–10 进 hold-out（这批是人工修正过的，最接近真实金标准，但也最像“学生见过的分布”，权重最高、数量最少）。
- **门禁判据要成对写**：不只 mean Dice，还有**每类 Dice 下限**（如任一目标类 Dice < 0.5 即 gate fail）+ 空漏检（某类在 hold-out 里存在但学生整类漏掉）。整体均值会掩盖整类失败。
- hold-out 案例的 `case_id` 列表固化在一个文件里（`eval/holdout.txt`），流水线启动时先做**泄漏检查**（训练集与 hold-out 交集必须为空，非空即中止）。
- 合成 hold-out 用 TotalSegmentator 公开集时注意其真值本身也是模型加人工迭代产出，与教师同源，存在偏乐观；论文类用途需另加 BTCV 或 AMOS 作独立评估。

## 9. 技术栈（MVP 定稿）

| 层 | MVP 选择 | 说明 |
|---|---|---|
| 后端 | FastAPI（Python） | |
| 存储 | 本地文件系统（按 `case_id/series_id/` 目录）+ SQLite 或 PG | `StorageBackend` 接口抽象，MinIO 后置 |
| 队列 | 进程内队列（默认，配 SQLite）/ PG（可选 profile） | Redis 后置；单卡 16GB 本来就是串行，不需要分布式队列 |
| 影像 I/O | pydicom、SimpleITK / nibabel | DICOM/NIfTI |
| 教师 | TotalSegmentator v2、nnInteractive | CT |
| 训练基线 | nnU-Net v2 | |
| Web 影像浏览 | **niivue**（NIfTI + 轻量 viewer，mask 叠加、逐类开关） | OHIF 默认要 DICOMweb 后端（= Orthanc），MVP 不用；OHIF 推迟到 M3 之后评估。niivue 吃 NIfTI + mask overlay，正好匹配本地存储格式，无需 DICOMweb——这是弃 OHIF 的实质理由 |
| 前端 | Web UI + TUI | 前端开发用 **frontend-design skill** |
| DICOM SEG 回写 | 导出功能，不依赖 Orthanc 验证 | pydicom 重读 + highdicom 校验；验收加一条：pydicom 重读回写文件，与源 mask 逐体素 diff 为零 |
| 部署 | docker-compose 只含 PG（可选），默认 SQLite + 本地文件系统 | Orthanc、MinIO、Redis 不在 MVP compose 里 |
| CLI | argparse 或 Typer + rich.table（M0/M1）；Textual 挂 `bodydonor tui`（M2 起） | 见 §4.4 |

### 9.1 M2 niivue 审阅的最小可用功能

- **做**：三正交面 + 最大密度投影；mask 按 TotalSegmentator 类别逐类开关与换色；切割/平移/窗宽窗位；**逐 slice 滚动浏览且 mask 同步重绘**；导出人工修正结果（把用户编辑写回 NIfTI mask，M2 允许“整类删除 + 画笔粗修”粒度，不要求体绘制级雕刻）。
- **不做（写进 M2 验收的“非目标”）**：4D/时间序列、DTI、三维任意切面自由角、多人实时协同标注、DICOM 原生渲染。
- **性能红线**：512×512×300 CT + 全类别 mask 在开发机浏览器里可流畅滚动（目标 < 100ms/帧），达不到就先降采样 mask 再叠加。这条进 §12 第 6 条的基线汇报。

## 10. 16GB 显存与 WSL2 的现实约束（开发机：Quadro RTX 5000）

- Quadro RTX 5000 是 **Turing 架构，不支持 bf16**。nnU-Net 默认 AMP 用 fp16，可用，但要实测稳定性（实测写进 M4 基线汇报）。
- **开发期 WSL2 里直接 venv 运行 GPU 任务**，不要求先容器化 GPU；GPU 容器化留到部署阶段验证（WSL2 GPU 透传 + Docker + NVIDIA Container Toolkit 层数多）。
- nnInteractive 与 TotalSegmentator 同时常驻会超显存，**分时**使用。
- 笔电训练会降频（散热、功耗墙），M4 验收的训练耗时接受较长；先用 **3d_lowres 或 2d + 小数据集**跑通流程。
- **数据目录放 WSL2 原生文件系统（`~/data/BodyDonor/`），不要放 `/mnt/c/`**（9P 协议读 DICOM 千层序列慢一个量级）。

## 11. 里程碑（Steering + grill-me 改版后）

| 阶段 | 内容 |
|---|---|
| **M0** | 仓库（本地 git，GitHub 远程待用户确认再建）、FastAPI 骨架、SQLite（默认）+ 本地文件系统（`StorageBackend`、`QueueBackend` 接口抽象）、CI 最小集、许可证登记。**TotalSegmentator 许可证核查提到最前面并产出结论**（6 项 checklist：`total` 基础任务权重许可、高级任务授权边界、商业授权持有主体、nnInteractive 许可、nnU-Net v2 许可 + 蒸馏传染、SegVol/SAT 许可；产出物 `LICENSES-THIRD-PARTY.md` + config 任务白名单 `teacher.allowed_tasks` + CI 白名单负测试）。版本 pin：独立 venv/uv lock，逐个验证 import 链，不共享环境。CLI 命令树起步（`bodydonor import`、`bodydonor license`、`bodydonor status`） |
| **M1** | Import Bundle（NIfTI、DICOM 文件夹、zip、多序列）、Series 选择器、导入报告、De-ID、NIfTI 几何校验。验收：三种输入各跑通一次，产出导入报告；zip 炸弹三 case 负测试通过。QC 数据集下载与许可登记（TotalSegmentator Zenodo + AMOS22） |
| **M2** | TotalSegmentator worker、本体映射（静态查表 + 三态回退）、niivue 审阅（做/不做清单 + 性能红线）、TUI（`bodydonor tui` Textual 监控面 + TUI import） |
| **M3** | QC 引擎（两级校准）与抽检（混合采样）。多教师规则延后（M4 学生模型出现后启用） |
| **M4** | nnU-Net 蒸馏与门禁（3d_lowres 起步；训练输入 = 教师 + 人工修正，学生输出不回灌）。准入项：蒸馏许可传染性书面结论；无结论则产物仅限科研用途 |
| **M5** | 流水线 + Edge + 云 demo（公开数据 + 上传页）。Orthanc/DICOMweb 为可选 Ingest 插件；mTLS 在此实现 |

### CI 最小集（M0）

lint + 单测 + zip 炸弹负测试 + 许可白名单负测试 + AgentContext PHI 负测试。GPU 推理不进 CI（显存/耗时基线手工汇报）。

## 12. 给 Claude Code 的启动指令（精简版 v2）

1. 仓库 BodyDonor，按 M0 开工：本地 git init（**不要擅自创建 GitHub 远程仓库，远程待用户确认**）；FastAPI 骨架、SQLite（默认）+ 本地文件系统（`StorageBackend`、`QueueBackend` 接口抽象，后续可替换）、CI 最小集（lint/单测/zip 炸弹/许可白名单/PHI 负测试）、许可证登记。docker-compose 只含 PG（可选）。
2. **先做 TotalSegmentator 许可证核查并产出结论**（6 项 checklist + `LICENSES-THIRD-PARTY.md` + 任务白名单 + 负测试），再写推理代码。核查范围包括 nnInteractive、nnU-Net v2、SegVol/SAT。
3. 版本 pin：独立 venv/uv lock，逐个验证 import 链，不共享环境。数据目录 `~/data/BodyDonor/`，不要放 `/mnt/c/`。
4. CLI 命令树起步：`bodydonor import`、`bodydonor license`、`bodydonor status`（argparse 或 Typer + rich.table）。
5. M1 验收：单 NIfTI、DICOM 文件夹、多序列 zip 三种输入各跑通一次，产出导入报告；zip 炸弹三 case 负测试通过。
6. 配置化限制：`max_upload_mb` 与 `deployment_mode: cloud_demo|on_prem`。
7. 开发期直接在 WSL2 venv 运行 GPU 任务，不要求先容器化 GPU。
8. **每个里程碑结束先汇报显存与耗时基线，再继续。**
9. 前端开发用 frontend-design skill。

## 13. 已定项

- CT 优先，跑通再扩 MRI/X-ray
- 开发/测试机：WSL2 + Quadro RTX 5000 16GB
- 仓库名：BodyDonor
- Ingest：Import Bundle 单入口，Orthanc 后置 v1.1
- 存储：本地文件系统 + SQLite/PG；队列进程内（默认）/ PG（可选）；viewer 用 niivue
- 路线：Claude Code 开发；grill-me 已落实方案；前端用 frontend-design skill
- 标签语种：中英双语（Q1）
- 许可证分层：前端 Apache 2.0；后端 BSL 1.1（change date 3 年）+ Additional Use Grant；GPU Worker/Edge/调度/蒸馏/授权模块专有商业许可、仅发布镜像；离线激活 Ed25519 签名 JWT + 容错机器指纹（Q2）
- 云 demo：仅公开数据 + 上传页（Q3）
- 学生权重：科研 only（Q4）
- TUI：M0/M1 纯 CLI 起步，M2 挂 Textual（Q5）
- QC 数据集：TotalSegmentator Zenodo + AMOS22 独立 hold-out；本院化校准走 `bodydonor qc-calibrate`（Q6）
- 版本 pin：M0 独立 venv/uv lock（Q7）
- demo 病例：公开 CT 样例（Q8）
- mTLS：M0–M4 不碰，M5 再实现（Q9）
- 抽检角色：科研期单人修正，权限模型预留 role 字段（Q10）

## 14. 待定项

- BSL Additional Use Grant 最终文本：需法务把关，产出前 LICENSE 文件放占位说明。
- TotalSegmentator / nnInteractive / nnU-Net / SegVol 的许可事实：grill-me 运行无网络权限未核实，M0 按 6 项 checklist 联网核实并产出结论。
- GitHub 远程仓库 picspin/BodyDonor 是否创建、何时推：待用户确认。
