# BodyDonor 科研基线 PR 审核评论与修改建议

来源：Codex 再审（2026-11-25）+ 用户评论。用途：发给 WSL2 Claude Code，作为后续审核与验证工作的基线依据——deep-reviewer 与 verify-loop 须把新基线钉牢，避免 plan 与目标漂移。**不发 GitHub**（用户 2026-11-25 定：不必发）。

本轮建议：PR #5 修复审查报告误判 FRESH，并同步审核规则中的旧商业口径；PR #6 修复开发来源元数据被决策回流覆盖的问题后，可作为三集分离基础交付合入。多教师科研训练入口、模型接线和产品限制另列后续任务，不将主分支历史问题全部归因于这两个 PR。

# 1 审核基线与当前项目定位

| 对象 | 提交 | 状态 |
| --- | --- | --- |
| main | `421ef0c4b572dfb5f4c30963e0a4239a4fc6c59b` | 当前主分支 |
| PR #5 | `00473dacc869a9b67491975e0df00776b309eab7` | open，未合并 |
| PR #6 | `97a3fe8836ef37b14e6bcc91c0e18f752a1f4c05` | open，未合并 |

当前定位是非商业、research-only 的分割、科研与教学工具。研究训练可使用已确认符合项目科研口径的教师与数据。

已合入的 PR #3、PR #4 及 11 月 18 日决定是本轮政策依据：SynthSeg/MOOSE 本地科研蒸馏已放行；SA 义务不挡推理路由；SynthSeg/MOOSE 学生制品按既定方案隔离并单列条款。来源真实性、数据划分隔离、模态与类别契约、权重完整性继续属于工程与实验正确性要求。

nnUNet 框架与具体任务 checkpoint 分别登记。当前代码仍单独拒绝 nnInteractive 修正产物进入研究蒸馏，这是既有特殊项目政策，需要修正，SynthSeg 等也已放行教师模型。

# 2 PR #5 评论

发布位置：PR #5 Conversation（https://github.com/picspin/BodyDonor/pull/5）。其中 FRESH 问题已有线程，建议把修复细节追加到原线程（https://github.com/picspin/BodyDonor/pull/5#discussion_r4628680800），避免重复建立同一 finding。下面正文可作为一条完整审核意见发布。

## 评论正文

基于 main `421ef0c` 和本 PR `00473da`，按项目当前非商业 research-only 定位重新审核。本轮建议在合并前处理以下两项。

**一、P2：被 wrapper 拒绝的报告不能被 freshness checker 判为 FRESH。**

已复现：在临时 git 仓使用现有测试替身的 `missingfield` 模式，wrapper 因 finding 缺少 evidence/suggestion/reproduction 返回 exit 1，但残留 JSON 的 review_sha 等于 HEAD；随后 checker 返回 exit 0、打印 FRESH。这使未完成的审核结果可以被引用。该问题与项目是否商业化无关。

建议 wrapper 与 checker 共用轻量结构校验：checker 要求显式 `stale is False`，同时检查报告及 finding 的必需字段和类型。wrapper 在本次运行临时文件中收集结果，校验成功并检查 HEAD 后再原子发布正式报告；同 SHA 重跑时先使旧正式报告失效，防止失败后继续读取上次成功结果。

验收至少包括：缺 stale、stale=null/0/字符串 false、findings 结构损坏均拒绝；wrapper 失败后 checker 同样拒绝；同 SHA 成功后再失败重跑不能引用旧结果；HEAD 移动后拒绝；完整报告和合法空 findings 正常通过。FRESH 只表示报告完成且绑定当前提交，不表示所有问题已解决或允许合并。

**二、同步审核规则的科研基线，避免固定引用已失效的商业政策。**

`.claude/agents/deep-reviewer.md:26` 仍写“商用蒸馏仅 total、brain_aneurysm 禁用”。这些表述不能继续作为当前科研扩展的准入规则。本 PR 将规则来源固定为 main，因而应同步校正现行规则，明确以已合入的研究准入与项目决定为准。

建议该条改为：
“所有均属研究用途准入、教师及数据来源和制品条款记录，以当前注册表和已合入项目决定为依据；不得使用已退役的商业白名单限制科研范围。来源缺失、未登记任务、模态或输出契约不符，应按实际原因报告，不将工程未就绪解释为商业禁用。nnInteractive 修正产物按现行独立政策核对。”

这项旧口径是主分支既有问题，不应标成 #5 新引入的回归；但应随本轮审核规则固定化一并收口。规则改动在合入 main 后才成为 wrapper 的默认规则来源，合入前验证须写明实际使用的规则版本。

config commit hook 仅验证提交声明，不等同于运行期测试路径隔离。请保留其现有范围，将“测试不得写真实 config/数据目录”单独验收，避免把 hook 当作原污染问题已经解决的证据。

