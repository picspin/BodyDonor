"""M5 审阅会话状态模型测试（review_session.py：M5-a/b/c/d + 传播 + 风险队列）。

数字全部来自本文件内合成的小数组 / M4 实测逐类 Dice（学生 hold-out
15 类：prostate 0.6099、duodenum 0.6357、liver 0.9433，
见 M4-REPORT §2；一致性 below_080：adrenal_gland_left/right、
duodenum、gallbladder、prostate，见 eval/student_teacher_consistency.json）。
"""

from __future__ import annotations

import numpy as np
import pytest

from bodydonor.review_session import (
    ADAPTIVE_RENDER_MODES,
    RENDER_SOURCES,
    DraftStore,
    InferenceScheduler,
    PromptSession,
    build_provenance,
    default_render_mode,
    local_roi,
    merge_prompts,
    propagate_correction,
    risk_queue,
    validate_prompt,
)


def _point(cls: str, x: int, y: int, z: int, include: bool = True) -> dict:
    return {"type": "point", "x": x, "y": y, "z": z, "include": include, "class": cls}


# --- M5-a：模式切换 ------------------------------------------------------------


class TestPromptSession:
    def test_switch_mode_keeps_prompts(self) -> None:
        s = PromptSession()
        s.set_mode("prompt_pos")
        s.add_prompt("liver", _point("liver", 10, 10, 20))
        s.add_prompt("liver", _point("liver", 12, 10, 20, include=False))
        s.set_mode("prompt_neg")
        s.set_mode("bbox")
        assert len(s.prompts_for("liver")) == 2
        assert s.all_prompts()[0]["x"] == 10

    def test_key_press_mapping(self) -> None:
        s = PromptSession()
        assert s.key_press("p") == "prompt_pos"
        assert s.key_press("n") == "prompt_neg"
        assert s.key_press("b") == "bbox"
        assert s.key_press("s") == "scribble"
        assert s.key_press("x") is None  # 未映射键不改模式
        assert s.mode == "scribble"

    def test_esc_returns_to_browse_without_clearing_prompts(self) -> None:
        s = PromptSession()
        s.set_mode("prompt_pos")
        s.add_prompt("liver", _point("liver", 10, 10, 20))
        assert s.key_press("escape") == "browse"
        assert s.mode == "browse"
        assert len(s.prompts_for("liver")) == 1  # Esc 不清提示

    def test_space_pan_suppresses_click(self) -> None:
        s = PromptSession()
        s.set_mode("prompt_pos")
        assert s.click_allowed() is True
        s.set_space_held(True)
        assert s.click_allowed() is False  # 空格拖拽不落提示
        s.set_space_held(False)
        assert s.click_allowed() is True

    def test_browse_mode_no_click(self) -> None:
        assert PromptSession().click_allowed() is False

    def test_invalid_prompt_rejected(self) -> None:
        s = PromptSession()
        with pytest.raises(ValueError, match="非法提示"):
            s.add_prompt("liver", {"type": "point", "x": 1, "y": 2})  # 缺 z/include
        ok, _ = validate_prompt(
            {"type": "point", "x": 1, "y": 2, "z": 3, "include": True, "class": "liver"}
        )
        assert ok is True


# --- M5-b：草稿生命周期 ---------------------------------------------------------


class TestDraftStore:
    def _session_with_liver_prompts(self) -> PromptSession:
        s = PromptSession()
        s.add_prompt("liver", _point("liver", 10, 10, 20))
        s.add_prompt("liver", _point("liver", 12, 10, 20, include=False))
        s.add_prompt("spleen", _point("spleen", 5, 5, 10))
        return s

    def test_accept_one_class_does_not_touch_other(self) -> None:
        d = DraftStore()
        d.update_draft("liver", 2)
        d.update_draft("spleen", 1)
        d.accept("liver")
        assert d.state_of("liver") == "accepted"
        assert d.state_of("spleen") == "draft"  # 单类确认不影响其他类

    def test_discard_returns_to_teacher(self) -> None:
        d = DraftStore()
        d.update_draft("liver", 2)
        prev = d.discard("liver")
        assert prev == "draft"
        assert d.state_of("liver") == "teacher"

    def test_accept_without_draft_rejected(self) -> None:
        with pytest.raises(ValueError, match="无草稿可确认"):
            DraftStore().accept("liver")

    def test_submit_payload_contains_all_prompts_and_accepted(self) -> None:
        s = self._session_with_liver_prompts()
        d = DraftStore()
        d.update_draft("liver", 2)
        d.update_draft("spleen", 1)
        d.accept("liver")  # 只确认肝脏；脾脏仍 draft，不进 acceptedClasses
        payload = d.submit_payload(s, "sess-1", 3)
        assert payload["sessionId"] == "sess-1"
        assert payload["baselineTeacherVersion"] == 3
        assert payload["acceptedClasses"] == ["liver"]
        assert len(payload["prompts"]) == 2  # 全量 prompts（liver 的 2 条）
        assert all(p["class"] == "liver" for p in payload["prompts"])

    def test_submit_without_accepted_rejected(self) -> None:
        s = PromptSession()
        d = DraftStore()
        d.update_draft("liver", 1)
        with pytest.raises(ValueError, match="无已确认的类"):
            d.submit_payload(s, "sess-1", None)

    def test_provenance_prompts_replayable(self) -> None:
        p = build_provenance("s", None, [_point("liver", 1, 2, 3)], ["liver"])
        assert p["baselineTeacherVersion"] is None
        assert p["prompts"][0]["x"] == 1
        with pytest.raises(ValueError, match="不可重放"):
            build_provenance("s", None, [{"type": "point", "x": 1}], ["liver"])


