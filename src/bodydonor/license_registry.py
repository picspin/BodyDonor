"""许可注册表：从 config 读白名单，输出 status 报告（CLI 与 API 共用）。

许可事实来源：LICENSES-THIRD-PARTY.md（M0 联网核查结论）。
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
    "research_use",
    "known_restrictions",
    "provenance",
    "ready",
    "weight_integrity",
)

# 首批条目（M7-LICENSE-CHECK §二/四/八 + M7-PLAN v3.2 §二；provenance 即核查依据）
TEACHER_REGISTRY: list[dict] = [
    # TotalSegmentator 公开任务（TS CT 主力 + MRI _mr 族；Apache-2.0，§四）
    {
        "teacher_id": "totalsegmentator",
        "task": "total",
        "checkpoint_id": "unpinned (venv totalsegmentator==2.10.0)",
        "modalities": ("CT",),
        "output_type": "mask",
        "research_use": "allowed",
        "known_restrictions": [],
        "provenance": {
            "source": "https://github.com/wasserth/TotalSegmentator/ (LICENSE + README Subtasks)",
            "version": "2.10.0",
            "verified": "M0/M4 结论 + M7-LICENSE-CHECK §四（total 与 total_mr 同列 "
            "'Openly available for any usage (Apache-2.0)' 清单）",
        },
        "ready": True,
        "weight_integrity": {"sha256": None, "size_bytes": None, "status": "not_recorded"},
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
        "research_use": "allowed",
        "known_restrictions": [],
        "provenance": {
            "source": "https://github.com/wasserth/TotalSegmentator/ (README Subtasks 清单)",
            "version": "2.10.0",
            "verified": "M7-LICENSE-CHECK §四（公开 _mr 任务同在 Apache-2.0 清单内）",
        },
        "ready": False,
        "weight_integrity": {"sha256": None, "size_bytes": None, "status": "not_recorded"},
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
        "research_use": "allowed",
        "known_restrictions": ["Duke-110K 训练数据不含脑 MRI（脑部不在覆盖内）"],
        "provenance": {
            "source": "https://github.com/mazurowski-lab/mri_foundation (README License 节)",
            "version": "vitb",
            "verified": "M7-LICENSE-CHECK §二：code and models 均 Apache-2.0（README 声明，"
            "仓库无 LICENSE 文件已注记；不写 CC BY 4.0——那是论文 arXiv 许可）",
        },
        "ready": False,
        "weight_integrity": {"sha256": None, "size_bytes": None, "status": "not_recorded"},
        "feature_schema": {"embed_dim": 768, "patch": 16, "space": "image"},
    },
    # nnInteractive：交互式提示分割教师（LICENSE-CHECK §八；两处证据链接）
    {
        "teacher_id": "nninteractive",
        "task": "interactive_correction",
        "checkpoint_id": "nnInteractive_v1.0",
        "modalities": ("CT", "MR", "PET"),
        "output_type": "mask",
        "research_use": "restricted",
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
        "weight_integrity": {"sha256": None, "size_bytes": None, "status": "not_recorded"},
        "class_map_ref": "prompt-targets/v1 (目标由交互提示决定，无固定类映射)",
        "data_contract_version": "v1",
    },
    # SynthSeg / MOOSE：QC/研究路由，蒸馏须先走法务，不写成可蒸馏（§五/§六）
    {
        "teacher_id": "synthseg",
        "task": "brain_mri_qc",
        "checkpoint_id": "FreeSurfer 分发 (未下载)",
        "modalities": ("MR",),
        "output_type": "mask",
        "research_use": "restricted",
        "known_restrictions": [
            "QC-only：权重适用 FreeSurfer Software License Agreement v1.0，"
            "蒸馏阻断待法务复核；不得登记为可蒸馏"
        ],
        "provenance": {
            "source": "https://github.com/BBillot/SynthSeg; "
            "https://freesurfer.net/fswiki/FreeSurferSoftwareLicense",
            "version": "synthseg",
            "verified": "M7-LICENSE-CHECK §六",
        },
        "ready": False,
        "weight_integrity": {"sha256": None, "size_bytes": None, "status": "not_recorded"},
        "class_map_ref": "synthseg-classes/v1 (待建)",
        "data_contract_version": "v1",
    },
    {
        "teacher_id": "moose",
        "task": "moose_qc",
        "checkpoint_id": "未下载",
        "modalities": ("CT", "PET"),
        "output_type": "mask",
        "research_use": "restricted",
        "known_restrictions": [
            "QC-only：训练数据含 CC BY-NC 4.0 部分，蒸馏阻断待法务复核；不得登记为可蒸馏"
        ],
        "provenance": {
            "source": "https://github.com/ENHANCE-PET/MOOSE (DATA_CARD.md §6)",
            "version": "moose",
            "verified": "M7-LICENSE-CHECK §五：代码 Apache-2.0，权重 CC BY 4.0（项目自述）",
        },
        "ready": False,
        "weight_integrity": {"sha256": None, "size_bytes": None, "status": "not_recorded"},
        "class_map_ref": "moose-classes/v1 (待建)",
        "data_contract_version": "v1",
    },
    # nnUNet v2：框架条目，不附带权重，mask 任务逐任务登记
    {
        "teacher_id": "nnunet_v2",
        "task": "framework",
        "checkpoint_id": None,
        "modalities": ("CT", "MR"),
        "output_type": "framework",
        "research_use": "allowed",
        "known_restrictions": ["框架条目：不附带权重；具体 mask 任务须逐任务登记"],
        "provenance": {
            "source": "https://github.com/MIC-DKFZ/nnUNet (LICENSE)",
            "version": "v2",
            "verified": "M0 结论 + M7-LICENSE-CHECK §一",
        },
        "ready": False,
        "weight_integrity": {"sha256": None, "size_bytes": None, "status": "not_recorded"},
    },
    # FleXray：X-ray 模态约束（非许可问题；权重 CC BY-NC 4.0，§三）
    {
        "teacher_id": "flexray",
        "task": "flexray_xray",
        "checkpoint_id": "未下载",
        "modalities": ("X-ray",),
        "output_type": "mask",
        "research_use": "restricted",
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
        "class_map_ref": "flexray-classes/v1 (待建)",
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
    if entry["research_use"] not in ("allowed", "restricted", "unverified"):
        raise ValueError(f"research_use 非法：{entry['research_use']!r}")
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
    "totalsegmentator.brain_aneurysm": {
        "license": "CC BY-NC 4.0",
        "usage": "BLOCKED — no commercial license available",
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
            "commercial_distill_tasks": t.commercial_distill_tasks,
            "blocked_tasks": t.blocked_tasks,
            "nninteractive": self.settings.nninteractive.model_dump(),
            "experimental_text_prompt": self.settings.experimental_text_prompt.model_dump(),
            "license_facts": LICENSE_FACTS,
            "reference_doc": "LICENSES-THIRD-PARTY.md",
        }

    def check_task(self, task: str, modality: str | None = None) -> tuple[bool, str]:
        """教师路由判定（worker 分发层与 CI 负测试共用）。

        数据来源改读注册表（M7-S3 做法 5 号项，接口不变）：
        - settings.teacher.blocked_tasks 仍一票否决（blocked 是许可硬约束）。
        - 注册表有条目且 ready 且模态匹配 → 放行。
        - 注册表有条目但模态不符 / 未就绪 → 拒绝并带原因（回退语义 §2.2.3）。
        - 注册表无条目且不在 allowed_tasks 白名单 → 拒绝（白名单只记录已知条款，
          不作准入天花板 §2.3——新教师按条目登记后即可路由）。
        """
        if task in self.settings.teacher.blocked_tasks:
            return False, f"task '{task}' is BLOCKED (license: see LICENSES-THIRD-PARTY.md)"
        entries = [e for e in TEACHER_REGISTRY if e["task"] == task]
        if entries:
            if modality is not None:
                ok = [e for e in entries if modality in e["modalities"]]
                if not ok:
                    return (
                        False,
                        f"task '{task}' modality '{modality}' not supported by registry "
                        f"(registered modalities: "
                        f"{sorted({m for e in entries for m in e['modalities']})})",
                    )
            ready = [e for e in entries if e["ready"]]
            if not ready:
                return (
                    False,
                    f"task '{task}' registered but not ready "
                    f"(checkpoint 未部署；见注册表 ready=False)",
                )
            return True, "allowed"
        if task in self.settings.teacher.allowed_tasks:
            return True, "allowed"
        return (
            False,
            f"task '{task}' not in teacher registry and not in teacher.allowed_tasks "
            f"whitelist (register as a registry entry, see LICENSES-THIRD-PARTY.md)",
        )
