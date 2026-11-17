"""M7-S3P R2 ③ source.json 回填脚本单测（test_m7_s3p2_backfill.py）。

覆盖 backfill_source_json 的来源判定与写入位置契约：
- decide_amos_basis：M3 评估窗口 / M4 训练窗口 -> total 依据；窗口外/类不全 -> None
- decide_ct_lite_basis：M3 冷启动窗口 -> 依据；窗口外 -> None
- main --dry-run：不写任何文件；全部证据通过才写库级一份（ct_lite fail-closed）
- 写入位置契约：AMOS 每目录一份 / ct_lite 一库一份（read_teacher_provenance 读回
  provenance_missing=False）
"""

from __future__ import annotations

import importlib.util
import json
import time
from pathlib import Path

import pytest

from bodydonor.distill_data import (
    DISTILL_CLASSES,
    read_teacher_provenance,
)
from bodydonor.holdout import HoldoutEntry

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "backfill_source_json.py"
_spec = importlib.util.spec_from_file_location("backfill_source_json", _SCRIPT)
bf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bf)


def _epoch(s: str) -> float:
    return time.mktime(time.strptime(s, "%Y-%m-%d %H:%M"))


def _seed_full_ts_out(teacher_out: Path, mtime: str) -> None:
    """117 类全量 ts_out（空占位 + 15 蒸馏类），全部 nii mtime 设为指定时刻
    （判据 = 全目录 mtime 一致，非单文件）。"""
    teacher_out.mkdir(parents=True, exist_ok=True)
    names = [f"cls_{i:03d}" for i in range(102)] + sorted(DISTILL_CLASSES.values())
    assert len(names) == 117
    ts = _epoch(mtime)
    import os

    for n in names:
        f = teacher_out / f"{n}.nii.gz"
        f.write_bytes(b"")
        os.utime(f, (ts, ts))


# ---------------------------------------------------------------------------
# decide_amos_basis
# ---------------------------------------------------------------------------

def test_amos_m3_eval_window_returns_basis(tmp_path):
    ts_out = tmp_path / "amos_0004" / "ts_out"
    _seed_full_ts_out(ts_out, "2026-11-07 18:20")
    decided = bf.decide_amos_basis(tmp_path / "amos_0004")
    assert decided is not None
    basis, created_by = decided
    assert "M3 门禁评估" in basis
    assert "evaluate_holdout" in created_by


def test_amos_m4_train_window_returns_basis(tmp_path):
    ts_out = tmp_path / "amos_0024" / "ts_out"
    _seed_full_ts_out(ts_out, "2026-11-08 11:10")
    decided = bf.decide_amos_basis(tmp_path / "amos_0024")
    assert decided is not None
    basis, created_by = decided
    assert "M4 训练侧" in basis
    assert created_by == "run_distill_teacher_inference.py"


def test_amos_outside_windows_keeps_missing(tmp_path):
    """mtime 窗口外（如 2026-11-09 新补丁批次）-> None，不猜。"""
    ts_out = tmp_path / "amos_0035" / "ts_out"
    _seed_full_ts_out(ts_out, "2026-11-09 10:56")
    assert bf.decide_amos_basis(tmp_path / "amos_0035") is None


def test_amos_incomplete_ts_out_keeps_missing(tmp_path):
    """类数不全（非 total 全量布局）-> None，不猜。"""
    case_dir = tmp_path / "amos_0099"
    ts_out = case_dir / "ts_out"
    ts_out.mkdir(parents=True)
    (ts_out / "spleen.nii.gz").write_bytes(b"")
    assert bf.decide_amos_basis(case_dir) is None


# ---------------------------------------------------------------------------
# decide_ct_lite_basis
# ---------------------------------------------------------------------------

def test_ct_lite_m3_coldstart_window_returns_basis(tmp_path):
    ts_out = tmp_path / "s0045"
    _seed_full_ts_out(ts_out, "2026-11-07 17:30")
    decided = bf.decide_ct_lite_basis(tmp_path / "s0045")
    assert decided is not None
    basis, created_by = decided
    assert "M3 QC 冷启动" in basis
    assert created_by == "qc_coldstart.py"


def test_ct_lite_outside_window_keeps_missing(tmp_path):
    ts_out = tmp_path / "s0999"
    _seed_full_ts_out(ts_out, "2026-11-14 10:00")
    assert bf.decide_ct_lite_basis(tmp_path / "s0999") is None


