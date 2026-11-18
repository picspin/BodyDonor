# M7-S3P2 终报：合并后第二修复轮（S3P2）——七项全部完成

日期：2026-11-18。协调方：S3P2 协调方（素子子代理）。分支 `feat/m7-s3-postmerge-2`，worktree `~/work/BodyDonor-S3P2`，基线 S3P HEAD `4207899`。**未 push、未 merge、未开 PR**（用户未批推 S3P，本轮亦不推）。

指导原则（用户原话）：项目不必把太多商业合规许可等设施限制写入软件，仅保留在文档中提供依据即可。优先文档、最小代码门禁。

## 1. 分支 / HEAD / 提交 / diffstat（协调方实测）

- HEAD：`2eaa31d`（docs(m7-s3p2): R4b 报告提交列表补 d9db450 + diffstat 终态 206）。
- 提交总数：`4207899..HEAD` 共 **72 提交**（实测 `git log --oneline | wc -l`）。
- Diffstat（实测 `git diff --stat 4207899..HEAD`）：**45 files changed, 3069 insertions(+), 515 deletions(-)**（含 10 份轮次报告 + 5 份审查 findings 文件；代码+测试口径见各轮报告）。
- 磁盘数据改动（不在 git 内）：R2 就地升级 21 份 source.json（attestation 字段）；R3a 新建 gitignored 内部附件 `COMMERCIAL-CHECK-internal.md`（.gitignore:32）。

### 轮次提交列表（实测）

| 轮次 | 提交 | 内容 |
|---|---|---|
| R1a（②） | `2a04e93` `188b708` `7f06ac4` `fb0a295` `302a213` `2e23e29` + 协调方 `82f5d35` | run_total modality 必填 + resolve_modality + 三调用方迁移 + 测试 + 审查 W 修复 + 报告（+协调方勘误） |
| R1b（③） | `b2422f0` `a9e53ef` `363f310` `35185b6` + 协调方 `f005e42` | SOPClassUID 原样回填 + 审查 W1/W2 修复 + 报告（+协调方勘误） |
| R2（①） | `4802460` `312612c` `4c4d8f0` `91b7dab` `73c2cf3` `e2bed4d` `0a5f050` `94ffc98` `969fb08` `1fbc7cb` `bcae0e3` + 协调方 `d0ae8a5` | attestation 三元状态 + 回填脚本证明字段 + 磁盘 21 份文件升级 + 审查 W1/W2 修复 + 报告（+协调方勘误） |
| R3a（④） | `cea6a4a` `a1926b8` `6910d6d` `2a9be01` `cab477e` `64d9562` `7651e45` `01ff7ef` + 协调方 `a135fbd` | 删商用白名单 + --profile + brain_aneurysm 三处阻断 + 商业审计附件化 + 报告（+协调方勘误） |
| R3b（⑤） | `fb960aa` `9dbcc36` `249c94c` `eb86cd3` `1b1fc82` `bc1b3b5` `729c235` `b06a3d2` `0a789f4` `897f2fa` `c802870` `cc948b2` `a49eea5` + 协调方 `eb0183b` | 两轴 + license_verified + 判定层迁移 + 决策表重做 + §9 法务事实 + 报告（+协调方归属勘误） |
| R4a（⑥） | `8505bc5` `e532105` `3aede7f` `15b6498` `74b6207` `5a4253e` `2bb6854` `dcc9c6d` + 协调方 `a1632cc` | ready 拆就绪三分 + 拆分后状态表 + 报告（+协调方勘误） |
| R4a-fix（⑥纠偏） | `a95200b` `6531609` `0969cdc` `3caae8f` `e003495` `a19345f` `6235ec2` + 协调方 `c1b6d87` | check_task SA 挡项纠偏 + 快照还原 + R4a 报告更正（+协调方勘误） |
| R4b（⑦） | `6d5a557` `5786f54` `71a8699` `bbb638d` `178f189` `dacfea8` `d9db450` + 协调方 `2eaa31d` | 术语清查零处 + 守卫测试固化 + 报告（+协调方勘误） |

### 过程记录：三次 128k 死亡与拆分