# 3 PR #6 评论

发布位置：PR #6 Conversation（https://github.com/picspin/BodyDonor/pull/6）。元数据问题的定位为 `config/qc_thresholds.yaml:81–84`，可追加到现有行级线程（https://github.com/picspin/BodyDonor/pull/6#discussion_r4690444820）。下面正文可作为一条完整审核意见发布。

## 评论正文

基于 main `421ef0c` 和本 PR `97a3fe8`，按项目当前 research-only 定位重新审核。支持把本 PR 作为三集分离与来源披露的基础交付；不要求恢复商用白名单，也不因教师含 NC 条款就拒绝科研蒸馏。建议先修复以下 P2 问题，再合入。

**P2：开发依据与决策反馈共用 calibrated_on/at，正常回流会覆盖真实来源。**

已在临时配置副本复现：调用一次 `record_review_decision(Settings(), 'accepted', path=p)`，multi_teacher_consistency 的元数据从 `holdout_30_dev / 30 / 2026-11-11` 变成 `holdout_30_dev / 1 / 当天日期`。来源和 note 仍是原 30 例开发依据，支持数与日期却成了反馈统计。现有正常 API 决策入口会调用这条路径。

建议拆分为开发/拟合 provenance 与反馈计数。可保留既有 calibrated_on/at 的反馈含义，将本 PR 核实的开发依据迁入独立结构化字段，例如 development_provenance，记录 source、case_ids_hash、n_cases、selected_at 和 procedure。字段名可按仓库惯例调整。

multi_teacher_consistency 与 sampling_priority 应分别保留真实开发日期与过程；w3/w4 定义的开发依据不能写成四个权重都已校准。`record_review_decision()` 只更新反馈统计；`calibrate_thresholds()` 及保存读回路径同样不能丢失历史开发依据。未来确有重新拟合时，另记所用数据和方法，不能仅因反馈数量增长就声称取得独立校准。

验收至少包括：单条与多种决策回流后开发 provenance 不变、反馈正确累计；校准及 YAML 保存读回后两类事实均可读取；旧配置缺新字段时兼容读取且不虚构 30 例来源；本轮不修改 0.80 阈值、w1–w4 数值及既有 QC 判定。先补失败用例，再修复转绿。

**同时收紧本 PR 的完成口径。**

calib 仍为空；check_derived_source 尚无生产调用方；teacher_reliability_entry 尚未接入实际消费链。因此本 PR 可验收为“冻结 CT gate/train 身份、披露 gate 上调参事实、提供检查基础”，不能据此宣称独立三集分离、可靠性解耦和加权训练均已完成。

这些是已披露的阶段边界，不作为额外回归要求全部塞进本 PR。需要后续建设独立划分、实际写入路径的来源检查及可靠性消费链。已参与调参的 30 例继续用于历史开发回归，不能通过改名恢复独立最终验收资格。

README/PR 描述中“prostate stays explicitly low-risk”建议改为“prostate teacher reliability remains explicitly low”。当前代码的 low 指教师可靠性低，对应高风险类别，不是低风险。

# 4 新基线修改方向可复制说明

建议发布为独立项目 issue 或新一轮计划说明，不将以下主分支历史问题全部计入 #5/#6 的回归。

## 评论正文

本轮后续工作以 BodyDonor 非商业 research-only 分割与教学工具为唯一产品定位。已确认的科研使用范围继续有效，不重新引入商业化审批、商业白名单或 commercial profile。

当前差距是“研究准入已放宽，但执行路径、产品限制与审核文档尚未统一”。建议按以下顺序收口：

1. **统一研究训练准入。** 注册表与 ontology 已允许 total_mr、SynthSeg、MOOSE、FleXray 等研究蒸馏，但 distill_data 仍只接收 total/manual-correction 来源。规范化 teacher_id、task、checkpoint 与来源记录，让数据准备调用同一研究判定；研究允许、工程未部署、输出契约不符分别报告。
2. **扩展通用研究数据准备。** Dataset501 现有 CT/15 类/TS117 输出约定继续作为历史对照。新增任务按模态、类别映射、输出形状和训练目标准备数据，不能让 total_mr、SynthSeg 或肿瘤模型都通过 TS117 文件完整性检查。新数据集使用独立标识与制品目录。
3. **把来源与条款写入制品。** 已允许的研究输入应能进入相应实验，同时将教师、checkpoint、数据来源与条款结构化写入 manifest 和模型记录。SynthSeg/MOOSE 按已确认方案隔离学生制品，避免将准入函数返回的条款只留在 reason 字符串中。
4. **保留默认路径的试用激活限制。** 现有导入应仍受无 license 月 20 例/年 200 例限制。这不是商业限制而是基于硬件和用户局部测试的考量。保留有用的使用统计与数据兼容。
5. **统一现行文档、UI 与审核规则。** 当前说明不再写“只有 total 可蒸馏”“SynthSeg 仅 QC”或“NC 自动禁止科研蒸馏”。历史报告保留并标明适用阶段；自有源码许可证与模型使用范围分别处理，不因定位变化擅自修改许可证。
6. **按数据划分使用研究数据。** 永不将 gate/test 样本用于训练或调参；其他合规训练划分的人工标签是否参与监督或蒸馏对照，由具体实验设计决定，不把 Dataset501 的纯伪标签约定扩大为全项目的人工标签禁令。

