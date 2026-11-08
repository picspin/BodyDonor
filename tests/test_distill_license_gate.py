"""M4 蒸馏许可门禁单测（LICENSES-THIRD-PARTY.md §8.3 约束 1）。

- 拒绝 nnInteractive 修正产物（CC BY-NC-SA 4.0 ShareAlike 传染）
- 拒绝 licensed 任务教师 mask（推定传染）
- 拒绝 SegVol 逃生通道产物（权重许可待核实）
- 接受 total 教师 mask + 纯人工修正
- 训练池以真实训练 case 列表重查泄漏（proposal §8：M3-REPORT 遗留落地）
"""

from __future__ import annotations

import pytest

from bodydonor.distill_data import (
    ALLOWED_TRAIN_SOURCES,
    COMMERCIAL_DISTILL_TASKS,
    DISTILL_CLASSES,
    DistillDataError,
    build_train_pool,
    check_distill_source,
    check_teacher_task_commercial,
    mask_allowed_in_distill_training_strict,
    read_teacher_provenance,
)

# ---------------------------------------------------------------------------
# 来源白名单门禁（check_distill_source）
# ---------------------------------------------------------------------------

def test_reject_nninteractive_correction():
    """nnInteractive 修正产物：ShareAlike 传染，拒绝（LICENSES §8.1）。"""
    ok, reason = check_distill_source("manual-correction-nninteractive")
    assert not ok
    assert "nnInteractive" in reason
    assert "CC BY-NC-SA" in reason


def test_reject_segvol_source():
    """SegVol 逃生通道产物：权重许可待核实，拒绝（LICENSES §8.1/§4.2）。"""
    ok, reason = check_distill_source("segvol")
    assert not ok
    assert "SegVol" in reason


def test_reject_unknown_source():
    """来源字段不在白名单（打错 / 未登记）即拒绝，不静默进训练集。"""
    ok, _ = check_distill_source("teacher")
    assert not ok
    ok, _ = check_distill_source("student")
    assert not ok
    ok, _ = check_distill_source("")
    assert not ok


def test_reject_licensed_teacher_task():
    """licensed 任务教师 mask：不在商用蒸馏白名单，拒绝（LICENSES §8.1）。"""
    ok, reason = check_distill_source("total", teacher_task="tissue_types")
    assert not ok
    assert "tissue_types" in reason


def test_reject_experimental_flag():
    """experimental 标记产物：逃生通道隔离，拒绝。"""
    ok, reason = check_distill_source("total", teacher_task="total", experimental=True)
    assert not ok
    assert "experimental" in reason


def test_accept_total_teacher_mask():
    """total 教师 mask（商用蒸馏唯一安全教师）：接受。"""
    ok, reason = check_distill_source("total", teacher_task="total")
    assert ok, reason
    assert reason == "ok"


def test_accept_pure_manual_correction():
    """纯人工修正（无第三方权重参与）：接受（LICENSES §8.2 推理 3）。"""
    ok, reason = check_distill_source("manual-correction")
    assert ok, reason


def test_commercial_distill_tasks_only_total():
    """商用蒸馏白名单 = 仅 total（settings.teacher.commercial_distill_tasks 同源）。"""
    assert frozenset({"total"}) == COMMERCIAL_DISTILL_TASKS


def test_allowed_sources_constant():
    """训练标签来源白名单：total 教师 mask + 纯人工修正。"""
    assert frozenset({"total", "manual-correction"}) == ALLOWED_TRAIN_SOURCES


# ---------------------------------------------------------------------------
# 严格版准入（mask_allowed_in_distill_training_strict，扩展 ontology 版）
# ---------------------------------------------------------------------------

def test_strict_gate_rejects_nninteractive_meta():
    meta = {"source": "manual-correction-nninteractive", "task": "total"}
    ok, reason = mask_allowed_in_distill_training_strict(meta)
    assert not ok
    assert "nnInteractive" in reason


def test_strict_gate_rejects_segvol_meta():
    meta = {"source": "segvol-escape", "task": ""}
    ok, _ = mask_allowed_in_distill_training_strict(meta)
    assert not ok  # segvol-escape 不在白名单也不在显式拒绝清单 -> 未知来源拒绝