# ---------------------------------------------------------------------------
# main：dry-run 不写 / 写入位置契约 / fail-closed
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_data(tmp_path, monkeypatch):
    """AMOS 1 case（M3 评估窗口）+ ct_lite 2 case（冷启动窗口）+ holdout 命中。"""
    amos_dir = tmp_path / "amos22_subset"
    _seed_full_ts_out(amos_dir / "amos_0004" / "ts_out", "2026-11-07 18:20")
    ct_root = tmp_path / "ct_lite"
    _seed_full_ts_out(ct_root / "ts_out" / "s0045", "2026-11-07 17:30")
    _seed_full_ts_out(ct_root / "ts_out" / "s0899", "2026-11-07 18:10")
    monkeypatch.setattr(bf, "AMOS", amos_dir / "train")
    monkeypatch.setattr(bf, "CT_LITE", ct_root)
    monkeypatch.setattr(
        bf, "load_holdout",
        lambda path=None: [HoldoutEntry("amos_0004", "amos"),
                           HoldoutEntry("s0045", "ts_public")],
    )
    return amos_dir, ct_root


def test_main_dry_run_writes_nothing(fake_data, capsys):
    amos_dir, ct_root = fake_data
    rc = bf._run(dry_run=True)
    assert rc == 0
    assert not (amos_dir / "amos_0004" / "source.json").exists()
    assert not (ct_root / "ts_out" / "source.json").exists()
    out = capsys.readouterr().out
    assert "dry-run" in out


def test_main_writes_amos_per_case_and_ct_lite_library_level(fake_data, capsys):
    """写入位置契约：AMOS {case}/source.json 每目录一份；ct_lite ts_out/
    source.json 一库一份——read_teacher_provenance 读回 provenance_status=inferred。"""
    amos_dir, ct_root = fake_data
    rc = bf._run(dry_run=False)
    assert rc == 0
    amos_doc = json.loads(
        (amos_dir / "amos_0004" / "source.json").read_text(encoding="utf-8")
    )
    assert amos_doc["source"] == "total"
    assert amos_doc["task"] == "total"
    assert amos_doc["backfill"] is True
    assert "backfill_basis" in amos_doc
    assert amos_doc["backfilled_at"] == "2026-11-16"
    ct_doc = json.loads(
        (ct_root / "ts_out" / "source.json").read_text(encoding="utf-8")
    )
    assert ct_doc["source"] == "total"
    assert ct_doc["cases"] == ["s0045", "s0899"]
    # read_teacher_provenance 读回（写入位置与读取位置一致的实质验收）
    assert read_teacher_provenance(amos_dir / "amos_0004" / "ts_out")[
        "provenance_status"
    ] == "inferred"
    assert read_teacher_provenance(ct_root / "ts_out" / "s0899")[
        "provenance_status"
    ] == "inferred"


def test_main_idempotent_skips_existing(fake_data):
    """已有 source.json 不重做（幂等：第二次跑 0 写入）。"""
    amos_dir, ct_root = fake_data
    assert bf._run(dry_run=False) == 0
    before = (amos_dir / "amos_0004" / "source.json").read_text(encoding="utf-8")
    assert bf._run(dry_run=False) == 0
    assert (amos_dir / "amos_0004" / "source.json").read_text(encoding="utf-8") == before


def test_main_undecided_dir_without_aorta_does_not_crash(tmp_path, monkeypatch, capsys):
    """W1：undecided 分支 evidence 不能因 ts_out 缺 aorta 崩溃（不猜，列未决）。"""
    amos_dir = tmp_path / "amos22_subset"
    case = amos_dir / "amos_0099" / "ts_out"
    case.mkdir(parents=True)
    (case / "spleen.nii.gz").write_bytes(b"")  # 不含 aorta：decide 返回 None
    monkeypatch.setattr(bf, "AMOS", amos_dir / "train")
    (tmp_path / "ct_lite" / "ts_out").mkdir(parents=True)  # ct_lite 空但不缺目录
    monkeypatch.setattr(bf, "CT_LITE", tmp_path / "ct_lite")
    monkeypatch.setattr(bf, "load_holdout", lambda path=None: [])
    assert bf._run(dry_run=True) == 0  # 不崩，保持缺失
    err = capsys.readouterr().err
    assert "KEEP-MISSING" in err and "amos_0099" in err
    assert not (case.parent / "source.json").exists()


