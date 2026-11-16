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
    """117 类全量 ts_out（空占位 + 15 蒸馏类），aorta mtime 设为指定时刻。"""
    teacher_out.mkdir(parents=True, exist_ok=True)
    names = [f"cls_{i:03d}" for i in range(102)] + sorted(DISTILL_CLASSES.values())
    assert len(names) == 117
    for n in names:
        (teacher_out / f"{n}.nii.gz").write_bytes(b"")
    ts = _epoch(mtime)
    import os

    os.utime(teacher_out / "aorta.nii.gz", (ts, ts))


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
    rc = bf.main() if False else None  # main() 读 sys.argv，走 helper
    rc = bf._run(dry_run=True)
    assert rc == 0
    assert not (amos_dir / "amos_0004" / "source.json").exists()
    assert not (ct_root / "ts_out" / "source.json").exists()
    out = capsys.readouterr().out
    assert "dry-run" in out


def test_main_writes_amos_per_case_and_ct_lite_library_level(fake_data, capsys):
    """写入位置契约：AMOS {case}/source.json 每目录一份；ct_lite ts_out/
    source.json 一库一份——read_teacher_provenance 读回 provenance_missing=False。"""
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
        "provenance_missing"
    ] is False
    assert read_teacher_provenance(ct_root / "ts_out" / "s0899")[
        "provenance_missing"
    ] is False


def test_main_idempotent_skips_existing(fake_data):
    """已有 source.json 不重做（幂等：第二次跑 0 写入）。"""
    amos_dir, ct_root = fake_data
    assert bf._run(dry_run=False) == 0
    before = (amos_dir / "amos_0004" / "source.json").read_text(encoding="utf-8")
    assert bf._run(dry_run=False) == 0
    assert (amos_dir / "amos_0004" / "source.json").read_text(encoding="utf-8") == before


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
