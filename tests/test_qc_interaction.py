"""M6-b：过程级 QC 规则单测（两条规则全分支 / 两层逻辑 / skipped / 阈值边界 / config 加载）。

合成边界 case（任务书 8）：全部条目为手工构造的合成 interactionMetrics，
只用于分支覆盖，不冒充真实会话；真实全路径回放见 test_qc_interaction_e2e.py。
"""

from __future__ import annotations

import numpy as np
import pytest

from bodydonor.qc import (
    RULE_INTERACTION_ABANDONMENT,
    RULE_INTERACTION_CONVERGENCE,
    load_qc_thresholds,
    run_qc,
)
from bodydonor.qc_interaction import (
    is_high_risk,
    rule_interaction_abandonment,
    rule_interaction_convergence,
)


def _entry(
    num_prompts: int = 1,
    convergence_steps: int | None = None,
    final_local_dice: float | None = 0.9,
    reliability: str = "high",
    abandoned: int = 0,
) -> dict:
    """合成 interactionMetrics 条目（M6-a JSON 结构）。"""
    return {
        "numPrompts": num_prompts,
        "convergenceSteps": convergence_steps,
        "perStepDeltaDice": [],
        "finalLocalDice": final_local_dice,
        "teacherReliability": reliability,
        "abandonedAttempts": abandoned,
    }


def _cfg(**kw) -> dict:
    return {"enabled": True, "source": "cold_start", "calibrated_on": 0, **kw}


# ---------------------------------------------------------------------------
# 规则本体：interaction_convergence（普通类/高风险类 × numPrompts ×
# convergenceSteps × abandonedAttempts 上下界）
# ---------------------------------------------------------------------------