- R1、R1a 整派死于 128000 输出上限（零提交）→ R1a-v2 处方式派遣成功。
- R3 整派（④+⑤）死于同错（零提交）→ 拆 R3a（④）+ R3b（⑤），均成功。
- R4 整派（⑥+⑦）死于同错（零提交）→ 拆 R4a（⑥）+ R4b（⑦），均成功。
- 另：R4a 实施中协调方核查抓到 SA 挡路由政策变化 → R4a-fix 小修复轮纠偏（见 §2 ⑥）。
- 诊断（已多次实证）：`~/.claude/settings.json` 的 256000 会被按模型 cap 回 128000，改配置无效；唯一杠杆是输出节制 + 处方式派遣（协调方先探明、钉死位置、硬探索预算、每步即提交）。

## 2. 七项逐项证据

### ② run_total 签名层 fail-closed（R1a，HEAD 82f5d35）

- `run_total`（`src/bodydonor/segmentation.py:222-231`）：`*, modality: str`——keyword-only 必填，缺省即 TypeError。
- `resolve_modality`（`:191`）：(a) `.modality` 属性（`MODALITY_ALIASES` PT→PET、DX/CR→X-RAY）；(b) DICOM Modality 标签（ingest.py W4 语义）；(c) `DATASET_MODALITY` 对照表（amos/ts_public/ct_lite→CT，唯一陈述数据集→CT 之处）；否则 raise ValueError。
- 4 处生产调用方迁移（segment_series、ts_teacher_inference、ts_teacher_mask、run_distill_teacher_inference）；三脚本零 `"CT"` 字面量；S3P 未决 #4 闭合（NULL 模态不再降级为任务-only 门禁，改 SegmentWorkerError）。
- 审查：1 deep-reviewer，0 Blocker；W1（ValueError 逃逸到 app/cli 500，失败测试先行修复）；P1（pydicom 3.x 写入）实跑证伪。
- 协调方独立重跑受影响 4 文件：**62 passed、exit 0**；ruff 全绿。勘误：报告"12 个新测试"实测 11 个（已修正）。

### ③ SOPClassUID 原样回填，删"默认 CT"设计（R1b，HEAD f005e42）

- 单点解析 `resolve_source_sop_class_uid`（`src/bodydonor/export_seg.py:96`）：(a) 实例自带 sop_class_uid 原样用；(b) 合成源（inst is None）→ DX/CR/MR/CT 映射 fallback；(c) 真实实例缺 UID 或未映射模态 → SegExportError，绝不默认 CT。
- 提取：`ingest.py:474`（`extract_source_instances` 加 sop_class_uid）、`pipeline.py:336`（透传，旧 JSON 缺键为空串→raise 分支，向后兼容不写迁移脚本）。
- `verify_seg` 加 `expected_source_sop_class_uid` 参数（:337，逐字比对 :409-412，取代反查模态核对）；`export_and_verify`（:472）与 `_build_source_images` 同一 helper 取值，建成与期望永不分叉。
- 生产代码 CT UID 只剩 :92 合成表与 :418 反查表两处合法残留；`tests/factories.py`/`test_burned_ocr.py` 的 `…1.2` 是 fixture 数据未动。
- 审查：0 Blocker，2 W 双修（W1 真实实例缺 UID raise；W2 混合 SOP Class 序列导出前断言），失败测试先红后绿。
- 多帧 IOD 边界（如实记录）：Enhanced MR（…4.1/…4.4）与 PET 后两档（…130/…128.1）是多帧 IOD，highdicom 拒绝作单帧源——只在 resolve 层测原样 UID，全链路 round-trip 用单帧 PET …128。
- 协调方独立重跑受影响 4 文件：**62 passed、exit 0**（collect 22+15+15+10=62）；ruff 全绿。勘误三处：新测试 27→22、export_seg 16→15、CT 残留位置 :385→:92/:418。

### ① 来源证明层级 attestation，不删文件（R2，HEAD d0ae8a5）