def test_strict_gate_accepts_total_meta():
    meta = {"source": "total", "task": "total", "experimental": False}
    ok, reason = mask_allowed_in_distill_training_strict(meta)
    assert ok, reason


def test_strict_gate_accepts_manual_correction_meta():
    meta = {"source": "manual-correction"}
    ok, reason = mask_allowed_in_distill_training_strict(meta)
    assert ok, reason


def test_strict_gate_rejects_licensed_meta():
    meta = {"source": "total", "task": "coronary_arteries"}
    ok, reason = mask_allowed_in_distill_training_strict(meta)
    assert not ok
    assert "coronary_arteries" in reason


# ---------------------------------------------------------------------------
# 教师任务白名单（check_teacher_task_commercial，纵深防线）
# ---------------------------------------------------------------------------

def test_teacher_task_commercial_raises_on_licensed():
    with pytest.raises(DistillDataError, match="tissue_types"):
        check_teacher_task_commercial("tissue_types")


def test_teacher_task_commercial_accepts_total():
    check_teacher_task_commercial("total")  # 不 raise


# ---------------------------------------------------------------------------
# 类别集合（DISTILL_CLASSES 与 hold-out 评估 AMOS 15 类同源）
# ---------------------------------------------------------------------------

def test_distill_classes_cover_amos15():
    """蒸馏类别集合 = AMOS 15 类（hold-out 门禁需要的类别集合）。"""
    expected = {
        "spleen", "kidney_right", "kidney_left", "gallbladder", "esophagus",
        "liver", "stomach", "aorta", "inferior_vena_cava", "pancreas",
        "adrenal_gland_right", "adrenal_gland_left", "duodenum",
        "urinary_bladder", "prostate",
    }
    assert set(DISTILL_CLASSES.values()) == expected
    assert sorted(DISTILL_CLASSES.keys()) == list(range(1, 16))


# ---------------------------------------------------------------------------
# 训练池泄漏重查（proposal §8：M4 蒸馏启动时以真实训练 case 列表重查）
# ---------------------------------------------------------------------------

def test_build_train_pool_rejects_holdout_leak(tmp_path, monkeypatch):
    """泄漏重查兜底：排除后 hold-out 仍与训练池交集非空 -> LeakError。

    模拟场景：holdout.txt 在池构建后被扩充（评审把 s0899 补进 hold-out）——
    排除时用的旧列表没有 s0899，重查用同一份文件的新内容发现交集，必须中止
    （proposal §8：非空即中止，M3-REPORT 遗留的 M4 重查）。
    """
    from bodydonor import distill_data as dd
    from bodydonor.holdout import HoldoutEntry, LeakError

    amos_train = tmp_path / "amos22_subset" / "train"
    (amos_train / "imagesTr").mkdir(parents=True)
    ct_root = tmp_path / "ct_lite"
    (ct_root / "images").mkdir(parents=True)
    (ct_root / "images" / "s0899.nii.gz").write_bytes(b"fake")

    monkeypatch.setattr(dd, "AMOS", amos_train)
    monkeypatch.setattr(dd, "CT_LITE", ct_root)

    fake_holdout = tmp_path / "holdout.txt"
    fake_holdout.write_text("", encoding="utf-8")  # 排除时为空（旧列表）

    # 重查时 holdout.txt 已扩充：s0899 补进 hold-out（真实 check_leak，不 mock；
    # check_leak 走 holdout 模块自身的 load_holdout，patch 那边才生效）
    import bodydonor.holdout as holdout_mod

    def _expanded_load(path=None):
        return [HoldoutEntry("s0899", "ts_public")]

    monkeypatch.setattr(holdout_mod, "load_holdout", _expanded_load)

    with pytest.raises(LeakError) as exc:
        build_train_pool(holdout_path=fake_holdout)
    assert "泄漏" in str(exc.value)