class TestRuleInteractionConvergence:
    def test_normal_class_within_bounds_not_flagged(self) -> None:
        """普通类：numPrompts/k_max 全在上界内 → 不 flag。"""
        cm = {"liver": _entry(num_prompts=15, convergence_steps=6)}
        flagged, score, detail = rule_interaction_convergence(cm, _cfg())
        assert not flagged
        assert score == 1.0
        assert detail["flagged_classes"] == []

    def test_normal_class_num_prompts_over_n_max_flags(self) -> None:
        """普通类：numPrompts > 15 → flag（convergenceSteps 良好也 flag）。"""
        cm = {"liver": _entry(num_prompts=16, convergence_steps=1)}
        flagged, _score, detail = rule_interaction_convergence(cm, _cfg())
        assert flagged
        assert detail["flagged_classes"] == ["liver"]

    def test_normal_class_convergence_over_k_max_flags(self) -> None:
        """普通类：convergenceSteps > 6 → flag（numPrompts 良好也 flag）。"""
        cm = {"liver": _entry(num_prompts=1, convergence_steps=7)}
        flagged, _score, detail = rule_interaction_convergence(cm, _cfg())
        assert flagged
        assert detail["flagged_classes"] == ["liver"]

    def test_boundary_exact_n_max_not_flagged(self) -> None:
        """阈值边界：恰 n_max（15）/ k_max（6）不 flag（> 才 flag）。"""
        cm = {"liver": _entry(num_prompts=15, convergence_steps=6)}
        flagged, _score, _detail = rule_interaction_convergence(cm, _cfg())
        assert not flagged

    def test_boundary_one_over_flags(self) -> None:
        """阈值边界：16 / 7 flag（上界 +1）。"""
        cm = {"liver": _entry(num_prompts=16, convergence_steps=7)}
        flagged, _score, _detail = rule_interaction_convergence(cm, _cfg())
        assert flagged

    def test_high_risk_ignores_convergence_steps(self) -> None:
        """高风险类：convergenceSteps > k_max 不 flag（不看该维度）。"""
        cm = {"prostate": _entry(num_prompts=1, convergence_steps=99, reliability="low")}
        flagged, _score, detail = rule_interaction_convergence(cm, _cfg())
        assert not flagged
        assert detail["flagged_classes"] == []

    def test_high_risk_num_prompts_over_flags(self) -> None:
        """高风险类：numPrompts > n_max → flag。"""
        cm = {"prostate": _entry(num_prompts=16, reliability="low")}
        flagged, _score, _detail = rule_interaction_convergence(cm, _cfg())
        assert flagged

    def test_high_risk_abandoned_flags(self) -> None:
        """高风险类：abandonedAttempts >= 1 → flag（即使 numPrompts 少）。"""
        cm = {"prostate": _entry(num_prompts=1, reliability="low", abandoned=1)}
        flagged, _score, detail = rule_interaction_convergence(cm, _cfg())
        assert flagged
        assert detail["flagged_classes"] == ["prostate"]

    def test_high_risk_boundary_zero_abandoned_not_flagged(self) -> None:
        """高风险类：abandonedAttempts=0、numPrompts 在界内 → 不 flag。"""
        cm = {"prostate": _entry(num_prompts=15, reliability="low", abandoned=0)}
        flagged, _score, _detail = rule_interaction_convergence(cm, _cfg())
        assert not flagged

    def test_final_local_dice_not_a_criterion(self) -> None:
        """finalLocalDice 不作判据：Dice 差（0.3）不 flag；Dice 好不救 flag。"""
        bad_dice_ok = {"liver": _entry(num_prompts=1, convergence_steps=1, final_local_dice=0.3)}
        assert not rule_interaction_convergence(bad_dice_ok, _cfg())[0]
        good_dice_flagged = {"liver": _entry(num_prompts=20, final_local_dice=0.98)}
        assert rule_interaction_convergence(good_dice_flagged, _cfg())[0]

    def test_mixed_classes_flag_only_bad(self) -> None:
        """多类混合：只 flag 越界类，正常类不受牵连。"""
        cm = {
            "liver": _entry(num_prompts=2, convergence_steps=1),
            "prostate": _entry(num_prompts=1, reliability="low", abandoned=1),
            "kidney_left": _entry(num_prompts=30),
        }
        flagged, _score, detail = rule_interaction_convergence(cm, _cfg())
        assert flagged
        assert sorted(detail["flagged_classes"]) == ["kidney_left", "prostate"]

    def test_old_data_missing_reliability_falls_back_to_normal(self) -> None:
        """旧数据无 teacherReliability：按普通类判（不猜风险）。"""
        entry = _entry(num_prompts=1, convergence_steps=99)
        entry.pop("teacherReliability")
        flagged, _score, _detail = rule_interaction_convergence({"liver": entry}, _cfg())
        assert flagged  # 普通类：convergenceSteps 越界 flag

    def test_non_dict_entries_ignored(self) -> None:
        """结构异常条目（非 dict）跳过，不崩。"""
        flagged, _score, detail = rule_interaction_convergence(
            {"liver": "garbage", "prostate": _entry(num_prompts=20)}, _cfg()
        )
        assert flagged
        assert detail["flagged_classes"] == ["prostate"]


# ---------------------------------------------------------------------------
# 规则本体：interaction_abandonment
# ---------------------------------------------------------------------------


class TestRuleInteractionAbandonment:
    def test_zero_abandoned_not_flagged(self) -> None:
        cm = {"liver": _entry(abandoned=0)}
        flagged, score, _detail = rule_interaction_abandonment(cm, _cfg())
        assert not flagged
        assert score == 1.0

    def test_one_abandoned_flags(self) -> None:
        cm = {"liver": _entry(abandoned=1)}
        flagged, score, detail = rule_interaction_abandonment(cm, _cfg())
        assert flagged
        assert score == 0.0
        assert detail["flagged_classes"] == ["liver"]
        assert detail["worst_abandoned_attempts"] == 1

    def test_normal_class_abandoned_flags(self) -> None:
        """普通类放弃同样 flag（放弃事件本身是过程信号，两层同判据）。"""
        cm = {"kidney_left": _entry(num_prompts=1, abandoned=2)}
        flagged, _score, detail = rule_interaction_abandonment(cm, _cfg())
        assert flagged
        assert detail["flagged_classes"] == ["kidney_left"]
        assert detail["high_risk_flagged"] == []

    def test_high_risk_abandoned_marks_layer(self) -> None:
        """高风险类放弃：flag + detail 高风险层标注。"""
        cm = {"prostate": _entry(reliability="low", abandoned=1)}
        flagged, _score, detail = rule_interaction_abandonment(cm, _cfg())
        assert flagged
        assert detail["high_risk_flagged"] == ["prostate"]

    def test_multiple_classes_worst_reported(self) -> None:
        cm = {"liver": _entry(abandoned=1), "prostate": _entry(reliability="low", abandoned=3)}
        flagged, _score, detail = rule_interaction_abandonment(cm, _cfg())
        assert flagged
        assert detail["worst_abandoned_attempts"] == 3