- source.json 加 `attestation`（inferred|asserted）、`evidence`（回填的 `mtime_window`）、`evidence_scope`、`generated_by`（脚本名 + 运行时自算 sha256）。
- `read_teacher_provenance`（`src/bodydonor/distill_data.py:255-295`）返回三元状态 `provenance_status`（asserted/inferred/missing）；`provenance_missing` 布尔保留但由同一状态同源推导；`provenance_status_of`（:298-316）映射规则定死，非枚举 attestation 值 raise DistillDataError（审查 W1）。
- `write_teacher_source_json` 新写文件带 attestation=asserted + evidence=run_output；覆盖 inferred 时同样写 asserted（升级路径，回填脚本永不降级，审查 W2 半升级文件补齐）。
- `prepare_dataset` 预检：missing 仍 raise；manifest 每条 case 带 attestation（`DistillCase.attestation` + `manifest_entry`），inferred 肉眼可区分。
- ct_lite 库级文件：evidence_scope 枚举 25 个 case；读侧 scope 外新增 case 按 missing 处理（闭合 S3P 未决 #1 fail-open）。
- 磁盘实测（协调方）：21 份文件全部带四字段；`generated_by` 的 sha 经核对正是写盘时脚本版本（312612c），git 可追溯；另 20 份 amos_0035+ 实测文件确未被改动（无 attestation 字段）；`--dry-run` 幂等 0 可升级。
- 两个方法学漏洞全文写进 R2 报告（已知局限）：(a) mtime 窗口无法区分"同源产生"与"同一次 cp -r 拷贝"——单次非 `-a` 拷贝把 117 个 nii 压进同一窗口，恰好满足"全目录 mtime 一致性"；(b) WSL2 ext4 与 drvfs 的 mtime 语义、git checkout/rsync 重写行为未验证。
- 审查：W1/W2 双修，失败测试先行。协调方独立重跑受影响 3 文件：**101 passed、exit 0**（collect 57+23+21=101）；ruff 全绿。实施方全量：**747 passed、0 failed、0 errors**（R2 HEAD 时）。

### ④ 删商业合规设施（R3a，HEAD a135fbd）

- `COMMERCIAL_DISTILL_TASKS` 删定义及全部读值处（distill_data.py:87/:143/:146/:178/:180/:629/:693，`check_teacher_task_commercial` 整个函数删）；`--profile` 删（prepare_distill_dataset.py:44，**默认曾是 `"commercial"`**，删后 CLI 默认即 research 单口径）；`check_distill_source`/`prepare_dataset` 的 profile 参数一并去掉。
- brain_aneurysm 商业理由删除三处：license_registry.py:494 注册表条目（usage 纯商业一句，无非商业理由，已核实）、config/settings.yaml:75-77（blocked 列表 + 商业注释）、ts_runner.py:42 `RUNNER_BLOCKED_TASKS` 纵深镜像；blocked_tasks 机制本身（字段、执行层、展示）保留。brain_aneurysm 本身仍 fail-closed（注册表无条目 → SegmentWorkerError，实跑复核）。
- LICENSE-CHECK.md 未 tracked 的发现（报告 §0 最前面）：`git log --all -- "*LICENSE-CHECK*"` 为空——该文件从未进仓库；tracked 许可文档一直是 LICENSES-THIRD-PARTY.md；LICENSE-CHECK 内容在仓库外 plans 目录。按意图执行：商业审计内容抽成 `COMMERCIAL-CHECK-internal.md`（worktree 根，gitignored，.gitignore:32，文件在盘信息不丢），tracked 文档头部留指向行。请用户确认归属（a 保持现状 / b plans 仓自管 / c tracked 入仓）。
- 附带：实施方测 CLI 时顺手修一真 bug（`prepare_dataset` 的 `out_root` str 未转 Path 会 TypeError，一行，7651e45）。
- 审查：Blocker 0 / W 0。协调方独立重跑受影响 10 文件：**170 passed、exit 0**（collect 逐文件求和 =170）；ruff 全绿。报告 diffstat 口径已核对（19 文件/141 增是代码口径，实测 21 文件/369 增含报告 187 行 + 审查文件 41 行，删除数 279 一致）。

### ⑤ 两条轴替代原三档（R3b，HEAD eb0183b）

