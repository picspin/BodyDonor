"""许可注册表：从 config 读白名单，输出 status 报告（CLI 与 API 共用）。

许可事实来源：LICENSES-THIRD-PARTY.md（M0 联网核查结论）。
"""

from __future__ import annotations

from .config import Settings

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

    def check_task(self, task: str) -> tuple[bool, str]:
        """任务白名单判定（worker 分发层与 CI 负测试共用）。"""
        if task in self.settings.teacher.blocked_tasks:
            return False, f"task '{task}' is BLOCKED (license: see LICENSES-THIRD-PARTY.md)"
        if task in self.settings.teacher.allowed_tasks:
            return True, "allowed"
        return (
            False,
            f"task '{task}' not in teacher.allowed_tasks whitelist "
            f"(licensed tasks need explicit approval, see LICENSES-THIRD-PARTY.md)",
        )
