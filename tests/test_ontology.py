"""M2：本体映射测试（三态 + 消歧写回 + 逃生通道隔离）。"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from bodydonor.config import ExperimentalTextPrompt, Settings
from bodydonor.ontology import (
    AMBIGUOUS,
    ONTOLOGY_DIR,
    UNRESOLVED,
    MapEntry,
    OntologyError,
    OntologyService,
    load_map,
    load_overrides,
    mask_allowed_in_distill_training,
    normalize_label,
)


@pytest.fixture
def service(settings: Settings) -> OntologyService:  # noqa: ANN001
    return OntologyService(settings)


class TestLoadMap:
    def test_load_real_map(self) -> None:
        """真实 map.yaml 可加载（M2-align 3.4：117 类全覆盖，snomed 全非空）。

        uberon 允许空（拿不准的填空不编造，待核对编码清单见 M2-ALIGN-REPORT）。
        """
        entries = load_map()
        assert len(entries) == 117
        cn_labels = [e.nl_label for e in entries]
        assert len(cn_labels) == len(set(cn_labels))  # 中文标签唯一
        classes = {e.totalseg_class for e in entries}
        # 抽样：主要器官/椎体/肋骨/血管
        for cls in ["liver", "spleen", "kidney_left", "vertebrae_L1",
                    "rib_left_1", "aorta", "hip_left", "lung_upper_lobe_left"]:
            assert cls in classes, cls
        for e in entries:
            assert e.nl_label
            assert e.totalseg_class
            assert e.snomed_ct, f"{e.totalseg_class} snomed 空"
            assert e.synonyms, f"{e.totalseg_class} 无同义词"
            # 每类至少 1 个中文名（nl_label 即中文）+ 1 个英文别名
            assert any(s.isascii() for s in e.synonyms), (
                f"{e.totalseg_class} 缺英文别名"
            )

    def test_bilingual_synonyms(self, service: OntologyService) -> None:
        """中英双语同义词进同一张表：'liver' 与 '肝' 命中同一类别。"""
        r_cn = service.resolve("肝")
        r_en = service.resolve("liver")
        assert r_cn.state == "resolved"
        assert r_en.state == "resolved"
        assert r_cn.entry.totalseg_class == r_en.entry.totalseg_class == "liver"

    def test_missing_version_raises(self) -> None:
        with pytest.raises(OntologyError, match="不存在"):
            load_map("v999", ontology_dir=ONTOLOGY_DIR)

    def test_deprecated_excluded(self, tmp_path: Path) -> None:  # noqa: ANN001
        """deprecated 条目不参与查询。"""
        d = tmp_path / "vtest"
        d.mkdir(parents=True)
        (d / "map.yaml").write_text(
            yaml.safe_dump(
                {
                    "entries": [
                        {
                            "nl_label": "旧名",
                            "totalseg_class": "liver",
                            "status": "deprecated",
                        },
                        {"nl_label": "新名", "totalseg_class": "liver"},
                    ]
                }
            ),
            encoding="utf-8",
        )
        entries = load_map("vtest", ontology_dir=tmp_path)
        assert [e.nl_label for e in entries] == ["新名"]


class TestThreeStates:
    def test_resolved_unique_hit(self, service: OntologyService) -> None:
        """唯一命中 -> resolved。"""
        r = service.resolve("spleen")
        assert r.state == "resolved"
        assert r.entry.totalseg_class == "spleen"

    def test_ambiguous_multiple_candidates(self, service: OntologyService) -> None:
        """多候选 -> ambiguous，附候选与 SNOMED ID。

        '肝血管' 同义词 'hepatic vessel' 与主标签都不止一个候选时——表内
        '肝血管'/'肝脏血管'/'hepatic vessel'/'liver vessel' 全部归一，唯一条目
        -> resolved；构造一个真正多候选场景：临时表两条目共享同义词。
        """
        d = tmp_map_shared_synonym()
        svc = OntologyService(Settings(), ontology_dir=d)
        r = svc.resolve("肝脏血管")
        assert r.state == AMBIGUOUS
        assert len(r.candidates) == 2
        assert {c.entry.totalseg_class for c in r.candidates} == {"liver", "hepaticvessel"}
        # 候选附 SNOMED
        assert all(c.entry.snomed_ct for c in r.candidates)

    def test_unresolved_zero_hits(self, service: OntologyService) -> None:
        """零命中 -> unresolved。"""
        r = service.resolve("不存在的器官xyz")
        assert r.state == UNRESOLVED
        assert r.entry is None
        assert not r.candidates


def tmp_map_shared_synonym() -> Path:
    """构造共享同义词的两条目（真实 ambiguous 场景）。返回 ontology base 目录。"""
    import tempfile

    base = Path(tempfile.mkdtemp())
    d = base / "v1"
    d.mkdir(parents=True)
    (d / "map.yaml").write_text(
        yaml.safe_dump(
            {
                "entries": [
                    {
                        "nl_label": "肝",
                        "synonyms": ["liver", "肝脏血管"],
                        "snomed_ct": "10200004",
                        "totalseg_class": "liver",
                    },
                    {
                        "nl_label": "肝血管",
                        "synonyms": ["hepatic vessel", "肝脏血管"],
                        "snomed_ct": "55441007",
                        "totalseg_class": "hepaticvessel",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    return base


class TestDisambiguate:
    def test_user_choice_writes_back(self, settings: Settings, tmp_path: Path) -> None:  # noqa: ANN001
        """ambiguous -> 用户点选 -> 写回 overrides -> 下次直接 resolved。"""
        d = tmp_map_shared_synonym()
        svc = OntologyService(settings, ontology_dir=d)
        r = svc.resolve("肝脏血管")
        assert r.state == AMBIGUOUS

        # 用户点选 hepaticvessel
        chosen = svc.disambiguate("肝脏血管", "hepaticvessel", ontology_dir=d)
        assert chosen.totalseg_class == "hepaticvessel"
        assert (d / "v1" / "overrides.yaml").exists()

        # 新服务实例（模拟下次会话）查询 -> 直接 resolved
        svc2 = OntologyService(settings, ontology_dir=d)
        r2 = svc2.resolve("肝脏血管")
        assert r2.state == "resolved"
        assert r2.entry.totalseg_class == "hepaticvessel"
        assert "override" in r2.reason

    def test_write_back_rejects_non_candidate(self, settings: Settings) -> None:  # noqa: ANN001
        """写回类别不是候选 -> OntologyError（防写回错误类别）。"""
        d = tmp_map_shared_synonym()
        svc = OntologyService(settings, ontology_dir=d)
        with pytest.raises(OntologyError, match="不是"):
            svc.disambiguate("肝脏血管", "not_a_class", ontology_dir=d)

    def test_write_back_unresolved_rejected(self, settings: Settings) -> None:  # noqa: ANN001
        """unresolved 标签写回 -> OntologyError。"""
        d = tmp_map_shared_synonym()
        svc = OntologyService(settings, ontology_dir=d)
        with pytest.raises(OntologyError, match="unresolved"):
            svc.disambiguate("不存在器官xyz", "liver", ontology_dir=d)

    def test_load_overrides(self, settings: Settings, tmp_path: Path) -> None:  # noqa: ANN001
        """load_overrides 读回用户决策。"""
        d = tmp_map_shared_synonym()
        svc = OntologyService(settings, ontology_dir=d)
        svc.disambiguate("肝脏血管", "hepaticvessel", ontology_dir=d)
        ov = load_overrides("v1", ontology_dir=d)
        assert normalize_label("肝脏血管") in ov
        assert ov[normalize_label("肝脏血管")].totalseg_class == "hepaticvessel"


class TestEscapeHatch:
    def test_escape_hatch_disabled_by_default(self, service: OntologyService) -> None:
        """默认关闭：unresolved 时不路由到逃生通道。"""
        out = service.escape_hatch("不存在的器官xyz")
        assert out["allowed"] is False
        assert out["experimental"] is False

    def test_escape_hatch_requires_explicit_enable(self, tmp_path: Path) -> None:  # noqa: ANN001
        """显式开启（config）后才允许。"""
        d = tmp_map_shared_synonym()
        s = Settings()
        s.experimental_text_prompt = ExperimentalTextPrompt(enabled=True)
        svc = OntologyService(s, ontology_dir=d)
        out = svc.escape_hatch("不存在的器官xyz")
        assert out["allowed"] is True
        assert out["experimental"] is True
        # M2 不真正接模型：占位结果，不产出真 mask
        assert out["state"] == UNRESOLVED


class TestDistillIsolation:
    def test_experimental_mask_blocked_from_training(self) -> None:
        """(强制点) experimental 标记 mask 绝不进蒸馏训练集。"""
        ok, reason = mask_allowed_in_distill_training(
            {"experimental": True, "source": "teacher", "task": "total"}
        )
        assert ok is False
        assert "experimental" in reason

    def test_commercial_distill_only_total(self) -> None:
        """商用蒸馏只允许 total 任务（白名单强制点）。"""
        ok, reason = mask_allowed_in_distill_training(
            {"experimental": False, "source": "teacher", "task": "lung_vessels"}
        )
        assert ok is False
        assert "total" in reason

    def test_normal_teacher_total_allowed(self) -> None:
        ok, _ = mask_allowed_in_distill_training(
            {"experimental": False, "source": "teacher", "task": "total"}
        )
        assert ok is True

    def test_manual_correction_allowed(self) -> None:
        """人工修正产物可进训练集（proposal §6：教师 + 人工修正混合）。"""
        ok, _ = mask_allowed_in_distill_training(
            {"experimental": False, "source": "manual"}
        )
        assert ok is True

    def test_nninteractive_correction_blocked_from_training(self) -> None:
        """(M4 阶段 3 强制点) nnInteractive 修正产物绝不进蒸馏训练集。

        权重 CC BY-NC-SA 4.0 ShareAlike 传染（LICENSES-THIRD-PARTY.md
        §2.2/§8.1）；ontology 版门禁拒绝 manual-correction-nninteractive。
        """
        ok, reason = mask_allowed_in_distill_training(
            {"experimental": False, "source": "manual-correction-nninteractive"}
        )
        assert ok is False
        assert "nnInteractive" in reason

    def test_nninteractive_source_blocked_from_training(self) -> None:
        """(M4 阶段 3 强制点) source=nninteractive 同样拒绝。"""
        ok, reason = mask_allowed_in_distill_training(
            {"experimental": False, "source": "nninteractive"}
        )
        assert ok is False
        assert "CC BY-NC-SA" in reason


class TestEntrySerialization:
    def test_entry_roundtrip(self) -> None:
        e = MapEntry(
            nl_label="肝",
            totalseg_class="liver",
            synonyms=["liver", "肝脏"],
            snomed_ct="10200004",
            uberon="UBERON:0002107",
        )
        d = e.to_dict()
        e2 = MapEntry.from_dict(d)
        assert e2 == e
