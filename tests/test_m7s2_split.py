"""M7-S2：评估三集分离与元数据诚实（NEXT-ITERATION-PLAN §2 S2）。

三集分离验证：
- gate = eval/holdout.txt 30 例：冻结列表 + case_ids_hash；
- train = distill_data.build_train_pool 35 例：与 gate 不相交（check_leak）；
- calib = 现状 pending/空（无独立校准集），派生统计如实标来源。

覆盖：
1. case_ids_hash 稳定/排序不变/增删即变；
2. check_derived_source 与 gate 相交 → LeakError（测试挡住）；
3. train（manifest）与 gate 不相交 + manifest 记录双哈希；
4. 已调规则不再标 cold_start（multi_teacher_consistency / sampling_priority
   w3/w4 → holdout_30_dev；未调的 w1/w2 保持 cold_start）；
5. 可靠性条目 (teacher, task, modality, class) + n + 低频独立特征。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from bodydonor.holdout import (
    LeakError,
    case_ids_hash,
    check_derived_source,
    check_leak,
    load_holdout,
)

# ---------------------------------------------------------------------------
# case_ids_hash：冻结列表哈希
# ---------------------------------------------------------------------------


class TestCaseIdsHash:
    def test_stable_and_order_insensitive(self) -> None:
        a = case_ids_hash(["s0045", "s0071", "amos_0001"])
        b = case_ids_hash(["amos_0001", "s0071", "s0045"])
        assert a == b
        assert len(a) == 16

    def test_changes_on_addition(self) -> None:
        a = case_ids_hash(["s0045", "s0071"])
        b = case_ids_hash(["s0045", "s0071", "amos_0001"])
        assert a != b

    def test_gate_hash_matches_known_value(self) -> None:
        """gate 30 例冻结哈希（本机 eval/holdout.txt 实算，2026-11-14）。"""
        ids = [e.case_id for e in load_holdout()]
        assert len(ids) == 30
        assert case_ids_hash(ids) == "218a663e218d4ce7"


class TestCheckDerivedSource:
    def test_disjoint_passes(self, tmp_path: Path) -> None:
        write = tmp_path / "holdout.txt"
        write.write_text("s0045\tts_public\namos_0001\tamos\n", encoding="utf-8")
        check_derived_source(["s0071", "amos_0004"], path=write)

    def test_overlap_with_gate_raises(self, tmp_path: Path) -> None:
        """派生统计来源与 gate 相交 → LeakError（M7-PLAN §4.3.2）。"""
        write = tmp_path / "holdout.txt"
        write.write_text("s0045\tts_public\namos_0001\tamos\n", encoding="utf-8")
        with pytest.raises(LeakError) as exc:
            check_derived_source(["s0045", "s0071"], path=write)
        assert "gate" in str(exc.value)

    def test_defaults_to_real_holdout_file(self) -> None:
        """gate 30 例自身派生的统计（现状）→ LeakError（样本内开发指标）。"""
        gate_ids = [e.case_id for e in load_holdout()]
        with pytest.raises(LeakError):
            check_derived_source(gate_ids)


# ---------------------------------------------------------------------------
# 三集分离：train（manifest）与 gate 不相交
# ---------------------------------------------------------------------------


class TestThreeSetSplit:
    def test_train_pool_disjoint_from_gate(self, tmp_path, monkeypatch) -> None:
        """train 池逐 case 排除 hold-out（check_leak 兜底），与 gate 不相交。"""
        from bodydonor import distill_data as dd

        amos_train = tmp_path / "amos22_subset" / "train"
        (amos_train / "imagesTr").mkdir(parents=True)
        ct_root = tmp_path / "ct_lite"
        (ct_root / "images").mkdir(parents=True)
        for cid in ("amos_0001", "amos_0024"):
            (amos_train / "imagesTr" / f"{cid}.nii.gz").write_bytes(b"fake")
        for cid in ("s0045", "s0899"):
            (ct_root / "images" / f"{cid}.nii.gz").write_bytes(b"fake")

        monkeypatch.setattr(dd, "AMOS", amos_train)
        monkeypatch.setattr(dd, "CT_LITE", ct_root)

        pool = dd.build_train_pool()
        train_ids = [c.case_id for c in pool]
        gate_ids = [e.case_id for e in load_holdout()]
        # 真实 gate 文件里 amos_0001/s0045 在 gate，池里必须排除
        assert set(train_ids) & set(gate_ids) == set()
        assert "amos_0001" not in train_ids
        assert "s0045" not in train_ids
        # check_leak 真跑（非空才 raise）
        check_leak(train_ids)

    def test_manifest_records_both_hashes(self, tmp_path, monkeypatch) -> None:
        """manifest leak_check 记录 train/gate 双哈希，train != gate。"""
        from bodydonor import distill_data as dd

        amos_train = tmp_path / "amos22_subset" / "train"
        (amos_train / "imagesTr").mkdir(parents=True)
        ct_root = tmp_path / "ct_lite"
        (ct_root / "images").mkdir(parents=True)
        for cid in ("amos_0024",):
            (amos_train / "imagesTr" / f"{cid}.nii.gz").write_bytes(b"fake")
        for cid in ("s0899",):
            (ct_root / "images" / f"{cid}.nii.gz").write_bytes(b"fake")

        # 教师 mask 预检要求 117 类全量——用 dry_run 跳过落盘，但预检仍会跑；
        # 最小覆盖：只验证 build_train_pool 层的哈希（prepare_dataset 预检
        # 需要 TS 真实 mask，属于 local_artifacts 范围，本测试不依赖）。
        monkeypatch.setattr(dd, "AMOS", amos_train)
        monkeypatch.setattr(dd, "CT_LITE", ct_root)

        pool = dd.build_train_pool()
        train_hash = case_ids_hash([c.case_id for c in pool])
        gate_hash = case_ids_hash([e.case_id for e in load_holdout()])
        assert train_hash != gate_hash
        assert len(train_hash) == 16 and len(gate_hash) == 16

    def test_real_manifest_train_gate_disjoint(self) -> None:
        """本机真实 manifest（35 例）与 gate（30 例）不相交（local_artifacts）。"""
        manifest_path = (
            Path.home()
            / "data"
            / "BodyDonor"
            / "nnUNet_raw"
            / "Dataset501_BodyDonorDistill"
            / "manifest.json"
        )
        if not manifest_path.exists():
            pytest.skip("本机真实 manifest 缺失（local_artifacts）")
        import json

        m = json.loads(manifest_path.read_text(encoding="utf-8"))
        train_ids = [c["case_id"] for c in m["cases"]]
        gate_ids = [e.case_id for e in load_holdout()]
        assert set(train_ids) & set(gate_ids) == set()
        leak = m["leak_check"]
        assert leak["rechecked_with_real_train_list"] is True
        # 双哈希记录（M7-S2 新增字段，旧 manifest 缺失时跳过）
        if "train_case_ids_hash" in leak:
            assert leak["train_case_ids_hash"] != leak["gate_case_ids_hash"]
            assert leak["train_case_ids_hash"] == case_ids_hash(train_ids)


# ---------------------------------------------------------------------------
# 元数据诚实：已调规则不再标 cold_start
# ---------------------------------------------------------------------------

QC_THRESHOLDS = Path(__file__).resolve().parents[1] / "config" / "qc_thresholds.yaml"


class TestMetadataHonesty:
    @pytest.fixture
    def thresholds(self) -> dict:
        return yaml.safe_load(QC_THRESHOLDS.read_text(encoding="utf-8"))

    def test_multi_teacher_consistency_not_cold_start(self, thresholds: dict) -> None:
        """聚合方式已在 30 例上选定 → source=holdout_30_dev，开发依据读
        development_provenance（P2：calibrated_on 是反馈计数，从 0 起累计）。"""
        cfg = thresholds["rules"]["multi_teacher_consistency"]
        assert cfg["source"] == "holdout_30_dev"
        dev = cfg["development_provenance"]
        assert dev["source"] == "holdout_30_dev"
        assert dev["n_cases"] == 30
        assert dev["case_ids_hash"] == "218a663e218d4ce7"
        assert dev["selected_at"] == "2026-11-11"
        assert "聚合方式" in dev["procedure"]
        assert "cold_start" not in str(cfg.get("note", "")) or "保持 cold_start" not in str(
            cfg.get("note", "")
        )
        # 数值未动（M7-S2 只改元数据，不改阈值）
        assert cfg["threshold"] == 0.80
        assert cfg["enabled"] is True
        assert cfg["calibrated_on"] == 0  # 反馈计数从 0 起（未回流）

    def test_derived_hash_written_verbatim(self, thresholds: dict) -> None:
        """note 引用的 derived_from_case_ids_hash 必须写出真实哈希值（不只是公式），
        且与 eval/holdout.txt 实算一致——引用无值无法核对（deep-reviewer finding）。"""
        for node in (
            thresholds["rules"]["multi_teacher_consistency"],
            thresholds["sampling_priority"],
        ):
            real_hash = case_ids_hash([e.case_id for e in load_holdout()])
            assert real_hash in node["note"]
            # P2：结构化开发 provenance 同记真实哈希（不只 note 引用）
            assert node["development_provenance"]["case_ids_hash"] == real_hash
            assert node["development_provenance"]["source"] == "holdout_30_dev"
            assert node["development_provenance"]["n_cases"] == 30

    def test_sampling_priority_w3_w4_marked(self, thresholds: dict) -> None:
        """w3/w4 定义依据 30 例实测 → 节 source=holdout_30_dev；w1/w2 未调。"""
        cfg = thresholds["sampling_priority"]
        assert cfg["source"] == "holdout_30_dev"
        dev = cfg["development_provenance"]
        assert dev["n_cases"] == 30
        assert dev["selected_at"] == "2026-11-12"
        assert "w3" in dev["procedure"]
        assert "w1/w2" in dev["procedure"]  # 四个权重不冒充都已校准
        assert cfg["weights"]["w3"] == 1.0
        assert cfg["weights"]["w4"] == 1.0
        # w1/w2 未在 30 例上调整——note 如实记录（量级假设初值保持 cold_start）
        assert "w1/w2 未在 30 例上调整" in cfg["note"]
        assert cfg["calibrated_on"] == 0  # 反馈计数从 0 起（未回流）

    def test_uncalibrated_rules_stay_cold_start(self, thresholds: dict) -> None:
        """未在 30 例上调过的规则保持 cold_start 不动（逐项核对，不批量改）。"""
        for rule in ("volume_prior", "connected_component", "holes",
                     "boundary_smoothness", "hu_distribution",
                     "interaction_convergence", "interaction_abandonment",
                     "left_right_symmetry"):
            cfg = thresholds["rules"][rule]
            assert cfg["source"] == "cold_start", f"{rule} 被误改"
            assert cfg["calibrated_on"] == 0, f"{rule} 被误改"

    def test_gating_stays_cold_start(self, thresholds: dict) -> None:
        """gating 分档比例未经决策回流校准 → 保持 cold_start。"""
        cfg = thresholds["gating"]
        assert cfg["source"] == "cold_start"
        assert cfg["calibrated_on"] == 0


# ---------------------------------------------------------------------------
# 可靠性表：(teacher, task, modality, class) + n + 低频独立特征
# ---------------------------------------------------------------------------


class TestReliabilityEntry:
    def test_organised_by_teacher_task_modality_class(self) -> None:
        from bodydonor.interaction_metrics import teacher_reliability_entry

        e = teacher_reliability_entry("liver", {"liver": 30})
        assert e["teacher"] == "totalsegmentator"
        assert e["task"] == "total"
        assert e["modality"] == "ct"
        assert e["class"] == "liver"
        assert e["n"] == 30
        assert e["reliability"] == "high"
        assert e["low_frequency"] is False

    def test_n_attached_and_low_freq_independent(self) -> None:
        """低频是独立特征：reliability=unknown（不折成 low），low_frequency=True。"""
        from bodydonor.interaction_metrics import teacher_reliability_entry

        e = teacher_reliability_entry("brain", {"brain": 3})
        assert e["n"] == 3
        assert e["low_frequency"] is True
        assert e["reliability"] == "unknown"

    def test_prostate_explicit_low_with_basis(self) -> None:
        """prostate 显式 low（依据：M4 实测教师 prostate Dice 0.5914）。"""
        from bodydonor.interaction_metrics import (
            HIGH_RISK_TEACHER_CLASSES,
            teacher_reliability_entry,
        )

        e = teacher_reliability_entry("prostate", {"prostate": 18})
        assert e["n"] == 18
        assert e["reliability"] == "low"
        assert e["low_frequency"] is False
        # 显式名单仍在（依据记录）
        assert "prostate" in HIGH_RISK_TEACHER_CLASSES

    def test_missing_count_data_unknown(self) -> None:
        """计数数据缺 → n=None、reliability=unknown（不猜）。"""
        from bodydonor.interaction_metrics import teacher_reliability_entry

        e = teacher_reliability_entry("liver", None)
        assert e["n"] is None
        assert e["reliability"] == "unknown"
        assert e["low_frequency"] is False

    def test_dimension_overrides(self) -> None:
        """(teacher, task, modality, class) 维度可覆盖（现有产物只有 TS total CT）。"""
        from bodydonor.interaction_metrics import teacher_reliability_entry

        e = teacher_reliability_entry("liver", {"liver": 30}, task="total", modality="mr")
        assert e["task"] == "total"
        assert e["modality"] == "mr"


# ---------------------------------------------------------------------------
# 兼容入口：teacher_reliability 语义不变（tracker.build_metrics 打标）
# ---------------------------------------------------------------------------


class TestCompatEntry:
    def test_teacher_reliability_semantics_unchanged(self) -> None:
        from bodydonor.interaction_metrics import teacher_reliability

        assert teacher_reliability("prostate", {"prostate": 18}) == "low"
        assert teacher_reliability("brain", {"brain": 3}) == "low"
        assert teacher_reliability("liver", {"liver": 30}) == "high"
        assert teacher_reliability("liver", None) == "high"