- 注册表 36 条目：`research_distill` 删字段，换 `attribution_required` + `share_alike` + `license_verified`（见下设计问题）；每条目依据写进 `restriction_basis`。实测：share_alike=True 仅 nninteractive 1 个；license_verified=False 恰 7 个（teeth/trunk_cavities/liver_lesions/vertebrae_pp/vertebrae_pp_refined/vertebrae_body/framework）；attribution_required 全 True；research_distill 全仓清零（src 零残留，剩余仅测试 docstring/守卫文本）。
- 转换表（协调方实测 dump）：25 个 allowed Apache-2.0 → 署名真/SA 假；nninteractive（CC BY-NC-SA 4.0）→ 署名真/SA 真；flexray（CC BY-NC 4.0）→ 署名真/SA 假；synthseg（FreeSurfer SLA v1.0）→ 署名真/SA 假（**暂定待用户复核**）；moose（权重 CC BY 4.0）→ 署名真/SA 假（NC 训练数据 caveat 不进 SA 轴，**暂定待用户复核**）；5 个未核实 Apache-2.0 单证据 → 署名真/SA 假、准入仍阻断；vertebrae_body → 暂按 Apache-2.0 填、verified=False 准入阻断（⑥ 再议）；nnunet_v2/framework → 暂按 Apache-2.0 填、verified=False 准入阻断（无权重可蒸馏）。
- **关键设计问题（请用户确认）**：两条轴不能单独编码准入——5 个 unverified 与 25 个 allowed 同为 Apache-2.0、两轴值完全相同，差别只在证据强度。诚实做法是加小字段 `license_verified: bool`；准入规则显式写进判定层 docstring（未核实→阻断；已核实且研究可用→放行，义务按两轴保留，share_alike 真的派生物按 output_license_inherits 保留 SA 条款）。若用户不接受，备选是把准入快照留在决策表测试里（不推荐，理由已写进报告）。
- 判定层迁移：ontology.py `mask_allowed_in_distill_training` 准入只读 license_verified；inherits 非空即列条款（不再限定 conditional 标记）；nnInteractive 修正产物既有硬拒分支不动。`validate_entry` 三 bool 校验（isinstance(bool)，审查 N1 收紧 int 1/0 漏洞）+ M-1 承接（share_alike 强制 output_license_inherits 非空）。
- 新旧判定对照：快照两测通过，**36 条目逐条一致、零静默变化**。
- 法务事实（用户 2026-11-17 任务给出，写入 LICENSES-THIRD-PARTY.md §9 与注册表 docstring）：research 只化解 NC 不化解 ShareAlike；nnInteractive 与 SynthSeg 蒸馏学生权重是衍生作品，须以 BY-NC-SA 分发；repo 出站纯 NC 则隔离独立制品；软件内不另建合规门禁，依据只留文档。
- 审查：Blocker 0，W1（docstring 计数漂移）+ N1（int 校验）双修。协调方独立重跑受影响 4 文件：**103 passed、exit 0**（collect 50+20+8+25=103）；ruff 全绿。
- **归属勘误**：R3b 报告初版写"synthseg/moose 用户 2026-11-18 确认"——查实是实施方误读派遣，用户从未确认过这两条取值（那是协调方派遣里的暂定分析、标明待用户复核的）。已勘误为"协调方派遣暂定、待用户复核"；四条法务事实的来源也正为用户 2026-11-17 任务原文。

### ⑥ ready 拆就绪三分（R4a + R4a-fix，HEAD c1b6d87）