def test_build_train_pool_excludes_holdout(tmp_path, monkeypatch):
    """正常路径：hold-out case 逐 case 排除，训练池只含非 hold-out case。"""
    from bodydonor import distill_data as dd

    amos_train = tmp_path / "amos22_subset" / "train"
    (amos_train / "imagesTr").mkdir(parents=True)
    ct_root = tmp_path / "ct_lite"
    (ct_root / "images").mkdir(parents=True)
    # hold-out：amos_0001 / s0045；非 hold-out：amos_0024 / s0899
    for cid in ("amos_0001", "amos_0024"):
        (amos_train / "imagesTr" / f"{cid}.nii.gz").write_bytes(b"fake")
    for cid in ("s0045", "s0899"):
        (ct_root / "images" / f"{cid}.nii.gz").write_bytes(b"fake")

    monkeypatch.setattr(dd, "AMOS", amos_train)
    monkeypatch.setattr(dd, "CT_LITE", ct_root)

    pool = build_train_pool()
    ids = [c.case_id for c in pool]
    assert "amos_0001" not in ids
    assert "s0045" not in ids
    assert "amos_0024" in ids
    assert "s0899" in ids
    assert all(c.label_source == "total" for c in pool)


# ---------------------------------------------------------------------------
# 来源清单实测（read_teacher_provenance；防止门禁被硬编码绕过）
# ---------------------------------------------------------------------------

def test_provenance_defaults_total_without_source_json(tmp_path):
    """ts_out 旁无 source.json -> 默认 total（M2/M3 既有目录，历史路径如实）。"""
    prov = read_teacher_provenance(tmp_path / "ts_out")
    assert prov["source"] == "total"
    assert prov["task"] == "total"
    assert prov["provenance_file"] is False


def test_provenance_reads_declared_nninteractive(tmp_path):
    """source.json 声明 nnInteractive 来源 -> 实测读出（门禁据此拒绝）。"""
    case_dir = tmp_path / "amos_0024"
    (case_dir / "ts_out").mkdir(parents=True)
    (case_dir / "source.json").write_text(
        '{"source": "manual-correction-nninteractive", "task": "total"}',
        encoding="utf-8",
    )
    prov = read_teacher_provenance(case_dir / "ts_out")
    assert prov["source"] == "manual-correction-nninteractive"
    ok, reason = check_distill_source(
        str(prov.get("source")), teacher_task=str(prov.get("task")),
        experimental=bool(prov.get("experimental", False)),
    )
    assert not ok
    assert "nnInteractive" in reason


def test_provenance_reads_declared_licensed_task(tmp_path):
    """source.json 声明 licensed 任务 -> 实测读出（门禁据此拒绝）。"""
    case_dir = tmp_path / "amos_0025"
    (case_dir / "ts_out").mkdir(parents=True)
    (case_dir / "source.json").write_text(
        '{"source": "total", "task": "tissue_types"}', encoding="utf-8"
    )
    prov = read_teacher_provenance(case_dir / "ts_out")
    ok, reason = check_distill_source(
        str(prov.get("source")), teacher_task=str(prov.get("task")),
        experimental=bool(prov.get("experimental", False)),
    )
    assert not ok
    assert "tissue_types" in reason


def test_provenance_corrupt_json_raises(tmp_path):
    """source.json 损坏（坏 JSON）-> DistillDataError（不猜来源）。"""
    case_dir = tmp_path / "amos_0027"
    (case_dir / "ts_out").mkdir(parents=True)
    (case_dir / "source.json").write_text("not-json{", encoding="utf-8")
    with pytest.raises(DistillDataError, match="损坏"):
        read_teacher_provenance(case_dir / "ts_out")


def test_provenance_non_dict_raises(tmp_path):
    """source.json 非 dict（如 list）-> DistillDataError。"""
    case_dir = tmp_path / "amos_0030"
    (case_dir / "ts_out").mkdir(parents=True)
    (case_dir / "source.json").write_text('["total"]', encoding="utf-8")
    with pytest.raises(DistillDataError, match="非法"):
        read_teacher_provenance(case_dir / "ts_out")


def test_provenance_accepts_declared_total(tmp_path):
    """source.json 声明 total -> 实测读出且过门禁。"""
    case_dir = tmp_path / "amos_0033"
    (case_dir / "ts_out").mkdir(parents=True)
    (case_dir / "source.json").write_text(
        '{"source": "total", "task": "total"}', encoding="utf-8"
    )
    prov = read_teacher_provenance(case_dir / "ts_out")
    ok, reason = check_distill_source(
        str(prov.get("source")), teacher_task=str(prov.get("task")),
        experimental=bool(prov.get("experimental", False)),
    )
    assert ok, reason