# ---------------------------------------------------------------------------
# is_high_risk（两层逻辑分层依据）
# ---------------------------------------------------------------------------


class TestIsHighRisk:
    def test_reliability_low_is_high_risk(self) -> None:
        assert is_high_risk({"teacherReliability": "low"})

    def test_reliability_high_is_not(self) -> None:
        assert not is_high_risk({"teacherReliability": "high"})

    def test_missing_reliability_explicit_list_fallback(self) -> None:
        """旧数据无标记：显式名单兜底（prostate），否则普通类。"""
        assert is_high_risk({"__class__": "prostate"}, frozenset({"prostate"}))
        assert not is_high_risk({"__class__": "liver"}, frozenset({"prostate"}))

    def test_missing_both_treats_as_normal(self) -> None:
        """无标记无名册 → 普通类（不猜）。"""
        assert not is_high_risk({})


# ---------------------------------------------------------------------------
# run_qc 集成：skipped（纯自动 case）与 flag 通路
# ---------------------------------------------------------------------------

_SHAPE = (64, 64, 32)


def _mask() -> np.ndarray:
    arr = np.zeros(_SHAPE, dtype=np.uint8)
    arr[10:30, 10:30, 5:20] = 1
    return arr


class TestRunQcIntegration:
    def test_no_interaction_metrics_skipped(self, th) -> None:  # noqa: ANN001
        """纯自动 case（interaction_metrics=None）：两规则显式 skipped。

        skipped 不进 flags、不计入 enabled 分母——调用方可区分
        （rule_scores[key] == {"skipped": ...}，与 multi_teacher 同模式）。
        """
        hu = np.full(_SHAPE, 50, dtype=np.int16)
        r = run_qc(
            _mask(),
            _SHAPE,
            "mv-s",
            "sid-s",
            thresholds=th,
            hu_values=hu,
            totalseg_classes={1: "liver"},
        )
        assert RULE_INTERACTION_CONVERGENCE not in r.flags
        assert RULE_INTERACTION_ABANDONMENT not in r.flags
        assert r.rule_scores[RULE_INTERACTION_CONVERGENCE] == {"skipped": "no interaction metrics"}
        assert r.rule_scores[RULE_INTERACTION_ABANDONMENT] == {"skipped": "no interaction metrics"}
        # skipped 不进分母：与 test_run_qc_solid_mask_passes 同输入，passed 仍 true
        assert r.passed, f"flags={r.flags} score={r.overall_score}"

    def test_empty_interaction_metrics_skipped(self, th) -> None:  # noqa: ANN001
        """tracker 空 dict（M6-a provenance.interactionMetrics == {}）：同样 skipped。"""
        r = run_qc(
            _mask(),
            _SHAPE,
            "mv-s2",
            "sid-s2",
            thresholds=th,
            interaction_metrics={},
        )
        assert r.rule_scores[RULE_INTERACTION_CONVERGENCE] == {"skipped": "no interaction metrics"}

    def test_convergence_flag_flows_to_flags_and_score(self, th) -> None:  # noqa: ANN001
        """规则 flag → 进 flags、overall_score 线性降、rule_scores 记 detail。"""
        hu = np.full(_SHAPE, 50, dtype=np.int16)
        im = {"liver": _entry(num_prompts=20)}
        r = run_qc(
            _mask(),
            _SHAPE,
            "mv-c",
            "sid-c",
            thresholds=th,
            hu_values=hu,
            totalseg_classes={1: "liver"},
            interaction_metrics=im,
        )
        assert RULE_INTERACTION_CONVERGENCE in r.flags
        detail = r.rule_scores[RULE_INTERACTION_CONVERGENCE]
        assert detail["flagged"] is True
        assert detail["flagged_classes"] == ["liver"]

    def test_abandonment_flag_flows(self, th) -> None:  # noqa: ANN001
        hu = np.full(_SHAPE, 50, dtype=np.int16)
        im = {"liver": _entry(abandoned=1)}
        r = run_qc(
            _mask(),
            _SHAPE,
            "mv-a",
            "sid-a",
            thresholds=th,
            hu_values=hu,
            totalseg_classes={1: "liver"},
            interaction_metrics=im,
        )
        assert RULE_INTERACTION_ABANDONMENT in r.flags

    def test_not_in_hard_flag_rules(self, th) -> None:  # noqa: ANN001
        """过程级规则不在 hard_flag_rules：flag 不一票否决（过程信号不替代结果门禁）。"""
        hu = np.full(_SHAPE, 50, dtype=np.int16)
        im = {"liver": _entry(num_prompts=20, abandoned=1)}
        r = run_qc(
            _mask(),
            _SHAPE,
            "mv-nh",
            "sid-nh",
            thresholds=th,
            hu_values=hu,
            totalseg_classes={1: "liver"},
            interaction_metrics=im,
        )
        hard = th.get("overall", {}).get("hard_flag_rules", [])
        assert RULE_INTERACTION_CONVERGENCE not in hard
        assert RULE_INTERACTION_ABANDONMENT not in hard
        assert RULE_INTERACTION_CONVERGENCE in r.flags
        assert RULE_INTERACTION_ABANDONMENT in r.flags
        # 其余规则全过 + 两个过程 flag：score = 1 - 2/enabled_n，可能低于
        # min_score（0.5）则 passed=false——那是线性降分的正常行为，非一票否决