- `ready` 拆成 `weights_present` / `class_map_present` / `share_alike`（share_alike 与⑤同字段复用）；推导函数写死规则：weights_present ← weight_integrity.status == "verified"；class_map_present ← class_map_ref 非空且无"待建"/"待落地"（含 `str(None)` 漏洞的边界修复，失败测试先行）；36 条目 `ready` 键清零（残留仅 `recalibration_ready` 另一概念 + 历史叙述注释）。
- `check_task` 改读三字段，拒绝原因写明挡项（"缺权重"/"缺 class_map"/"SA 义务"三分）；`total` 权重实测记录（sha256 `3489323a…9270`、164951587 字节、status=verified，2026-11-18 实测，审查 sha256sum 复跑一致）；其余 34 条目权重未部署/未核实一律 not_recorded 不造假。
- 拆分后状态表（报告逐条）：
  - 5 个缺 class_map（MR 组：total_mr/body_mr/vertebrae_mr/liver_segments_mr/liver_lesions_mr）：权重假/class_map 假/SA 假/verified 真——纯技术缺失，除此无其他阻断。
  - 其余 8 个含占位 class_map 的条目逐条列出（synthseg/moose/flexray + 5 个未核实 TS 任务，另 mri_core/nnunet_v2 无 ref 派生 False 一并列出）。
  - vertebrae_body：weights_present=False（未下载）、class_map_present=True（`ts-classes/v1 (TS 2.10.0 map_to_binary.class_map)` 在位）、share_alike=False、license_verified=False（保持）。曾经挡住它的是安装包 commercial_models id 305 需 license 与 README open 清单出入登记的 unverified；NC 框架下商用 license 要求不构成研究用途的暴露，pending 项直接消解（**论证请用户复核**）；证据冲突本身作为文档注记保留不抹掉（known_restrictions 与 provenance.verified 原文未动，测试断言 commercial_models 字样仍在）。
- **R4a-fix（SA 挡路由纠偏）**：R4a 初版把 `check_task` 准入写成 `weights and class_map and not share_alike`——SA 成了挡项，nninteractive 由 `[True, "ok+license_inherits"]` 变为 `[False, "blocked:not-ready"]`，且拒绝原因模板固定带"checkpoint 未部署"前缀（nninteractive 权重明明已部署，是假话）。协调方核查抓获：SA 是义务不是可用性，忠实拆分应零政策变化。已纠偏：准入改为权重+class_map 两项；SA 只作放行原因里的义务注记（`allowed（SA 义务：CC-BY-NC-SA 4.0……）`）；"checkpoint 未部署"前缀仅当缺权重时出现；快照还原（其余 35 条零变化）；R4a 报告 §4 原"预期语义变更"论证作废并更正。**SA 是否该挡路由是政策问题，请用户定**（若改回"SA 挡"，分发层需同步）。
- 审查：R4a W1（挡项原因三元表达式优先级陷阱，失败测试先行）；R4a-fix W1（SA 条目经 check_worker_task 可达性）实测生产不可达、不修。协调方独立重跑受影响 6 文件：R4a **150 passed**、R4a-fix **150 passed**，exit 0（collect 逐文件求和 =150）；ruff 全绿。

### ⑦ 术语纠偏（R4b，HEAD 2eaa31d）

- 全仓清查（检索词六个：open source/open-source/opensource/开源/Openly available/openly available；范围 worktree 全仓文本 + plans 目录，git ls-files 188 文件扩展名普查 + Python re 交叉实测）：**零处 NC 许可成分被表述为 "open source"——如实零改动文件**。
- 命中分类（worktree 18 行 + plans 10 行，逐条列报告）：全部属于保留类——上游 Apache-2.0 README 逐字引用（"Openly available for any usage"，total/total_mr 权重原文，表述准确）、仓库自身 Apache-2.0 层（proposal.md 分层开源、LICENSES-THIRD-PARTY.md:397 开源层）、本轮报告/审查文档自身（守卫跳过 178f189）。NC 成分在仓内既有表述已是正确口径（"CC BY-NC-SA 4.0（非商业，ShareAlike）"、"non-commercial only" 等）。
- 守卫固化：`tests/test_license_whitelist.py::test_no_nc_component_described_as_open_source`（正则抓相邻组合表述、不碰 "Openly available" 引文；扫描 git ls-files 受跟踪文件；白名单按行号动态读取；解码失败记 violation；探针实测：追加违规行→failed 抓住，未跟踪文件→passed 不扫）。
- 审查：6 findings 全部闭环（4 修 1 不采纳 1 闭环；F3 不采纳有实测依据——OSI 子串误伤 "repository"）。协调方独立重跑受影响 2 文件：**59 passed、exit 0**（collect 50+9=59）；ruff 全绿。

## 3. 全量数字（实施方与协调方各一遍）