# --- M5-c：第三视觉态 -----------------------------------------------------------


class TestRenderSources:
    def test_four_sources_visually_distinct(self) -> None:
        assert set(RENDER_SOURCES) == {"teacher", "gt_human", "guided_draft", "guided_accepted"}
        styles = {(v["fill_alpha"], v["outline"]) for v in RENDER_SOURCES.values()}
        assert len(styles) == 4  # 4 种 source 两两可区分
        assert "未确认" in RENDER_SOURCES["guided_draft"]["badge"]
        assert "✓" in RENDER_SOURCES["guided_accepted"]["badge"]

    def test_adaptive_modes(self) -> None:
        assert set(ADAPTIVE_RENDER_MODES) == {"quick", "fine"}
        assert ADAPTIVE_RENDER_MODES["quick"]["outline"] == "weak"
        assert ADAPTIVE_RENDER_MODES["fine"]["deviation_pulse"] is True
        assert default_render_mode("CT") == "fine"
        assert default_render_mode("MR") == "fine"
        assert default_render_mode("XR") == "quick"
        assert default_render_mode("CT", task_kind="detection") == "quick"
        assert default_render_mode("XR", task_kind="boundary") == "fine"


# --- 修正传播 ------------------------------------------------------------------


class TestPropagateCorrection:
    def test_propagates_to_neighbours_background_only(self) -> None:
        arr = np.zeros((10, 10, 5), dtype=np.uint8)
        arr[4:6, 4:6, 2] = 1  # z=2 肝脏 2x2
        new, total, per = propagate_correction(arr, z=2, label=1, radius=1)
        assert total == 8  # 上下各 1 层 × 膨胀后 3x3 邻域内 4 体素…实测见下
        assert per == {1: 4, 3: 4}
        assert arr[4, 4, 2] == 1 and new[4, 4, 1] == 1  # 原数组不动

    def test_never_overwrites_other_classes(self) -> None:
        arr = np.zeros((10, 10, 5), dtype=np.uint8)
        arr[4:6, 4:6, 2] = 1
        arr[4:6, 4:6, 1] = 2  # 相邻层同位置已有其他类（修正投影全被占）
        arr[4, 4, 3] = 2  # 修正投影内 1 体素已被其他类占
        new, total, per = propagate_correction(arr, z=2, label=1, radius=1)
        assert new[4, 4, 1] == 2  # 不覆盖
        assert new[4, 4, 3] == 2  # 不覆盖
        assert 1 not in per  # z=1 投影全被占，0 传播
        assert per[3] == 3  # 只写背景体素（4 投影体素 - 1 被占）
        assert total == 3

    def test_radius_zero_noop_and_edge_slice(self) -> None:
        arr = np.zeros((6, 6, 3), dtype=np.uint8)
        arr[2:4, 2:4, 0] = 1
        new, total, per = propagate_correction(arr, z=0, label=1, radius=0)
        assert total == 0 and per == {} and (new == arr).all()
        new, total, per = propagate_correction(arr, z=0, label=1, radius=2)
        assert per == {1: 4, 2: 4} and total == 8  # 边缘层只向内传播

    def test_explicit_corrected_mask(self) -> None:
        arr = np.zeros((10, 10, 5), dtype=np.uint8)
        arr[4:6, 4:6, 2] = 1
        corrected = np.zeros((10, 10), dtype=bool)
        corrected[4, 4] = True  # 只修正 1 体素
        new, total, per = propagate_correction(arr, z=2, label=1, radius=1, corrected=corrected)
        assert per == {1: 1, 3: 1} and total == 2  # 膨胀邻域内仅 (4,4) 本身是背景

    def test_invalid_inputs(self) -> None:
        arr = np.zeros((5, 5, 3), dtype=np.uint8)
        with pytest.raises(ValueError, match="越界"):
            propagate_correction(arr, z=3, label=1)
        with pytest.raises(ValueError, match="label"):
            propagate_correction(arr, z=1, label=0)