def test_main_empty_ct_lite_ts_out_writes_nothing(tmp_path, monkeypatch):
    """W7：ct_lite ts_out 空时不写库级清单（对零个 case 不下 total 结论）。"""
    monkeypatch.setattr(bf, "AMOS", tmp_path / "amos22_subset" / "train")
    (tmp_path / "ct_lite" / "ts_out").mkdir(parents=True)  # 存在但空
    monkeypatch.setattr(bf, "CT_LITE", tmp_path / "ct_lite")
    monkeypatch.setattr(bf, "load_holdout", lambda path=None: [])
    assert bf._run(dry_run=False) == 0
    assert not (tmp_path / "ct_lite" / "ts_out" / "source.json").exists()


def test_main_ct_lite_fail_closed_when_any_case_undecided(tmp_path, monkeypatch, capsys):
    """ct_lite 任一 case 证据不符 -> 整库保持缺失（fail-closed，不部分回填）。"""
    amos_dir = tmp_path / "amos22_subset"
    _seed_full_ts_out(amos_dir / "amos_0004" / "ts_out", "2026-11-07 18:20")
    ct_root = tmp_path / "ct_lite"
    _seed_full_ts_out(ct_root / "ts_out" / "s0045", "2026-11-07 17:30")
    _seed_full_ts_out(ct_root / "ts_out" / "s0999", "2026-11-14 10:00")  # 窗口外
    monkeypatch.setattr(bf, "AMOS", amos_dir / "train")
    monkeypatch.setattr(bf, "CT_LITE", ct_root)
    monkeypatch.setattr(bf, "load_holdout", lambda path=None: [])
    assert bf._run(dry_run=False) == 0
    assert not (ct_root / "ts_out" / "source.json").exists()
    # AMOS 正常回填不受影响
    assert (amos_dir / "amos_0004" / "source.json").exists()
    err = capsys.readouterr().err
    assert "ct_lite" in err and "保持缺失" in err


# ---------------------------------------------------------------------------
# S3P2-R2 ①：证明字段（attestation/evidence/evidence_scope/generated_by）
# ---------------------------------------------------------------------------

def test_new_write_has_attestation_fields(fake_data):
    """新回填文档带证明字段：attestation=inferred + evidence=mtime_window +
    evidence_scope（AMOS 本 case）+ generated_by 含脚本 sha256。"""
    amos_dir, _ = fake_data
    assert bf._run(dry_run=False) == 0
    doc = json.loads(
        (amos_dir / "amos_0004" / "source.json").read_text(encoding="utf-8")
    )
    assert doc["attestation"] == "inferred"
    assert doc["evidence"] == "mtime_window"
    assert doc["evidence_scope"] == "amos_0004"
    assert doc["generated_by"].startswith("backfill_source_json.py sha256=")
    sha = doc["generated_by"].split("=", 1)[1]
    assert len(sha) == 64  # 实际脚本 sha256，非占位