| 方 | 时点 | 数字 |
|---|---|---|
| 实施方（R2 轮） | R2 HEAD（bcae0e3 时） | **747 passed、0 failed、0 errors**（全量后台跑完 exit 0；进度行 10×72+27=747 dots 无 F/E/s 字符，实测核数） |
| 协调方（本人独立重跑） | 终态 HEAD `2eaa31d` | **748 passed、0 failed、0 errors、0 skipped**（JUnit `m7-s3p2-coordinator-fulltest-junit.xml`：tests=748 errors=0 failures=0 skipped=0；进度行 748 dots 无 F/E/s；PYTEST_EXIT=0；ruff 全仓 `All checks passed!`，RUFF_EXIT=0） |

- 747→748 的净 +1 为 R3a–R4b 各轮测试增减 churn（R4b 守卫测试 +1 为可识别项；实施方在 R2 之后各轮未再跑全量，只跑受影响文件——各轮受影响文件数字见 §2，均为协调方独立重跑核实）。
- 全量跑前核实（协调方）：五条 `__file__` 全指 S3P2 worktree src（附录）；bodydonor.db 与 eval/*.json 在 worktree 内；GPU 锁串行（全量约 2 小时，慢于 43 分钟估计——含 local_artifacts 真卡用例）。

### 附录：`__file__` 五条路径（全量跑前实测）

```
bodydonor                  /home/gogvf/work/BodyDonor-S3P2/src/bodydonor/__init__.py
bodydonor.license_registry /home/gogvf/work/BodyDonor-S3P2/src/bodydonor/license_registry.py
bodydonor.distill_data     /home/gogvf/work/BodyDonor-S3P2/src/bodydonor/distill_data.py
bodydonor.ontology         /home/gogvf/work/BodyDonor-S3P2/src/bodydonor/ontology.py
bodydonor.export_seg       /home/gogvf/work/BodyDonor-S3P2/src/bodydonor/export_seg.py
```

## 4. 未决清单（不写"全部修复"）

**请用户定的事项：**

1. **license_verified 字段方案**（⑤）：两轴不能单独编码准入，须加该字段（R3b 报告最前节，备选与不推荐理由已写）。是否接受？
2. **synthseg / moose 两轴取值复核**（⑤）：FreeSurfer SLA 非 SA（share_alike=False）与 moose NC 训练数据 caveat 不进 SA 轴——均为协调方派遣暂定，待用户复核。
3. **vertebrae_body NC 框架消解论证复核**（⑥）：NC 框架下无商业暴露、pending 项直接消解的论证（R4a 报告），请用户复核确认后该 pending 项关闭；证据冲突本身已保留为文档注记。
4. **SA 是否该挡路由**（⑥ R4a-fix）：已还原为零政策变化（SA 只作放行义务注记）；若用户决定 SA 应挡路由，分发层需同步。
5. **LICENSE-CHECK.md 归属**（④）：该文件从未进仓库；商业审计内容已抽成 gitignored 内部附件 `COMMERCIAL-CHECK-internal.md`。是否需要 tracked 入仓（a 保持现状 / b plans 仓自管 / c tracked 入仓）？
6. **synthseg / moose 蒸馏学生权重的出站许可形态**（⑤）：BY-NC-SA 分发 vs 隔离独立制品，待法务澄清后落 §9 补录。
7. **快照 `tests/data/m2_decision_table.json` 历史字段名 research_distill**：快照读判定结果不读注册表字段，保留；若用户接受 license_verified 方案，下轮可考虑重做快照格式（非必需）。

**已知局限与遗留：**

8. **source.json 无 sop_class_uid 键需重跑 ingest**（③ 未决）：R2 已回填的 45 目录 source.json 无此键，真实引用路径导出会 raise（W1 修复后 fail-closed）。
9. **20 份 amos_0035+ 实测 source.json 无 attestation 字段**（① 未决）：读侧映射 asserted（默认实测），行为正确，磁盘未被改动；若要显式标注需重跑 write_teacher_source_json 或扩展升级路径。
10. **方法学漏洞 (a)(b)**（①）：mtime 窗口只能证"同一次拷贝"不能证"同源产生"；WSL2 drvfs mtime 语义未实验验证。回填结论的可信度以"WSL2 drvfs mtime 行为可信"为前提，该前提未经实验验证。
11. **多帧 IOD 源的 SEG 导出**（③）：Enhanced MR / PET 后两档作源时 highdicom 拒绝——如研究方向扩到这些模态，需多帧 SEG 路径（highdicom 支持，本项目未接）。
12. **`expected_source_modality` 与 `expected_source_sop_class_uid` 同时给出时**（③）：后者取代前者核对（设计取舍，已写进 verify_seg docstring）；若将来要两道都核对，需拆分支。
13. **prepare_dataset dry_run 性能**：~9 分钟量级（M6 已记录"慢非挂"），未做优化（非本项范围）。
14. **LICENSES-THIRD-PARTY.md 历史原文引用保留**（④）：:137-140/:333/:338/:350 的 brain_aneurysm 禁用句是核查记录（README 逐字摘录），非白名单设施——保留如实记录；若用户希望同步改写为"已随 R3 ④ 移除阻断"口径，需另行小改。
15. **上游 README "Openly available for any usage" 引文**（⑦）：按派遣逐字保留；若上游将来改口径，引用需随上游更新重扫（访问日期已注记）。

## 5. 协调方勘误记录（本轮抓到的报告/实现错误，均已修正并提交）

- R1a：报告"12 个新测试"→实测 11 个。
- R1b：新测试 27→22、export_seg 16→15、CT UID 残留位置 :385→:92/:418。
- R2：提交列表漏 3 个后续报告提交（969fb08/1fbc7cb/bcae0e3）。
- R3a：提交列表漏本报告提交 01ff7ef；diffstat 口径核对（代码 19 文件/141 增 vs 实测 21 文件/369 增，差额即报告+审查文件）。
- R3b：**归属误读**——报告写"synthseg/moose 用户 2026-11-18 确认"，查实用户从未确认（协调方派遣暂定待复核）；法务事实来源正为用户 2026-11-17 任务原文。提交列表漏 a49eea5。
- R4a：法务事实归属同上误读（已正为用户 2026-11-17 任务给出）；提交列表漏 dcc9c6d。
- R4a-fix：提交列表漏 6235ec2（跨行锚点一次未命中后修正）。
- R4b：提交列表漏 d9db450；diffstat 198→终态 206。
- **实现返工**：R4a 的 check_task SA 挡路由（政策变化 + "checkpoint 未部署"假话前缀）→ R4a-fix 纠偏还原，见 §2 ⑥。

## 6. 报告与日志文件

- 终报（本文件）：worktree 根 `M7-S3P2-REPORT.md`；VM 同步件 `~/workspace/bodydonor/M7-S3P2-REPORT.md`。
- 轮次报告：`M7-S3P2-R1a-REPORT.md`、`M7-S3P2-R1b-REPORT.md`、`M7-S3P2-R2-REPORT.md`、`M7-S3P2-R3a-REPORT.md`、`M7-S3P2-R3b-REPORT.md`、`M7-S3P2-R4a-REPORT.md`、`M7-S3P2-R4a-fix-REPORT.md`、`M7-S3P2-R4b-REPORT.md`（worktree 根 + VM `~/workspace/bodydonor/` 同步）。
- 审查 findings：`M7-S3P2-R3a-REVIEW.md`、`M7-S3P2-R3b-REVIEW.md`、`M7-S3P2-R4a-REVIEW.md`、`M7-S3P2-R4a-fix-REVIEW.md`、`M7-S3P2-R4b-REVIEW.md`（worktree 根）。
- 协调方独立全量：JUnit `~/work/bodydonor-plans/m7-s3p2-coordinator-fulltest-junit.xml`、日志 `~/work/bodydonor-plans/s3p2-coordinator-fulltest.log`（VM 同步件见 `~/workspace/bodydonor/`）。
- 派遣文件：`s3p2-r1-dispatch.md`、`s3p2-r1a-dispatch-v2.md`、`s3p2-r1b-dispatch.md`、`s3p2-r2-dispatch.md`、`s3p2-r3a-dispatch.md`、`s3p2-r3b-dispatch.md`、`s3p2-r4a-dispatch.md`、`s3p2-r4a-fix-dispatch.md`、`s3p2-r4b-dispatch.md`（VM `~/workspace/bodydonor/` + WSL2 `~/work/bodydonor-plans/`）。