下一能力切片优先完成 total_mr 的正常路由、推理、落盘、审阅与 QC/trace；随后在通用研究数据准备接口上接入 SynthSeg 和任务型 nnUNet 模型，并用固定划分做未加权/加权对照。2D X-ray 独立定义多标签、坐标变换与审阅导出契约。

# 5 修改任务与验收条件

下列任务是工程建议，不改变已确认的研究准入范围。P1/P2 仅用于安排后续工作优先级；不把主分支历史缺口追溯为两个 PR 新增缺陷。

## A 审核结果有效性与规则

**归属：PR #5；合并前完成核心修复。**

- wrapper 与 checker 使用一致的结构校验。
- 失败、中断、同 SHA 重跑和 HEAD 移动均不能产生可引用的错误 FRESH。
- 审核规则采用当前科研定位，明确规则生效顺序。
- 回归覆盖脚本之间的完整链路，不能只分别验证输入输出。
- 运行期 config 隔离另立任务；提交 hook 不承担该能力。

## B 开发来源与反馈统计

**归属：PR #6；合并前修复字段冲突。**

- 开发 provenance 与反馈统计分别存储，字段含义唯一。
- 不从已被覆盖的运行时计数反推开发支持数；使用已核实的开发记录迁移。
- 回流、校准、保存、读回和 trace 消费方均保持来源语义一致。
- 测试仅写 tmp_path；保留原数值阈值、权重和既有 QC 判定。
- calib 建设与可靠性接线不扩大为本次 P2 修复的前置工程。

## C 研究训练入口统一

**归属：独立后续 PR；优先级 P1。**

代码依据：distill_data 来源判定（`src/bodydonor/distill_data.py#L55`）、研究准入判定（`src/bodydonor/ontology.py#L592`），均以 main `421ef0c` 为准。

已复现：SynthSeg 的 brain_mri_qc 在研究判定中允许，但数据准备的 source=teacher 或 synthseg 被来源白名单拒绝。其他已准入教师也有同类入口不一致。source=total 是历史来源约定，不能作为新教师接入规范。

验收：
- 使用如实标注的教师与任务来源，可到达同一研究准入判定；未知来源不会被默认为 total。
- 教师身份、任务、checkpoint、证明层级和数据来源可读回；历史 source 格式有明确兼容映射。
- 对当前明确允许的教师，用合成 fixture 覆盖来源接线；缺部署或输出契约不符应报工程原因。
- 对既有明确拒绝分支保留实测，尤其 nnInteractive 独立政策不能被无意绕过。

## D 按任务准备数据与记录制品

**归属：独立后续 PR；优先级 P1。**

代码依据：TS117 完整性检查（`src/bodydonor/distill_data.py#L309`）、固定 CT dataset 描述（`src/bodydonor/distill_data.py#L558`），均以 main `421ef0c` 为准。

验收：
- Dataset501 旧实验可复现；新任务使用独立数据集 ID 和目录。
- 完整性检查按具体任务类别与输出契约执行，不统一要求 117 文件或固定 15 类。
- MR 通道不会被写成 CT；类别映射与空间对齐可验证。
- manifest、dataset 描述和模型记录反映真实教师及数据来源，不继续统一写“TS total、Apache-2.0、commercial-safe”。
- 继承条款由结构化数据传递，并能从训练输入追踪到学生制品；不把许可 reason 字符串作为唯一记录。
- gate/test 泄漏检查在数据写入前实际执行；跨扫描、患者与派生版本的身份按数据来源定义。

## E 科研产品限制与文档

**归属：独立后续 PR；优先级 P1 至 P2。**

代码依据：导入路径额度检查（`src/bodydonor/pipeline.py#L127`）、试用额度实现（`src/bodydonor/usage_counter.py#L98`）、现有审核规则（`.claude/agents/deep-reviewer.md#L26`），均以 main `421ef0c` 为准。

验收：
- 默认科研导入超过 20 例/月仍会被要求激活 license；使用统计仍准确、幂等。
- 既有数据库记录可继续读取；不为移除产品拦截而破坏历史流水。
- CLI/API/UI 的当前说明与科研定位一致，需邮件授权激活以解锁科研使用。
- proposal、许可摘要、审核规则和用户提示明确区分历史商业规划与当前执行基线。
- 修改文档时保留第三方许可事实和历史报告；不自动将 BSL 等自有源码许可证改成其他许可证。

