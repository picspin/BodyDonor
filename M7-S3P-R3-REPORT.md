# BodyDonor M7-S3 合并后修复轮 R3 报告：④ 注册表门控放宽

- 日期：2026-11-16
- 分支：`feat/m7-s3-postmerge`，基线 R2 HEAD `08fc490`
- 用户原话：不想收紧，要让注册表门控更宽松，nnInteractive 各类教师模型都尽可能匹配适配的模态去使用
- 提交：`a76d55f`（放宽 + 审计测试）、`7d945b9`（审查修复）、报告 commit（见 git log）

## 一、审计表（36 条目逐条，modalities 对照书面支持范围）

证据源：`~/work/bodydonor-plans/M7-LICENSE-CHECK.md`（§一 总表 + 分教师章节）、
各教师 README（nnInteractive master readme.md 2026-11-16 curl 实取）、
已部署 checkpoint 实测（`~/data/BodyDonor`、`~/.totalsegmentator`）、
TS 2.10.0 安装包（venv-totalseg，M7-S3F ② 已核）。

| # | teacher_id / task | 原 modalities | 新 modalities | 书面支持范围 | 是否拓宽 | 依据出处 |
|---|---|---|---|---|---|---|
| 1 | totalsegmentator / total | (CT,) | (CT,) 不动 | CT（117 类） | 否 | LICENSE-CHECK §四：README "**total**: default task containing 117 main classes"；S3 验收安全属性红线 |
| 2-6 | totalsegmentator / total_mr, body_mr, vertebrae_mr, liver_segments_mr, liver_lesions_mr | (MR,)×5 | (MR,)×5 不动 | MR | 否 | LICENSE-CHECK §四："**total_mr**: default task containing 50 main classes on MR images"；其他 _mr 公开任务同清单 |
| 7 | mri_core / feature_encoding | (MR,) | (MR,) 不动 | MR | 否 | LICENSE-CHECK §二（Duke-110K 训练数据，脑 MRI 不在覆盖内） |
| 8 | nninteractive / interactive_correction | (CT, MR, PET) | (CT, MR, PET) 不动 | CT/MRI/PET/显微 | 否（已覆盖数据域内全部登记模态） | README §Abstract 原文 "Trained on 120+ diverse volumetric 3D datasets (CT, MRI, PET, 3D Microscopy, etc.)"（master readme.md，2026-11-16 curl 实取）；显微/微CT 不在本项目数据域内不登记 |
| 9 | synthseg / brain_mri_qc | (MR,) | (MR,) 不动 | 脑 MRI | 否 | LICENSE-CHECK §六（脑专用教师） |
| 10 | moose / moose_qc | (CT, PET) | (CT, PET) 不动 | CT/PET | 否 | LICENSE-CHECK §一 总表 'CT/PET'（§五） |
| 11 | nnunet_v2 / framework | (CT, MR) | (CT, MR, **PET**) | MRI/PET（总表） | **是（+PET）** | MR/PET：LICENSE-CHECK §一 总表 'MRI/PET'；CT：§七 结论"框架自适应任意数据集" + Dataset501 dataset.json channel_names['0']='ct' 实证（本轮之前已有，审查后补 §七 出处） |
| 12 | flexray / flexray_xray | (X-ray,) | (**X-RAY**,) | X-ray | 大小写修复（非拓宽） | "X-ray" 混合大小写在比对侧 strip().upper() 后变 "X-RAY" 永不相等——潜在缺口，ready=False 掩盖；LICENSE-CHECK §三 |
| 13-30 | totalsegmentator / 18 个公开 subtasks（组一：lung_vessels, body, cerebral_bleed, hip_implant, pleural_pericard_effusion, head_glands_cavities, head_muscles, headneck_bones_vessels, headneck_muscles, liver_vessels, oculomotor_muscles, lung_nodules, kidney_cysts, breasts, liver_segments, craniofacial_structures, abdominal_muscles, ventricle_parts） | (CT,)×18 | (CT,)×18 不动 | CT | 否 | M7-S3F ②：LICENSES-THIRD-PARTY §1.2 open 清单 + TS 2.10.0 安装包 class_map 双证据 |
| 31-35 | totalsegmentator / teeth, trunk_cavities, liver_lesions, vertebrae_pp, vertebrae_pp_refined | (CT,)×5 | (CT,)×5 不动 | CT | 否 | M7-S3F ②：仅 README open 清单单证据，登记 unverified |
| 36 | totalsegmentator / vertebrae_body | (CT,) | (CT,) 不动 | CT | 否 | M7-S3F ②：安装包 commercial_models 含（id 305，需 license），按实况登记 unverified |

### 本轮真正修复的门控缺口（不是拓宽，是模态匹配）

1. **PT → PET 别名**（`MODALITY_ALIASES`）：DICOM PS3.3 C.7.3.1.1 中 PET 的
   Modality 代码是 **PT**；ingest 层（`ingest.py:366`）原样记录 DICOM 头内值
   不翻译，而注册表登记 "PET"。`check_task` 归一化只做 strip+upper，真实
   PET series 派发 nninteractive/moose 任务一律被拒。别名映射后 PT 匹配 PET。
2. **DX/CR → X-RAY 别名**（审查 W2 修复）：投影 X 光 DICOM 代码 DX/CR 此前
   同样永拒 flexray_xray；口径与 review_session.py:293 一致。