# ---------------------------------------------------------------------------
# config 加载（source: cold_start、calibrated_on: 0、注记可见）
# ---------------------------------------------------------------------------


class TestConfigLoading:
    def test_new_rules_in_config(self, th) -> None:  # noqa: ANN001
        ic = th["rules"][RULE_INTERACTION_CONVERGENCE]
        ia = th["rules"][RULE_INTERACTION_ABANDONMENT]
        for cfg in (ic, ia):
            assert cfg["enabled"] is True
            assert cfg["source"] == "cold_start"
            assert cfg["calibrated_on"] == 0
        assert ic["n_max_prompts"] == 15
        assert ic["k_max_steps"] == 6
        assert ic["min_abandonments"] == 1
        assert ia["min_abandonments"] == 1

    def test_cold_start_declared_uncalibrated(self, th) -> None:  # noqa: ANN001
        """cold_start 未校准显式声明在注记里（任务书 5）。"""
        ic = th["rules"][RULE_INTERACTION_CONVERGENCE]
        assert "未校准" in ic["note"]
        ia = th["rules"][RULE_INTERACTION_ABANDONMENT]
        assert "未校准" in ia["note"]

    def test_threshold_overrides_change_verdict(self, th) -> None:  # noqa: ANN001
        """改阈值 = config diff：调低 n_max 后同一条目判 verdict 翻转。"""
        cm = {"liver": _entry(num_prompts=5, convergence_steps=1)}
        assert not rule_interaction_convergence(cm, _cfg(n_max_prompts=15))[0]
        assert rule_interaction_convergence(cm, _cfg(n_max_prompts=4))[0]

    def test_default_config_not_mutated_by_rule_calls(self) -> None:  # noqa: ANN001
        """规则只读 cfg，不写回（load_qc_thresholds 两次加载一致）。"""
        cm = {"liver": _entry(num_prompts=20)}
        rule_interaction_convergence(cm, _cfg())
        assert load_qc_thresholds()["rules"][RULE_INTERACTION_CONVERGENCE]["n_max_prompts"] == 15


@pytest.fixture
def th() -> dict:
    return load_qc_thresholds()