# --- M5-d：推理调度 --------------------------------------------------------------


class TestInferenceScheduler:
    def test_stale_result_dropped(self) -> None:
        sch = InferenceScheduler()
        v1 = sch.submit()
        v2 = sch.submit()  # 快速连点：v1 已过时
        ok, _ = sch.complete(v1, "old")
        assert ok is False  # 旧 version 结果丢弃
        ok, res = sch.complete(v2, "new")
        assert (ok, res) == (True, "new")

    def test_cancel_drops_result(self) -> None:
        sch = InferenceScheduler()
        v = sch.submit()
        sch.cancel(v)
        assert sch.complete(v, "x") == (False, None)

    def test_merge_prompts_dedupes_near_duplicates(self) -> None:
        prompts = [
            _point("liver", 10, 10, 20),
            _point("liver", 11, 10, 20),  # ±1 体素内重复
            _point("liver", 10, 10, 20, include=False),  # 负向不同，保留
            _point("spleen", 10, 10, 20),  # 不同类，保留
            {"type": "bbox", "bbox": [[0, 5], [0, 5], [0, 5]], "class": "liver"},
        ]
        merged = merge_prompts(prompts)
        assert len(merged) == 4

    def test_local_roi_clipped(self) -> None:
        roi = local_roi(_point("liver", 2, 2, 2), shape=(10, 10, 10), margin=8)
        assert roi == (0, 10, 0, 10, 0, 10)  # 裁剪到边界
        roi = local_roi(_point("liver", 50, 50, 50), shape=(100, 100, 100), margin=8)
        assert roi == (42, 59, 42, 59, 42, 59)


# --- 异常驱动队列 -----------------------------------------------------------------


class TestRiskQueue:
    # M4 实测学生 hold-out 15 类逐类 Dice（M4-REPORT §2）
    DICE = {
        "liver": 0.9433,
        "spleen": 0.9318,
        "kidney_right": 0.9262,
        "kidney_left": 0.9172,
        "aorta": 0.9121,
        "stomach": 0.8817,
        "urinary_bladder": 0.8219,
        "inferior_vena_cava": 0.8048,
        "esophagus": 0.7795,
        "pancreas": 0.7460,
        "gallbladder": 0.7315,
        "adrenal_gland_right": 0.7068,
        "adrenal_gland_left": 0.6724,
        "duodenum": 0.6357,
        "prostate": 0.6099,
    }
    # 阶段 5 多教师一致性 below_080 类
    FLAGGED = ["adrenal_gland_left", "adrenal_gland_right", "duodenum", "gallbladder", "prostate"]

    def test_prostate_first_and_liver_last(self) -> None:
        q = risk_queue(self.DICE, self.FLAGGED)
        assert q[0]["class"] == "prostate"  # Dice 最低 + flagged
        assert q[0]["risk"] == "high"
        assert q[-1]["class"] == "liver"  # Dice 最高
        assert q[-1]["risk"] == "low"
        assert [e["class"] for e in q[:5]] == [
            "prostate",
            "duodenum",
            "adrenal_gland_left",
            "adrenal_gland_right",
            "gallbladder",
        ]  # 5 个 flagged high 在前，按 Dice 升序
        # （0.6099 < 0.6357 < 0.6724 < 0.7068 < 0.7315）

    def test_flagged_outranks_low_dice_without_flag(self) -> None:
        q = risk_queue({"a": 0.50, "b": 0.75}, ["b"])
        assert q[0]["class"] == "b"  # flagged 优先于单纯低 Dice
        assert q[0]["reason"].startswith("多教师一致性 flag")

    def test_risk_bands(self) -> None:
        q = {e["class"]: e["risk"] for e in risk_queue(self.DICE, self.FLAGGED)}
        assert q["esophagus"] == "high"  # 0.7795 < 0.80（无 flag 也 high）
        assert q["urinary_bladder"] == "medium"  # 0.8219
        assert q["aorta"] == "low"  # 0.9121
