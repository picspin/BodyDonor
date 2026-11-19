"""观察清单守卫测试（M7-CONSISTENCY 轮 C 项）。

断言 docs/observation-list.md 存在、含五条标题与门槛句。
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OBS_PATH = REPO / "docs" / "observation-list.md"

SECTION_TITLES = [
    "## 1. MaskVersion.source 列宽",
    "## 2. target_label 计数口径",
    "## 3. 门禁/评估 scope 口径",
    "## 4. 测试污染真实 config",
    "## 5. s0209 方向矩阵非严格正交",
]


def test_observation_list_exists() -> None:
    assert OBS_PATH.exists(), "docs/observation-list.md 未建"


def test_observation_list_five_sections() -> None:
    text = OBS_PATH.read_text(encoding="utf-8")
    for title in SECTION_TITLES:
        assert title in text, f"缺条目标题：{title}"


def test_observation_list_three_parts_per_item() -> None:
    """每条三段齐全：历史故障 → diff 触发条件 → 检查项。"""
    text = OBS_PATH.read_text(encoding="utf-8")
    for label in ("历史故障", "diff 触发条件", "检查项"):
        assert text.count(f"**{label}**") >= 5, f"三段 '{label}' 不足 5 条"


def test_observation_list_gate_sentence() -> None:
    """门槛句：两个 PR 出现过才固化进规则。"""
    text = OBS_PATH.read_text(encoding="utf-8")
    assert "两个 PR" in text
    assert "不进硬规则" in text or "不进规则" in text
