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
    write_teacher_source_json,
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


# ---------------------------------------------------------------------------
# merge_teacher_mask / prepare_dataset（found 实测进 manifest，n_classes_found）
# ---------------------------------------------------------------------------

def _make_case_fixture(tmp_path, case_id: str, classes: list[str], pad_to: int = 117):
    """最小合成 case：一致 affine 的原图 + 指定蒸馏类的空 mask + 占位补齐到 117。

    蒸馏类文件为真实小 NIfTI（merge 会 nib.load）；占位补齐文件只被 glob 计数
    （keep_classes 之外不加载），空字节即可。pad_to=0 不补齐（配合
    _touch_ts_out 自造部分目录）。
    """
    import nibabel as nib
    import numpy as np

    case_dir = tmp_path / case_id
    (case_dir / "imagesTr").mkdir(parents=True)
    img_path = case_dir / "imagesTr" / f"{case_id}.nii.gz"
    nib.save(nib.Nifti1Image(np.zeros((8, 8, 4), dtype=np.int16), np.eye(4)), str(img_path))
    ts_out = case_dir / "ts_out"
    ts_out.mkdir()
    for cls in classes:
        nib.save(nib.Nifti1Image(np.zeros((8, 8, 4), dtype=np.uint8), np.eye(4)),
                 str(ts_out / f"{cls}.nii.gz"))
    for i in range(max(0, pad_to - len(classes))):
        (ts_out / f"ts_extra_{i:03d}.nii.gz").write_bytes(b"")
    return img_path, ts_out


def test_merge_teacher_mask_returns_found(tmp_path):
    """merge 返回 (merged, dropped, found)：found = 实际找到的 keep_classes 类。"""
    import nibabel as nib

    from bodydonor.distill_data import merge_teacher_mask

    img_path, ts_out = _make_case_fixture(
        tmp_path, "amos_9001", ["spleen", "liver", "prostate"]
    )
    img = nib.load(str(img_path))
    keep = set(DISTILL_CLASSES.values())
    merged, dropped, found = merge_teacher_mask(ts_out, img_path, keep, "amos_9001", img=img)
    assert found == ["liver", "prostate", "spleen"]  # sorted 目录序
    # pad 占位文件（keep 之外）进 dropped，蒸馏类一个不落
    assert sorted(dropped) == sorted(f"ts_extra_{i:03d}" for i in range(114))
    assert merged.shape == (8, 8, 4)


def test_merge_teacher_mask_drops_non_keep_classes(tmp_path):
    """keep_classes 之外的 TS 类进 dropped，不进 found。"""
    import nibabel as nib

    from bodydonor.distill_data import merge_teacher_mask

    img_path, ts_out = _make_case_fixture(tmp_path, "amos_9002", ["spleen", "heart"])
    img = nib.load(str(img_path))
    merged, dropped, found = merge_teacher_mask(
        ts_out, img_path, {"spleen"}, "amos_9002", img=img
    )
    assert found == ["spleen"]
    assert "heart" in dropped
    assert "heart" not in found
    assert merged.shape == (8, 8, 4)


def test_prepare_dataset_manifest_records_n_classes_found(tmp_path, monkeypatch):
    """manifest 逐 case 记真实找到的蒸馏类别数（实测 found，非默认 0）。

    完整 case（15 蒸馏类齐全 + 117 文件）过预检后 n_classes_found == 15——
    修复前该字段恒为默认 0。"缺类记小数"的语义在 merge 层覆盖
    （test_merge_teacher_mask_returns_found：found = 实际存在的类数）；预检
    加严（问题 3）后部分 ts_out 在 prepare_dataset 即拒绝，manifest 层只见
    完整 case。全程合成 fixture（tmp_path），不跑 GPU 推理、不读真实数据目录。
    """
    import json

    from bodydonor import distill_data as dd

    full_classes = sorted(DISTILL_CLASSES.values())
    _, ts_full = _make_case_fixture(tmp_path, "amos_9101", full_classes)
    _, ts_full2 = _make_case_fixture(tmp_path, "amos_9102", full_classes)
    img_full = tmp_path / "amos_9101" / "imagesTr" / "amos_9101.nii.gz"
    img_full2 = tmp_path / "amos_9102" / "imagesTr" / "amos_9102.nii.gz"

    amos_train = tmp_path / "amos22_subset" / "train"
    (amos_train / "imagesTr").mkdir(parents=True)
    ct_root = tmp_path / "ct_lite"
    (ct_root / "images").mkdir(parents=True)
    monkeypatch.setattr(dd, "AMOS", amos_train)
    monkeypatch.setattr(dd, "CT_LITE", ct_root)

    # 直接构造 DistillCase（绕过 build_train_pool 的真实目录扫描）
    cases = [
        dd.DistillCase(case_id="amos_9101", source="amos", image_path=img_full,
                       teacher_out=ts_full),
        dd.DistillCase(case_id="amos_9102", source="amos", image_path=img_full2,
                       teacher_out=ts_full2),
    ]
    monkeypatch.setattr(dd, "build_train_pool", lambda holdout_path=None: cases)

    out_root = tmp_path / "nnUNet_raw"
    result = dd.prepare_dataset(out_root=out_root, dry_run=False)
    assert result["n_cases"] == 2

    manifest = json.loads((out_root / dd.DISTILL_DATASET_NAME / "manifest.json").read_text(
        encoding="utf-8"))
    by_id = {c["case_id"]: c for c in manifest["cases"]}
    # 实测 found（15），不是修复前的默认 0
    assert by_id["amos_9101"]["n_classes_found"] == 15
    assert by_id["amos_9102"]["n_classes_found"] == 15
    # dropped = 117 全量类里不在 keep 的（pad 占位文件 102 个，逐 case 实测）
    assert by_id["amos_9101"]["dropped_classes"] == [f"ts_extra_{i:03d}" for i in range(102)]

# ---------------------------------------------------------------------------
# 推理成功后写 source.json（write_teacher_source_json，位置与读回约定一致）
# ---------------------------------------------------------------------------

def test_write_teacher_source_json_roundtrip(tmp_path):
    """写出的 source.json 内容正确，read_teacher_provenance 能读回（实测来源）。"""
    case_dir = tmp_path / "amos_9301"
    ts_out = case_dir / "ts_out"
    ts_out.mkdir(parents=True)

    write_teacher_source_json(ts_out, "amos_9301")

    written = case_dir / "source.json"
    assert written.exists()  # 写在 teacher_out.parent，与 read_teacher_provenance 一致
    import json

    doc = json.loads(written.read_text(encoding="utf-8"))
    assert doc == {
        "source": "total",
        "task": "total",
        "created_by": "run_distill_teacher_inference.py",
        "case_id": "amos_9301",
    }
    prov = read_teacher_provenance(ts_out)
    assert prov["provenance_file"] is True
    assert prov["source"] == "total"
    assert prov["task"] == "total"
    assert prov["case_id"] == "amos_9301"