# 6 M7 与下一阶段计划修订

**一致性基线。** 固化数据与指标基线有价值，但附件 W2 中“改偏后测试仍通过”仅解释了容差边界，未证明测试能抓住错误。补充必失败的 mutation 验证，分别覆盖错误 case 集、均值、配置来源；对确定性且远离阈值的 case 用精确断言，容差只用于有原因的边界。

**划分与可靠性。** 已用于选聚合方式的 30 例继续承担开发回归；新建未参与调参的最终评估划分。check_derived_source 应接入实际校准写入路径；teacher_reliability_entry 接入消费链前，不能声称 w1/w4 已完成解耦。类别支持数不等于准确率，高 agreement 也不能单独证明正确。

**新模态缺信号。** 当前合成探针中，10 条所有过程信号均缺失的记录仍有 2 条按固定比例进入 auto。新模态初期需区分未知与低风险，不能将缺信号直接作为自动接受依据。此为主分支已有边界，不归因于 #5/#6。

**next-step draft 的基线校正。** 教师注册表、字段集与注册表路由已存在；SynthSeg/MOOSE/FleXray 的问题是部署、数据契约与消费接线，不能再写成仓库零存在或商业不允许。total_mr 研究准入已允许，但仓库就绪检查仍报缺权重记录/class map。CT 病灶任务不能用 liver_lesions_mr 作为同模态替代。

建议实施顺序：修复 #5/#6 → 统一科研规则与训练入口 → 完成 total_mr 纵向流程 → 接入 SynthSeg/任务型 nnUNet 并做加权训练对照 → 再扩展更多教师和 2D X-ray。数据划分与制品 provenance 随每个切片交付，不重新建立商业门禁。

# 7 证据与验证范围

| 检查 | 结果 | 范围 |
| --- | --- | --- |
| 两 PR 组合文件快照上的现有定向测试 | 193 passed，1 skipped，2 deselected | skipped 为缺少本地真实 manifest |
| 两条独立缺陷探针 | 2 条按预期失败 | 报告 FRESH 绕过、开发元数据被回流覆盖 |
| 新基线研究准入与使用统计相关测试 | 83 passed | 主分支定向检查 |
| 新基线蒸馏入口相关测试 | 50 passed | 主分支定向检查 |
| 注册表准入矩阵 | 36 条目中 28 条研究蒸馏允许 | 不是 28 个模型已部署 |
| 试用额度探针 | 当月 20 个 case 后报 TrialLimitError | 内存数据库复现 |

以上为本会话已完成的检查，并非重新执行 Claude 报告中的全量套件。依赖环境不是完整 uv.lock 同步，未完成完整 API、GPU 与真实数据回归。仓库注册表的就绪记录中，total 与 nnInteractive 路由可放行；这不等于本环境执行了真实推理。

未推送的一致性分支和完整 M7-PLAN-v3-research 未独立取到；相关计划意见依据两份附件、可读取主分支与已合入决定。项目科研用途基线来自用户明确定位与仓库决定；本轮不重新裁定第三方许可。

本地证据：test_review_findings.py、review-findings-junit.xml、review-focused-junit.xml、review-qc-junit.xml、research-policy-audit.json、research-policy-checks.xml、research-distill-checks.xml。

# 8 用户建议的下一阶段顺序

1. 先收口当前 M7。修复两个 PR 的问题，完成组合审核；在最新 main 上重新生成现状表，明确每项是“已登记、已接线、已实跑、已评估”中的哪一步。
2. MRI 第一切只做 total_mr 完整纵向流程。权重记录与类别映射 → 正常应用路由 → 摄入 → 推理落盘 → 对齐审阅 → QC/trace。先限定一种部位、明确序列类型，分别验收工程跑通和分割质量。
3. 随后做一个最小加权训练对照实验。固定数据、模型和训练预算，对比未加权、可靠性加权、可靠性×一致性；同时报告逐类质量与整类漏检。不要等多个新教师接完才首次验证权重是否有效。
4. 再按用途增加教师。脑 MRI 需求明确时接 SynthSeg；交互病灶需求明确时接一个 MedSAM2 变体。不同教师的 agreement 应限定在可比较类别与相同空间内，不能把一致直接解释为正确。
5. X-ray 单独切片。先定义二维、多标签重叠、坐标变换和原图叠加契约，再做摄入与审阅。按草稿所述的多标签输出，现有单标签合并方式不能直接复用。

MRI 切片还应处理一个现存边界：用十条四类信号全部缺失的合成记录调用现有分档逻辑，仍有两条进入 auto。这是主分支既有行为，不是两个 PR 引入的回归；但新模态初期缺信号很常见，应把“未知”与“低风险”分开。