3. **flexray X-ray 大小写**：登记值改为比对口径内的 `X-RAY`。

## 二、特别核查结果（多 checkpoint 应否分别登记）

| 教师 | 部署实测 | 结论 |
|---|---|---|
| nnInteractive | 上游 HF 仓库 MIC-DKFZ/nnInteractive 实测（HF API siblings 全列 + 本地 tree manifest `models/.cache/huggingface/trees/3f308d75...json` 对照）：唯一 checkpoint `nnInteractive_v1.0/fold_0/checkpoint_final.pth`（411387150 字节，sha256 b3ac4421...，与注册表 weight_integrity 一致）；上游 models.json（blob 0f270b8c）与本地 models.json 均只列一个模型 | 无多 checkpoint，不拆分登记 |
| total_mr 族 | `~/.totalsegmentator/nnunet/results/` 只有 Dataset291-295+298（TS total 集权重）；无任何 _mr 权重 | 无多 checkpoint；条目 ready=False 与实况一致 |
| MOOSE | 未部署（无 checkpoint 目录） | 无 |
| nnUNet v2 | `~/data/BodyDonor/nnUNet_results/Dataset501_BodyDonorDistill/`（项目自训学生模型，非教师） | 框架条目不附带权重，维持不拆分；未来任务模型按 LICENSE-CHECK §七登记规则逐个登记 |
| SynthSeg | 未部署 | 无 |

## 三、依据不足请用户定的条目

无。审计未发现 modalities 与书面依据冲突的条目；nnunet_v2 的 CT 登记是本轮
之前已有的，审查指出其所引依据不覆盖 CT，已补 §七 出处（框架不产生蒸馏资格，
CT 登记只影响路由，风险为零）。

**不拓宽的（有据但不在数据域）**：nnInteractive 的显微/微CT（README 训练数据
清单内有，但 BodyDonor 数据域只有 CT/MR/PET，登记了也不会有对应输入序列）。

## 四、验收实测

- 失败测试先行：`test_dicom_pt_code_matches_pet_registered_modality` 在修复前
  复现 PT 拒绝（"task 'interactive_correction' not in teacher registry"——
  nninteractive 条目 PET 已登记但 PT 不匹配），修复后通过。
- `tests/test_teacher_registry.py`：25 collected / 25 passed（新增 4 测试：
  PT 别名、flexray 字面+别名断言、全条目口径+provenance、逐教师期望对照）。
- 受影响测试 6 文件：test_distill_license_gate 55 / test_export_seg 15 /
  test_license_whitelist 8 / test_m7_s3f_modality 15 / test_segmentation 11 /
  test_teacher_registry 25 = 109 passed, 0 failed。
- ruff：license_registry.py + test_teacher_registry.py 全绿。
- 全量套件：698 collected（R2 基线 694 + 本轮新增 4）/ 698 passed（进度点
  实数 698，无 F/E 字符），EXIT=0（`-q` 模式 summary 行未落日志，进度点
  与退出码为准；`tests/` 全量，`> /tmp/r3-full-suite.log 2>&1` 不走管道）。

## 五、审查 findings 清单（deep-reviewer，未实跑标注）

| # | severity | 内容 | 处置 |
|---|---|---|---|
| W1 | warning | nnunet_v2 CT 模态所引依据（§一 'MRI/PET'）不覆盖 CT | 已修：补 §七 出处（框架自适应流水线 + Dataset501 ct 实证），7d945b9 |
| W2 | warning | flexray 登记值 "X-RAY" 非 DICOM 代码，真实 DX/CR series 永拒 | 已修：MODALITY_ALIASES 补 DX/CR → X-RAY，7d945b9 |
| W3 | warning（PLAUSIBLE，既有） | export_seg.py:222 SOP Class 映射无 PT 键，PET 源 SEG 导出默认落 CT Image Storage（1.2.840.10008.5.1.4.1.1.2） | 本轮不动（既有缺口非本轮引入，导出层非注册表门控）；列未决 |
| N4 | nit | test_modality_aliases... 名字与内容不符（只断言字面值） | 已修：补 DX/CR/PT 三个映射显式断言 |
| N5 | nit | 审计测试 legal 集合是字面白名单，拦不住依据不足的拓宽 | 已修：docstring 改为字面口径层宣称，逐教师对照指向 test_r3 |
| N6 | nit | test_r3 by_task 字典重复 task 名会静默覆盖 | 已修：task 唯一性先行断言 |

审查另核实（正确部分）：别名归一化 pt/' PT ' 均映射成功；ready 过滤在别名后
正确；空串/全空白 fail closed；total 红线未动；7 个断言 task 名均唯一。

## 六、未决

1. **export_seg PT 源 SOP Class**（W3，既有）：PET 源 SEG 导出落 CT Image
   Storage SOP Class。导出层模态归一化应复用同一别名映射或补 PT 对应 SOP
   Class——⑤ 轮或后续处理。
2. **qc.py HU 规则对 PT**：PT 归一化后被当"modality unsupported"跳过 HU 规则，
   结果正确（MR 同样跳过），无实害，不改。
3. **nninteractive correct_image 未接注册表门控**（S3R4 W-7 既有）：ready=True
   故无实害；蒸馏防线在数据侧。
4. **v2 API**：nnInteractive 上游已出 API_CHANGES_v2.md（curl 404 未取到内容），
   未来升级时 checkpoint_id 可能变化，届时重新登记。
