"""许可注册表：从 config 读白名单，输出 status 报告（CLI 与 API 共用）。

许可事实来源：LICENSES-THIRD-PARTY.md（M0 联网核查结论）。

许可两轴 + 准入（M7-S3P2 ⑤，S3R4 M-2 统一替代）：
- ``attribution_required`` / ``share_alike``：许可义务两轴（按 LICENSES-THIRD-PARTY.md）。
- ``license_verified``：证据强度（双证据 true / 单一来源或出入 false）。
- 准入判定读 ontology.mask_allowed_in_distill_training（两轴不能单独编码准入：
  5 个 unverified 与 allowed 同为 Apache-2.0，差别只在证据强度——须 license_verified
  显式承载；判定层新方案见该函数 docstring。决策表 tests/
  test_research_field_decision_table.py 断言迁移前后判定逐条一致）。

法务事实（2026-11-18 用户确认）：research 只化解 NC 条款，不化解 ShareAlike；
nnInteractive（CC BY-NC-SA 4.0）与 SynthSeg 蒸馏出的学生权重是衍生作品，须以
BY-NC-SA 分发；软件内不另建合规门禁，依据只留文档。
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import Settings

# ---------------------------------------------------------------------------
# 教师注册表（M7-S3 做法 5 号项；字段集 M7-PLAN v3 §2.2.1 一字不差）
# ---------------------------------------------------------------------------

_REGISTRY_FIELDS = (
    "teacher_id",
    "task",
    "checkpoint_id",
    "modalities",
    "output_type",
    "known_restrictions",
    "provenance",
    "ready",
    "weight_integrity",
    "attribution_required",
    "share_alike",
    "license_verified",
    "restriction_basis",
    "output_license_inherits",
)

# 许可两轴取值（M7-S3P2 ⑤）：attribution_required 恒 True（本仓无公有领域来源），
# share_alike 只有 nnInteractive（CC BY-NC-SA 4.0）为 True。
ATTRIBUTION_REQUIRED_VALUES = (True,)
SHARE_ALIKE_VALUES = (True, False)

# 模态别名（M7-S3P R3 ④）：注册表登记 'PET'/'X-RAY'，DICOM 头内 Modality 代码
# 分别是 'PT'（PS3.3 C.7.3.1.1 定义词）与投影 X 光 'DX'/'CR'——ingest 层原样
# 记录不翻译，归一化时并成登记模态。X 光代码口径与 review_session 同。
MODALITY_ALIASES: dict[str, str] = {"PT": "PET", "DX": "X-RAY", "CR": "X-RAY"}

# 首批条目（M7-LICENSE-CHECK §二/四/八 + M7-PLAN v3.2 §二；provenance 即核查依据）
TEACHER_REGISTRY: list[dict] = [
    # TotalSegmentator 公开任务（TS CT 主力 + MRI _mr 族；Apache-2.0，§四）
    {
        "teacher_id": "totalsegmentator",
        "task": "total",
        "checkpoint_id": "unpinned (venv totalsegmentator==2.10.0)",
        "modalities": ("CT",),
        "output_type": "mask",
        "known_restrictions": [],
        "provenance": {
            "source": "https://github.com/wasserth/TotalSegmentator/ (LICENSE + README Subtasks)",
            "version": "2.10.0",
            "verified": "M0/M4 结论 + M7-LICENSE-CHECK §四（total 与 total_mr 同列 "
            "'Openly available for any usage (Apache-2.0)' 清单）",
        },
        "ready": True,
        "weight_integrity": {"sha256": None, "size_bytes": None, "status": "not_recorded"},
        "attribution_required": True,
        "share_alike": False,
        "license_verified": True,
        "restriction_basis": "许可明文：Apache-2.0（LICENSE-CHECK §四，total 在 "
        "'Openly available for any usage (Apache-2.0)' 清单内）。两轴：署名义务，"
        "无 SA（research 不化解署名）",
        "output_license_inherits": [],
        "class_map_ref": "ontology/v1/map.yaml",
        "data_contract_version": "v1",
    },
] + [
    {
        "teacher_id": "totalsegmentator",
        "task": t,
        "checkpoint_id": "unpinned (权重未下载)",
        "modalities": ("MR",),
        "output_type": "mask",
        "known_restrictions": [],
        "provenance": {
            "source": "https://github.com/wasserth/TotalSegmentator/ (README Subtasks 清单)",
            "version": "2.10.0",
            "verified": "M7-LICENSE-CHECK §四（公开 _mr 任务同在 Apache-2.0 清单内）",
        },
        "ready": False,
        "weight_integrity": {"sha256": None, "size_bytes": None, "status": "not_recorded"},
        "attribution_required": True,
        "share_alike": False,
        "license_verified": True,
        "restriction_basis": "许可明文：Apache-2.0（LICENSE-CHECK §四，公开 _mr 任务 "
        "同在 Apache-2.0 清单内）。两轴：署名义务，无 SA",
        "output_license_inherits": [],
        "class_map_ref": "ts-mr-classes/v1 (TS MR 50 类，待落地为 ontology 条目)",
        "data_contract_version": "v1",
    }
    for t in (
        "total_mr",
        "body_mr",
        "vertebrae_mr",
        "liver_segments_mr",
        "liver_lesions_mr",
    )
] + [
    # MRI-CORE ViT-B：特征编码器，不是开箱分割教师（LICENSE-CHECK §二）
    {
        "teacher_id": "mri_core",
        "task": "feature_encoding",
        "checkpoint_id": "MRI_CORE_vitb.pth (~360MB, 未下载)",
        "modalities": ("MR",),
        "output_type": "feature",
        "known_restrictions": ["Duke-110K 训练数据不含脑 MRI（脑部不在覆盖内）"],
        "provenance": {
            "source": "https://github.com/mazurowski-lab/mri_foundation (README License 节)",
            "version": "vitb",
            "verified": "M7-LICENSE-CHECK §二：code and models 均 Apache-2.0（README 声明，"
            "仓库无 LICENSE 文件已注记；不写 CC BY 4.0——那是论文 arXiv 许可）",
        },
        "ready": False,
        "weight_integrity": {"sha256": None, "size_bytes": None, "status": "not_recorded"},
        "attribution_required": True,
        "share_alike": False,
        "license_verified": True,
        "restriction_basis": "许可明文：Apache-2.0（LICENSE-CHECK §二，README 'code and "
        "models' 声明；仓库无 LICENSE 文件已注记）。两轴：署名义务，无 SA",
        "output_license_inherits": [],
        "feature_schema": {"embed_dim": 768, "patch": 16, "space": "image"},
    },
    # nnInteractive：交互式提示分割教师（LICENSE-CHECK §八；两处证据链接）。
    # R3 ④：modalities CT/MR/PET 有据——README §Abstract 原文 "Trained on 120+
    # diverse volumetric 3D datasets (CT, MRI, PET, 3D Microscopy, etc.)"
    # （master readme.md 2026-11-16 实取）；显微/微CT 不在数据域内不登记。
    # R3 ④ 特别核查：上游 HF 仓库 MIC-DKFZ/nnInteractive 实测只有
    # nnInteractive_v1.0/fold_0/checkpoint_final.pth 一个 checkpoint（ siblings
    # 全列 + 本地 tree manifest 对照），本地部署同款，models.json 上游与本地均
    # 只列一个模型——无多 checkpoint，不拆分登记。
    {
        "teacher_id": "nninteractive",
        "task": "interactive_correction",
        "checkpoint_id": "nnInteractive_v1.0",
        "modalities": ("CT", "MR", "PET"),
        "output_type": "mask",
        "known_restrictions": [
            "权重 CC BY-NC-SA 4.0：仅交互式标注辅助，产物不进商用蒸馏训练集"
        ],
        "provenance": {
            "source": "https://github.com/MIC-DKFZ/nnInteractive (README License 节); "
            "https://huggingface.co/MIC-DKFZ/nnInteractive (模型卡 License 节)",
            "version": "v1.0",
            "verified": "M7-LICENSE-CHECK §八：两处官方声明逐字一致；运行时以 checkpoint "
            "文件夹 LICENSE 首行（nni_runner 暴露为 session.license）为准，"
            "注册表 provenance 引用该运行时值",
        },
        "ready": True,
        "weight_integrity": {
            "sha256": "b3ac4421f85457bbd1aa0d87f5e67bcb7bc8e2ce6b824b6ac45077cc5d630ea9",
            "size_bytes": 411387150,
            "status": "verified",
            "checkpoint_path": "~/data/BodyDonor/nninteractive-models/models/"
            "nnInteractive_v1.0/fold_0/checkpoint_final.pth",
            "license_runtime": "CC BY-NC-SA 4.0（checkpoint 文件夹 LICENSE 首行，"
            "nni_runner 暴露为 session.license）",
        },
        "class_map_ref": "prompt-targets/v1 (目标由交互提示决定，无固定类映射)",
        "data_contract_version": "v1",
        "attribution_required": True,
        "share_alike": True,
        "license_verified": True,
        "restriction_basis": "项目政策：修正产物不进蒸馏（M6 已记录边界）+ 许可明文 "
        "CC BY-NC-SA 4.0（LICENSE-CHECK §八：两轴 attribution+share_alike，"
        "派生物须以 BY-NC-SA 分发并传播署名、商用须法务）",
        "output_license_inherits": ["CC-BY-NC-SA 4.0（ShareAlike：派生物须同许可并传播署名）"],
    },
    # SynthSeg / MOOSE：QC/研究路由，两轴 conditional 语义（S3 收口轮 ④
    # 用户定方向：不因商业政策模糊自动禁止本地科研蒸馏；§五/§六）
    {
        "teacher_id": "synthseg",
        "task": "brain_mri_qc",
        "checkpoint_id": "FreeSurfer 分发 (未下载)",
        "modalities": ("MR",),
        "output_type": "mask",
        "known_restrictions": [
            "conditional 语义（两轴 + 继承条款）：权重适用 FreeSurfer Software License "
            "Agreement v1.0，本地科研蒸馏按 conditional 放行（派生物按 "
            "output_license_inherits 保留条款）；商用蒸馏须先走法务——未决项："
            "FreeSurfer 第 3 条第三方权利条款 + ADNI DUA（§六未解决项 1/2），"
            "法务澄清前不升级 allowed"
        ],
        "provenance": {
            "source": "https://github.com/BBillot/SynthSeg; "
            "https://freesurfer.net/fswiki/FreeSurferSoftwareLicense",
            "version": "synthseg",
            "verified": "M7-LICENSE-CHECK §六",
        },
        "ready": False,
        "weight_integrity": {"sha256": None, "size_bytes": None, "status": "not_recorded"},
        "attribution_required": True,
        "share_alike": False,
        "license_verified": True,
        "restriction_basis": "许可明文 + 法务未决：FreeSurfer Software License Agreement "
        "v1.0 明示允许并入专有程序、商业化风险自担（LICENSE-CHECK §六），不禁止本地"
        "科研蒸馏；保留在第 3 条第三方权利条款与 ADNI DUA 未决（§六未解决项 1/2），"
        "法务澄清后可升级 allowed。两轴：署名 + 修改标识（SLA §六 Part B 1(c)/(d)），"
        "无 SA（SLA 非 ShareAlike 许可——用户 2026-11-18 确认）",
        "output_license_inherits": ["FreeSurfer Software License Agreement v1.0 "
        "(attribution + 修改标识条款，§六 Part B 1(c)/(d))"],
        "class_map_ref": "synthseg-classes/v1 (待建)",
        "data_contract_version": "v1",
    },
    {
        "teacher_id": "moose",
        "task": "moose_qc",
        "checkpoint_id": "未下载",
        # R3 ④：CT/PET 有据——M7-LICENSE-CHECK §一 总表书面支持 'CT/PET'；
        # DICOM 头内 PET 代码 PT 由 MODALITY_ALIASES 匹配。
        "modalities": ("CT", "PET"),
        "output_type": "mask",
        "known_restrictions": [
            "conditional 语义（两轴 + 继承条款）：训练数据含 NC 部分（AutoPET 溯源未决），"
            "本地科研蒸馏按 conditional 放行（派生物按 output_license_inherits "
            "保留条款）；商用蒸馏须先走法务——未决项：NC 训练数据溯源 + "
            "'Commercial Version' 关系（§五未解决项 2/3），法务澄清前不升级 allowed"
        ],
        "provenance": {
            "source": "https://github.com/ENHANCE-PET/MOOSE (DATA_CARD.md §6)",
            "version": "moose",
            "verified": "M7-LICENSE-CHECK §五：代码 Apache-2.0，权重 CC BY 4.0（项目自述）",
        },
        "ready": False,
        "weight_integrity": {"sha256": None, "size_bytes": None, "status": "not_recorded"},
        "attribution_required": True,
        "share_alike": False,
        "license_verified": True,
        "restriction_basis": "许可明文 + 法务未决：权重 CC BY 4.0 本身允许商用（含衍生），"
        "不禁止本地科研蒸馏（LICENSE-CHECK §五）；保留在 NC 训练数据溯源（AutoPET）与"
        "'Commercial Version' 关系两项未决（§五未解决项 2/3），法务澄清后可升级 allowed。"
        "两轴：署名义务，无 SA（训练数据含 NC 部分是独立 caveat，不进 SA 轴）",
        "output_license_inherits": ["CC BY 4.0（署名条款，MODEL_LICENSE）"],
        "class_map_ref": "moose-classes/v1 (待建)",
        "data_contract_version": "v1",
    },
    # nnUNet v2：框架条目，不附带权重，mask 任务逐任务登记。
    # R3 ④：modalities 拓宽为 CT/MR/PET——MR/PET 依据 M7-LICENSE-CHECK §一 总表
    # 'MRI/PET'；CT 依据 §七 结论（框架自适应任意数据集流水线，Dataset501
    # channel_names['0']='ct' 实证 CT 流水线在用；框架本身不产生蒸馏资格，
    # 逐任务权重许可仍取决于训练数据）。
    {
        "teacher_id": "nnunet_v2",
        "task": "framework",
        "checkpoint_id": None,
        "modalities": ("CT", "MR", "PET"),
        "output_type": "framework",
        "known_restrictions": [
            "框架条目：不附带权重，不产生蒸馏资格（LICENSE-CHECK §七）；"
            "具体 mask 任务须逐任务登记"
        ],
        "provenance": {
            "source": "https://github.com/MIC-DKFZ/nnUNet (LICENSE)",
            "version": "v2",
            "verified": "M0 结论 + M7-LICENSE-CHECK §一",
        },
        "ready": False,
        "weight_integrity": {"sha256": None, "size_bytes": None, "status": "not_recorded"},
        "attribution_required": True,
        "share_alike": False,
        "license_verified": False,
        "restriction_basis": "许可明文：框架条目不附带权重，不产生蒸馏资格（LICENSE-CHECK "
        "§七）；具体任务模型须逐任务登记核实后才有资格。两轴对框架条目 N/A——按 "
        "Apache-2.0 填署名义务，license_verified=False（无权重可蒸馏，准入阻断）",
        "output_license_inherits": [],
    },
    # FleXray：X-ray 模态约束（非许可问题；权重 CC BY-NC 4.0，§三）。
    # R3 ④：登记 'X-RAY'——比对侧 strip().upper()，混合大小写 'X-ray' 永不相等。
    {
        "teacher_id": "flexray",
        "task": "flexray_xray",
        "checkpoint_id": "未下载",
        "modalities": ("X-RAY",),
        "output_type": "mask",
        "known_restrictions": [
            "模态约束：仅 X-ray；权重 CC BY-NC 4.0，合成数据集合集 CC BY-NC 4.0"
        ],
        "provenance": {
            "source": "https://huggingface.co/VictorButoi/flexray (模型卡 Licenses 节)",
            "version": "flexray",
            "verified": "M7-LICENSE-CHECK §三：代码 MIT，权重 CC BY-NC 4.0",
        },
        "ready": False,
        "weight_integrity": {"sha256": None, "size_bytes": None, "status": "not_recorded"},
        "attribution_required": True,
        "share_alike": False,
        "license_verified": True,
        "restriction_basis": "许可明文：权重 CC BY-NC 4.0 禁止商业目的使用，QC/研究路由"
        "（输出不进商用蒸馏训练）允许（LICENSE-CHECK §三资格判断理由）。两轴：署名"
        "义务，无 SA（CC BY-NC 4.0 不是 ShareAlike）",
        "output_license_inherits": ["CC-BY-NC 4.0（NC：派生物不得用于商业目的）"],
        "class_map_ref": "flexray-classes/v1 (待建)",
        "data_contract_version": "v1",
    },
] + [
    # TS 公开 subtasks 第二批（M7-S3F ②）：allowed_tasks 全量登记进注册表，
    # 路由只走注册表。本组：README open 清单 + TS 2.10.0 安装包双证据齐全。
    {
        "teacher_id": "totalsegmentator",
        "task": t,
        "checkpoint_id": "unpinned (权重未下载)",
        "modalities": ("CT",),
        "output_type": "mask",
        "known_restrictions": [],
        "provenance": {
            "source": "https://github.com/wasserth/TotalSegmentator/ (README Subtasks open 清单)",
            "version": "2.10.0",
            "verified": "M7-S3F ②：LICENSES-THIRD-PARTY.md §1.2 open 清单（访问 2026-11-06，"
            "Apache-2.0 权重）+ TS 2.10.0 安装包 map_to_binary.class_map 含该任务且 "
            "commercial_models 不含",
        },
        "ready": False,
        "weight_integrity": {"sha256": None, "size_bytes": None, "status": "not_recorded"},
        "attribution_required": True,
        "share_alike": False,
        "license_verified": True,
        "restriction_basis": "许可明文：Apache-2.0（M7-S3F ②，LICENSES-THIRD-PARTY.md "
        "§1.2 open 清单 + TS 2.10.0 安装包双证据）。两轴：署名义务，无 SA",
        "output_license_inherits": [],
        "class_map_ref": "ts-classes/v1 (TS 2.10.0 map_to_binary.class_map)",
        "data_contract_version": "v1",
    }
    for t in (
        "lung_vessels",
        "body",
        "cerebral_bleed",
        "hip_implant",
        "pleural_pericard_effusion",
        "head_glands_cavities",
        "head_muscles",
        "headneck_bones_vessels",
        "headneck_muscles",
        "liver_vessels",
        "oculomotor_muscles",
        "lung_nodules",
        "kidney_cysts",
        "breasts",
        "liver_segments",
        "craniofacial_structures",
        "abdominal_muscles",
        "ventricle_parts",
    )
] + [
    # 本组仅在 README open 清单（LICENSES-THIRD-PARTY.md §1.2），本地 TS 2.10.0
    # 安装包 class_map 不含（vertebrae_pp_refined 在 2.10.0 无此类）——许可证据
    # 单一来源，结构未核实。许可摘要：登记 unverified，ready=False（未核实），
    # 注册表 provenance 记明差异。
    {
        "teacher_id": "totalsegmentator",
        "task": t,
        "checkpoint_id": "unpinned (权重未下载；2.10.0 安装包 class_map 不含该任务)",
        "modalities": ("CT",),
        "output_type": "mask",
        "known_restrictions": [
            "仅 README open 清单（Apache-2.0 权重，访问 2026-11-06）；"
            "本地 TS 2.10.0 安装包 class_map 不含，结构输出未核实"
        ],
        "provenance": {
            "source": "https://github.com/wasserth/TotalSegmentator/ (README Subtasks open 清单)",
            "version": "2.10.0",
            "verified": "M7-S3F ②：LICENSES-THIRD-PARTY.md §1.2 open 清单；安装包未核实 "
            "（class_map 不含）——结构未核实",
        },
        "ready": False,
        "weight_integrity": {"sha256": None, "size_bytes": None, "status": "not_recorded"},
        "attribution_required": True,
        "share_alike": False,
        "license_verified": False,
        "restriction_basis": "许可证据单一来源：仅 README open 清单（LICENSES-THIRD-PARTY.md "
        "§1.2），安装包结构未核实——准入未核实待双证据（两轴暂按 Apache-2.0：署名义务，"
        "无 SA）",
        "output_license_inherits": [],
        "class_map_ref": "ts-classes/v1 (待落地：2.10.0 class_map 不含，需按 README 定义建)",
        "data_contract_version": "v1",
    }
    for t in (
        "teeth",
        "trunk_cavities",
        "liver_lesions",
        "vertebrae_pp",
        "vertebrae_pp_refined",
    )
] + [
    # vertebrae_body：TS 2.10.0 安装包 commercial_models 含（id 305，需 license，
    # 与 M0 核查时上游 README 有出入）——按安装包实况登记 unverified + 限制注明。
    # lung_vessels_LEGACY 不登记：README 标注 "..."（含义不明），本地 TS 2.10.0
    # 安装包 class_map 不含——不准猜，留白名单历史记录（settings.yaml），见报告。
    {
        "teacher_id": "totalsegmentator",
        "task": "vertebrae_body",
        "checkpoint_id": "unpinned (权重未下载)",
        "modalities": ("CT",),
        "output_type": "mask",
        "known_restrictions": [
            "TS 2.10.0 安装包 commercial_models 含该任务（id 305，需 license）："
            "非商用走 academic license 渠道；与 README open 清单出入已注记"
        ],
        "provenance": {
            "source": "TS 2.10.0 安装包 totalsegmentator/map_to_binary.py commercial_models",
            "version": "2.10.0",
            "verified": "M7-S3F ②：安装包实测 commercial_models['vertebrae_body']=305 "
            "（需 license）；README open 清单（访问 2026-11-06）称 open——按安装包实况 "
            "登记 unverified",
        },
        "ready": False,
        "weight_integrity": {"sha256": None, "size_bytes": None, "status": "not_recorded"},
        "attribution_required": True,
        "share_alike": False,
        "license_verified": False,
        "restriction_basis": "许可明文出入：安装包 commercial_models 含该任务（id 305，需 "
        "license）与 README open 清单有出入（M7-S3F ②）——按安装包实况准入未核实"
        "（两轴暂按 Apache-2.0：署名义务，无 SA；⑥ 轮再议 NC 框架下消解）",
        "output_license_inherits": [],
        "class_map_ref": "ts-classes/v1 (TS 2.10.0 map_to_binary.class_map)",
        "data_contract_version": "v1",
    },
]


def validate_entry(entry: dict) -> None:
    """注册表条目字段完整性（§2.2.1）：缺失/非法即拒绝登记。

    mask 教师必须带版本化 class_map_ref 与 data_contract_version；feature 教师
    必须带 feature_schema（不进 mask 投票）。
    """
    missing = [f for f in _REGISTRY_FIELDS if f not in entry]
    if missing:
        raise ValueError(f"注册表条目缺字段：{missing}")
    if entry["output_type"] not in ("mask", "feature", "framework"):
        raise ValueError(f"output_type 非法：{entry['output_type']!r}")
    if entry["attribution_required"] not in ATTRIBUTION_REQUIRED_VALUES:
        raise ValueError(
            f"attribution_required 非法：{entry['attribution_required']!r}（本仓无公有"
            "领域来源，恒 True）"
        )
    if entry["share_alike"] not in SHARE_ALIKE_VALUES:
        raise ValueError(f"share_alike 非法：{entry['share_alike']!r}")
    if not isinstance(entry["license_verified"], bool):
        raise ValueError(
            f"license_verified 非法：{entry['license_verified']!r}（bool：双证据 true / "
            "单一来源或出入 false）"
        )
    rb = entry["restriction_basis"]
    if not isinstance(rb, str) or not rb.strip():
        raise ValueError("restriction_basis 必填（限制依据：许可明文或项目政策，附出处）")
    if not isinstance(entry["output_license_inherits"], list):
        raise ValueError("output_license_inherits 必须是列表（无需保留条款则显式空列表）")
    # S3P2 ⑤（承接 S3R4 M-1）：share_alike 且 inherits 空列表 -> SA 条款被静默
    # 丢弃（派生物按 output_license_inherits 保留条款）——入口拒绝登记
    if entry["share_alike"] and not entry["output_license_inherits"]:
        raise ValueError(
            f"{entry['teacher_id']}/{entry['task']} share_alike=True 但 "
            "output_license_inherits 为空：SA 派生物须按保留条款登记"
            "（share_alike=False 且无需条款则显式空列表，不静默丢条款）"
        )
    if not isinstance(entry["known_restrictions"], list):
        raise ValueError("known_restrictions 必须是列表（无限制则显式空列表）")
    p = entry["provenance"]
    if not isinstance(p, dict) or not p.get("source") or not p.get("verified"):
        raise ValueError("provenance 必须含 source 与 verified（核查依据）")
    if entry["output_type"] == "mask" and not (
        entry.get("class_map_ref") and entry.get("data_contract_version")
    ):
        raise ValueError(
            f"mask 教师 {entry['teacher_id']}/{entry['task']} 缺 class_map_ref 或 "
            "data_contract_version（无法判断结构覆盖与跨教师比较）"
        )
    # W16：占位守卫匹配「待建」与「待落地」两种措辞——永远不能在类映射落地前 ready
    cls_ref = str(entry.get("class_map_ref") or "")
    if entry.get("ready") and ("待建" in cls_ref or "待落地" in cls_ref):
        raise ValueError(
            f"{entry['teacher_id']}/{entry['task']} ready=True 但 class_map_ref "
            f"是占位（{entry['class_map_ref']}）——先落地类映射再 ready"
        )
    if entry["output_type"] == "feature" and not entry.get("feature_schema"):
        raise ValueError(f"feature 教师 {entry['teacher_id']} 缺 feature_schema")


for _e in TEACHER_REGISTRY:
    validate_entry(_e)


@dataclass
class FallbackRecord:
    """回退语义（§2.2.3）：执行前记 planned、结束记 actual；跳过写原因。"""

    planned: dict | None = None
    actual: dict | None = None
    status: str = "planned"  # planned | executed | skipped | failed
    skip_reason: str | None = None


def classify_missing_output(
    requested_classes: list[str], returned_classes: list[str], teacher_coverage: list[str]
) -> str:
    """执行成功却没返回目标 mask：区分"结构未覆盖"与"输出缺失"（§2.2.3）。

    请求的结构都不在教师类覆盖内 → structure_not_covered（教师本来就不出这些结构，
    不计成 Dice/置信度 0）；覆盖内却没返回 → output_missing。
    """
    if not requested_classes:
        return "output_missing"
    if not any(c in teacher_coverage for c in requested_classes):
        return "structure_not_covered"
    if not returned_classes:
        return "output_missing"
    return "executed"

# 与 LICENSES-THIRD-PARTY.md 同步的许可事实（此处只放判定用摘要，原文见该文件）
LICENSE_FACTS: dict[str, dict[str, str]] = {
    "totalsegmentator.code": {
        "license": "Apache-2.0",
        "usage": "any",
        "source": "https://raw.githubusercontent.com/wasserth/TotalSegmentator/master/LICENSE",
    },
    "totalsegmentator.total": {
        "license": "Apache-2.0",
        "usage": "any (openly available for any usage)",
        "note": "商用蒸馏唯一安全教师 mask 来源",
    },
    "totalsegmentator.licensed_tasks": {
        "license": "custom (free for non-commercial, via backend.totalsegmentator.com"
        "/license-academic/)",
        "usage": "non-commercial only",
        "count": "18",
        "holder": "University Hospital Basel, jakob.wasserthal@usb.ch",
    },
    "nninteractive.code": {
        "license": "Apache-2.0",
        "usage": "any",
        "source": "https://github.com/MIC-DKFZ/nnInteractive",
    },
    "nninteractive.weights": {
        "license": "CC BY-NC-SA 4.0",
        "usage": "interactive correction only; outputs must not enter "
        "commercial distillation training",
    },
    "nnunet_v2": {
        "license": "Apache-2.0",
        "usage": "any",
        "source": "https://raw.githubusercontent.com/MIC-DKFZ/nnUNet/master/LICENSE",
    },
    "segvol.code": {
        "license": "MIT",
        "usage": "any",
        "source": "https://raw.githubusercontent.com/BAAI-DCAI/SegVol/main/LICENSE",
    },
    "segvol.baai_weights": {
        "license": "待核实 (no license tag on HF BAAI/SegVol)",
        "usage": "experimental channel only; verify before any commercial use",
    },
}


class LicenseRegistry:
    def __init__(self, settings: Settings):
        self.settings = settings

    def summary(self) -> dict[str, object]:
        t = self.settings.teacher
        return {
            "teacher_engine": t.engine,
            "allowed_tasks": t.allowed_tasks,
            "blocked_tasks": t.blocked_tasks,
            "nninteractive": self.settings.nninteractive.model_dump(),
            "experimental_text_prompt": self.settings.experimental_text_prompt.model_dump(),
            "license_facts": LICENSE_FACTS,
            "reference_doc": "LICENSES-THIRD-PARTY.md",
        }

    def check_task(self, task: str, modality: str | None = None) -> tuple[bool, str]:
        """教师路由判定（worker 分发层与 CI 负测试共用）。

        注册表唯一来源（M7-S3F ②：白名单回退退役）：
        - settings.teacher.blocked_tasks 仍一票否决（blocked 是许可硬约束）。
        - 注册表有条目且 ready 且模态匹配 → 放行。
        - 注册表有条目但模态不符 / 未就绪 → 拒绝并带原因（回退语义 §2.2.3）。
        - 注册表无条目 → 拒绝并带原因（新任务走注册表登记，不走白名单旁路）。
        """
        if task in self.settings.teacher.blocked_tasks:
            return False, f"task '{task}' is BLOCKED (license: see LICENSES-THIRD-PARTY.md)"
        entries = [e for e in TEACHER_REGISTRY if e["task"] == task]
        if entries:
            if modality is not None:
                # 归一化（W8）：与 qc.py 同口径（"mr"/" MR" 按 MR 处理）；
                # R3 ④：别名 PT → PET（DICOM 代码匹配注册表登记值）
                m = MODALITY_ALIASES.get(modality.strip().upper(), modality.strip().upper())
                ok = [e for e in entries if m in e["modalities"]]
                if not ok:
                    return (
                        False,
                        f"task '{task}' modality '{modality}' not supported by registry "
                        f"(registered modalities: "
                        f"{sorted({mm for e in entries for mm in e['modalities']})})",
                    )
            ready = [e for e in ok if e["ready"]] if modality is not None else [
                e for e in entries if e["ready"]
            ]
            if not ready:
                return (
                    False,
                    f"task '{task}' registered but not ready "
                    f"(checkpoint 未部署；见注册表 ready=False)",
                )
            return True, "allowed"
        return (
            False,
            f"task '{task}' not in teacher registry "
            "(register as a registry entry, see registry docs)",
        )