def test_upgrade_in_place_adds_fields_keeps_existing(fake_data):
    """已有 backfill 文档就地补证明字段：原有字段保留，只增证明字段。"""
    amos_dir, ct_root = fake_data
    assert bf._run(dry_run=False) == 0
    pre = json.loads(
        (amos_dir / "amos_0004" / "source.json").read_text(encoding="utf-8")
    )
    # 删证明字段模拟旧文件（R1b 时代磁盘现状）
    for k in ("attestation", "evidence", "evidence_scope", "generated_by"):
        pre.pop(k, None)
    (amos_dir / "amos_0004" / "source.json").write_text(
        json.dumps(pre, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    assert bf._run(dry_run=False) == 0
    post = json.loads(
        (amos_dir / "amos_0004" / "source.json").read_text(encoding="utf-8")
    )
    # 原有字段保留（不删不重做）
    for k in ("source", "task", "created_by", "case_id", "backfill",
              "backfill_basis", "backfilled_at"):
        assert post[k] == pre[k]
    # 证明字段补齐
    assert post["attestation"] == "inferred"
    assert post["evidence"] == "mtime_window"
    assert post["evidence_scope"] == "amos_0004"
    assert post["generated_by"].startswith("backfill_source_json.py sha256=")


def test_upgrade_never_downgrades_asserted(fake_data):
    """不降级 asserted：已有 attestation=asserted 的文件不被回填覆盖。"""
    amos_dir, _ = fake_data
    assert bf._run(dry_run=False) == 0
    doc = json.loads(
        (amos_dir / "amos_0004" / "source.json").read_text(encoding="utf-8")
    )
    doc["attestation"] = "asserted"
    (amos_dir / "amos_0004" / "source.json").write_text(
        json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    before = (amos_dir / "amos_0004" / "source.json").read_text(encoding="utf-8")
    assert bf._run(dry_run=False) == 0
    assert (amos_dir / "amos_0004" / "source.json").read_text(encoding="utf-8") == before


def test_upgrade_idempotent_second_run_zero_changes(fake_data):
    """inferred 已升级过：第二次跑不重写（幂等，attestation 已存在跳过）。"""
    amos_dir, _ = fake_data
    assert bf._run(dry_run=False) == 0
    before = (amos_dir / "amos_0004" / "source.json").read_text(encoding="utf-8")
    assert bf._run(dry_run=False) == 0
    assert (amos_dir / "amos_0004" / "source.json").read_text(encoding="utf-8") == before


def test_ct_lite_library_scope_enumerates_cases(fake_data):
    """ct_lite 库级清单 evidence_scope 枚举覆盖的 cases；读侧 scope 内
    inferred、scope 外新增目录 missing（不继承库级 provenance）。"""
    amos_dir, ct_root = fake_data
    assert bf._run(dry_run=False) == 0
    doc = json.loads(
        (ct_root / "ts_out" / "source.json").read_text(encoding="utf-8")
    )
    assert doc["evidence_scope"] == ["s0045", "s0899"]
    # scope 内 case -> inferred
    assert read_teacher_provenance(ct_root / "ts_out" / "s0045")[
        "provenance_status"
    ] == "inferred"
    # scope 外新增目录 -> missing（不继承）
    (ct_root / "ts_out" / "s_new").mkdir()
    assert read_teacher_provenance(ct_root / "ts_out" / "s_new")[
        "provenance_status"
    ] == "missing"


def test_old_measured_write_without_backfill_maps_asserted(fake_data):
    """旧实测写入（无 attestation 无 backfill）读侧映射 asserted；回填不覆盖。"""
    amos_dir, _ = fake_data
    (amos_dir / "amos_0005" / "ts_out").mkdir(parents=True)
    (amos_dir / "amos_0005" / "source.json").write_text(
        '{"source": "total", "task": "total", '
        '"created_by": "run_distill_teacher_inference.py", "case_id": "amos_0005"}',
        encoding="utf-8",
    )
    assert read_teacher_provenance(amos_dir / "amos_0005" / "ts_out")[
        "provenance_status"
    ] == "asserted"
    # 回填不覆盖旧实测文件（无 backfill=true 不升级）
    assert bf._run(dry_run=False) == 0
    doc = json.loads(
        (amos_dir / "amos_0005" / "source.json").read_text(encoding="utf-8")
    )
    assert "attestation" not in doc  # 未被写 inferred


def test_upgrade_ct_lite_scope_enumerates_from_existing_cases(fake_data):
    """已有 ct_lite backfill 文档就地补字段时 evidence_scope 枚举文件内
    cases（非空列表）——scope 与清单覆盖范围一致。"""
    _, ct_root = fake_data
    # 模拟 R1b 时代磁盘现状：库级 backfill 文档无证明字段，cases 已枚举
    (ct_root / "ts_out" / "source.json").write_text(
        json.dumps({"source": "total", "task": "total", "created_by": "qc_coldstart.py",
                    "case_id": "ct_lite/ts_out", "backfill": True,
                    "backfill_basis": "M3 冷启动", "backfilled_at": "2026-11-16",
                    "cases": ["s0045", "s0899"]}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    assert bf._run(dry_run=False) == 0
    doc = json.loads(
        (ct_root / "ts_out" / "source.json").read_text(encoding="utf-8")
    )
    assert doc["attestation"] == "inferred"
    assert doc["evidence_scope"] == ["s0045", "s0899"]  # 非 []
    assert doc["generated_by"].startswith("backfill_source_json.py sha256=")
