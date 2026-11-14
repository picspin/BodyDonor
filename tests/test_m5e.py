"""M5-e 血缘修正测试：列宽 String(32) + provenance 列 + 迁移 + 分别统计 + 门禁。

M5-PLAN §4/§5 M5-e：MaskVersion.source 从 human 改为 nninteractive_guided
（20 字符）；legacy 别名 manual-correction-nninteractive / nninteractive
在门禁里保持拒绝；两类修正可分别统计；prompts 可重放。
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from bodydonor.db import _engine
from bodydonor.models import Base, Case, MaskVersion, Series

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_maskversion_source_column_width() -> None:
    """模型列宽：String(32)，装得下 20 字符的 nninteractive_guided。"""
    col = MaskVersion.__table__.c.source
    assert col.type.length == 32
    assert len("nninteractive_guided") == 20
    assert "provenance" in MaskVersion.__table__.c


def test_alembic_upgrade_head_on_fresh_db(tmp_path: Path) -> None:
    """迁移实测：空库 upgrade head → M0 + M5-e + M0-stale 补列；source
    VARCHAR(32)，provenance 列存在；series.source_instances_json 列存在；
    可插入 20 字符来源行并读回 provenance。"""
    db = tmp_path / "mig.db"
    url = f"sqlite:///{db}"
    r = subprocess.run(
        [sys.executable, "-m", "alembic", "-x", f"db_url={url}", "upgrade", "head"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert r.returncode == 0, f"alembic upgrade 失败：{r.stdout[-500:]}{r.stderr[-500:]}"
    c = sqlite3.connect(db)
    # head = f8a3b9c2d1e4（M7-S3 decision_traces 增 6 列，2026-11-15 加到链尾；
    # M6-f a4f9e2c81d03 仍是链中段，不回退）。head 断言只校验链尾后缀，
    # 不 hardcode 完整历史（M6-f 起新表迁移加链尾，断言跟随 head 更新）。
    assert c.execute("SELECT version_num FROM alembic_version").fetchone() == (
        "f8a3b9c2d1e4",
    )
    # M6-f：decision_traces 表存在（head 迁移语义实证）
    trace_cols = {row[1] for row in c.execute("PRAGMA table_info(decision_traces)")}
    assert "trace_id" in trace_cols and "human_outcome" in trace_cols
    cols = {row[1]: row[2] for row in c.execute("PRAGMA table_info(mask_versions)")}
    assert cols["source"] == "VARCHAR(32)"
    assert "provenance" in cols
    series_cols = {row[1] for row in c.execute("PRAGMA table_info(series)")}
    assert "source_instances_json" in series_cols  # M0-stale 补列
    assert c.execute("PRAGMA foreign_key_check").fetchall() == []
    c.close()

    engine = _engine(url)
    try:
        with Session(engine) as s:
            # MaskVersion.series_id 有 FK：先建 Case + Series
            s.add(Case(case_id="c"))
            s.add(Series(series_id="s", case_id="c"))
            s.commit()
            s.add(
                MaskVersion(
                    mask_version_id="mv-m5e-test",
                    series_id="s",
                    version=1,
                    source="nninteractive_guided",
                    storage_key="k",
                    provenance={
                        "sessionId": "sess-1",
                        "baselineTeacherVersion": None,
                        "prompts": [
                            {
                                "type": "point",
                                "x": 1,
                                "y": 2,
                                "z": 3,
                                "include": True,
                                "class": "liver",
                            }
                        ],
                        "acceptedClasses": ["liver"],
                    },
                )
            )
            s.commit()
            got = s.get(MaskVersion, "mv-m5e-test")
            assert got is not None and got.source == "nninteractive_guided"
            assert got.provenance["prompts"][0]["x"] == 1  # prompts 可重放
            assert got.provenance["acceptedClasses"] == ["liver"]
    finally:
        engine.dispose()


def test_correction_sources_counted_separately(tmp_path: Path) -> None:
    """两类修正可分别统计：human vs nninteractive_guided 按 source 分组计数。"""
    url = f"sqlite:///{tmp_path}/stats.db"
    engine = _engine(url)
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as s:
            s.add(Case(case_id="c"))
            s.add(Series(series_id="s", case_id="c"))
            s.commit()
            for i, src in enumerate(
                ["teacher", "human", "human", "nninteractive_guided", "manual"]
            ):
                s.add(
                    MaskVersion(
                        mask_version_id=f"mv-{i}",
                        series_id="s",
                        version=i + 1,
                        source=src,
                        storage_key=f"k{i}",
                    )
                )
            s.commit()
            counts = dict(
                s.execute(
                    select(MaskVersion.source, func.count()).group_by(MaskVersion.source)
                ).all()
            )
        assert counts == {
            "teacher": 1,
            "human": 2,
            "nninteractive_guided": 1,
            "manual": 1,
        }
    finally:
        engine.dispose()


def test_distill_gate_still_rejects_nninteractive_and_aliases() -> None:
    """门禁不倒退：规范名 + legacy 别名一律拒绝；纯画笔 human 允许。"""
    from bodydonor.distill_data import check_distill_source
    from bodydonor.ontology import mask_allowed_in_distill_training

    for src in (
        "nninteractive_guided",
        "manual-correction-nninteractive",
        "nninteractive",
    ):
        ok, _ = check_distill_source(src)
        assert ok is False, src
        ok2, _ = mask_allowed_in_distill_training({"experimental": False, "source": src})
        assert ok2 is False, src
    ok, _ = mask_allowed_in_distill_training({"experimental": False, "source": "human"})
    assert ok is True  # 纯画笔修正可进蒸馏（LICENSES §8.1 商用安全路径）
    ok, _ = check_distill_source("manual-correction")
    assert ok is True


def test_provenance_json_round_trip_in_sidecar_shape(tmp_path: Path) -> None:
    """provenance 结构与 sidecar 同形：M4 sidecar 的 provenance 可直接存列。"""
    from bodydonor.review_session import build_provenance

    prov = build_provenance(
        "sess-9",
        3,
        [{"type": "point", "x": 1, "y": 2, "z": 3, "include": True, "class": "liver"}],
        ["liver"],
    )
    blob = json.dumps(prov, ensure_ascii=False)
    assert json.loads(blob)["sessionId"] == "sess-9"
    assert json.loads(blob)["prompts"][0]["include"] is True
