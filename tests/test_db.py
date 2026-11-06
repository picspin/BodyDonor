"""DB 纪律测试（M0-fix）：WAL/busy_timeout、7 核心实体 + License 实体、并发写、Alembic。"""

from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path

import pytest
from sqlalchemy import insert, select, text
from sqlalchemy.orm import Session

from bodydonor.db import BUSY_TIMEOUT_MS, _engine, connect, init_db, write_lock
from bodydonor.models import (
    Case,
    LicenseState,
    MaskVersion,
    ModelVersion,
    Project,
    QCReport,
    ReviewTask,
    Series,
    UsageLedger,
    new_id,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    url = f"sqlite:///{tmp_path / 'test.db'}"
    init_db(url)
    return url


@pytest.fixture
def engine(db_url: str):
    e = _engine(db_url)
    yield e
    e.dispose()


@pytest.fixture
def conn(engine):
    """ORM Session（实体读写）。"""
    s = Session(engine)
    yield s
    s.close()


# ---------------------------------------------------------------------------
# PRAGMA 纪律
# ---------------------------------------------------------------------------

def test_connect_sets_wal_and_busy_timeout(db_url: str) -> None:
    c = connect(db_url)
    try:
        assert c.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert c.execute("PRAGMA busy_timeout").fetchone()[0] == BUSY_TIMEOUT_MS
        assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        c.close()


def test_connect_rejects_non_sqlite() -> None:
    with pytest.raises(ValueError, match="sqlite"):
        connect("postgresql://localhost/bodydonor")


# ---------------------------------------------------------------------------
# 7 核心实体 + License 实体（关系链）
# ---------------------------------------------------------------------------

def test_full_entity_chain_roundtrip(conn) -> None:
    """Project ─< Case ─< Series ─< MaskVersion ─< QCReport ─< ReviewTask，
    ModelVersion ─< MaskVersion，LicenseState/UsageLedger 可写。"""
    project = Project(project_id="proj-1", name="demo")
    case = Case(case_id="case-1", project_id="proj-1", source="nifti")
    series = Series(series_id="series-1", case_id="case-1", modality="CT", num_slices=300)
    model = ModelVersion(
        model_version_id="mv-1", name="student-r1", gate_status="passed",
        gate_metrics={"mean_dice": 0.87, "per_class_dice": {"liver": 0.9}},
    )
    mask = MaskVersion(
        mask_version_id="mask-1", series_id="series-1", version=1,
        source="teacher", storage_key="case-1/series-1/mask-v1.nii.gz",
    )
    qc = QCReport(
        qc_report_id="qc-1", mask_version_id="mask-1", overall_score=0.92,
        flags=["volume_prior"], rule_scores={"volume": 0.95}, passed=True,
    )
    review = ReviewTask(
        review_task_id="rt-1", qc_report_id="qc-1", series_id="series-1",
        status="pending", assignee="reviewer-a",
    )
    for obj in (project, case, series, model, mask, qc, review):
        conn.add(obj)
    conn.commit()

    # 关系链回读
    s = conn.get(Series, "series-1")
    assert s.case.project.project_id == "proj-1"
    assert s.mask_versions[0].source == "teacher"
    assert s.mask_versions[0].model_version is None  # teacher 初版不挂学生模型
    assert s.mask_versions[0].qc_reports[0].overall_score == 0.92
    assert s.review_tasks[0].status == "pending"

    # ModelVersion 挂到 student mask 版本（proposal：学生输出只用于对比评估）
    student_mask = MaskVersion(
        mask_version_id="mask-2", series_id="series-1", version=2,
        source="student", model_version_id="mv-1",
        storage_key="case-1/series-1/mask-v2.nii.gz",
    )
    conn.add(student_mask)
    conn.commit()
    assert conn.get(MaskVersion, "mask-2").model_version.gate_status == "passed"

    # License 实体
    conn.execute(
        insert(LicenseState).values(
            fingerprint_hash="a" * 64, license_id=None,
            trial_yearly_limit=200, trial_monthly_limit=20,
        )
    )
    conn.execute(
        insert(UsageLedger).values(
            fingerprint_hash="a" * 64, license_id=None,
            period="2026-11", case_id="case-1",
        )
    )
    conn.commit()
    ls = conn.execute(select(LicenseState)).scalar_one()
    assert ls.trial_yearly_limit == 200
    assert ls.trial_monthly_limit == 20
    assert ls.trial_yearly_used == 0  # 试用计数缓存默认 0（真相源在 UsageLedger）
    assert ls.trial_monthly_used == 0
    assert conn.execute(select(UsageLedger)).scalar_one().period == "2026-11"


def test_mask_version_unique_series_version(conn) -> None:
    """同 Series 同版本号唯一（MaskVersion 语义约束）。"""
    conn.add(Case(case_id="c-9"))
    conn.add(Series(series_id="s-9", case_id="c-9"))
    conn.add(
        MaskVersion(
            mask_version_id="m-9a", series_id="s-9", version=1,
            source="teacher", storage_key="k1",
        )
    )
    conn.commit()
    conn.add(
        MaskVersion(
            mask_version_id="m-9b", series_id="s-9", version=1,
            source="teacher", storage_key="k2",
        )
    )
    # SQLite 报错为列名形式（不含命名约束 ID）：
    # UNIQUE constraint failed: mask_versions.series_id, ...
    with pytest.raises(Exception, match="UNIQUE constraint failed: mask_versions"):
        conn.commit()


def test_usage_ledger_unique_fp_period_case(conn) -> None:
    """同指纹同期间同 case 只记一行（幂等计数）。IntegrityError 在 execute 时抛出。"""
    conn.execute(
        insert(UsageLedger).values(
            fingerprint_hash="b" * 64, period="2026", case_id="c-1"
        )
    )
    conn.commit()
    # SQLite 报错为列名形式（不含命名约束 ID）
    with pytest.raises(Exception, match="UNIQUE constraint failed: usage_ledger"):
        conn.execute(
            insert(UsageLedger).values(
                fingerprint_hash="b" * 64, period="2026", case_id="c-1"
            )
        )
    conn.rollback()


def test_new_id_prefix() -> None:
    assert new_id("case").startswith("case-")
    assert new_id("case") != new_id("case")


# ---------------------------------------------------------------------------
# jobs 兜底表（M0 遗留表纳入 init_db）
# ---------------------------------------------------------------------------

def test_jobs_table_exists_after_init(db_url: str) -> None:
    c = connect(db_url)
    try:
        cols = {r[1] for r in c.execute("PRAGMA table_info(jobs)")}
        assert {"job_id", "case_id", "stage", "status"} <= cols
    finally:
        c.close()


# ---------------------------------------------------------------------------
# 并发纪律（M0-fix 核心）：三方同时写不抛 database is locked
# ---------------------------------------------------------------------------

def test_write_lock_is_single_path() -> None:
    """write_lock 可重入持有（纪律 2：单一写入路径的进程内互斥）。"""
    with write_lock, write_lock:  # RLock 语义：同线程可重入
        pass


def test_concurrent_writers_no_locked(tmp_path: Path) -> None:
    """模拟三方并发写（FastAPI 请求 / CLI 批量导入 / Worker 状态更新）：
    各自持 write_lock 串行提交，断言无 'database is locked'、无数据丢失。

    这条测试锁定 M0-fix 的单一写入纪律；若未来有人绕过 write_lock 并发写，
    本测试可能（非确定性）失败——这正是纪律的执行提示。
    """
    url = f"sqlite:///{tmp_path / 'conc.db'}"
    init_db(url)
    errors: list[str] = []
    eng = _engine(url)

    def api_writer(tag: str) -> None:
        s = Session(eng)
        try:
            for i in range(20):
                with write_lock:
                    s.add(Project(project_id=f"api-{tag}-{i}", name="api"))
                    s.commit()
        except Exception as e:  # noqa: BLE001
            errors.append(f"api: {e}")
        finally:
            s.close()

    def cli_writer(tag: str) -> None:
        s = Session(eng)
        try:
            for i in range(20):
                with write_lock:
                    s.add(Case(case_id=f"cli-{tag}-{i}", source="cli-import"))
                    s.commit()
        except Exception as e:  # noqa: BLE001
            errors.append(f"cli: {e}")
        finally:
            s.close()

    def worker_writer(tag: str) -> None:
        s = Session(eng)
        try:
            for i in range(20):
                with write_lock:
                    s.add(
                        ModelVersion(
                            model_version_id=f"mv-{tag}-{i}", name="worker",
                            gate_status="pending",
                        )
                    )
                    s.commit()
        except Exception as e:  # noqa: BLE001
            errors.append(f"worker: {e}")
        finally:
            s.close()

    threads = [
        threading.Thread(target=api_writer, args=("a",)),
        threading.Thread(target=cli_writer, args=("b",)),
        threading.Thread(target=worker_writer, args=("c",)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert errors == [], f"并发写失败: {errors}"
    with eng.connect() as c:
        n_projects = c.execute(text("SELECT COUNT(*) FROM projects")).scalar_one()
        n_cases = c.execute(text("SELECT COUNT(*) FROM cases")).scalar_one()
        n_models = c.execute(text("SELECT COUNT(*) FROM model_versions")).scalar_one()
    eng.dispose()
    assert n_projects == 20
    assert n_cases == 20
    assert n_models == 20


def test_concurrent_writers_busy_timeout_holds(tmp_path: Path) -> None:
    """无 write_lock 的裸并发写（纪律被绕过的场景）也应由 busy_timeout=30s 兜底：
    短事务下 busy_timeout 足以消化竞争，不抛 database is locked。"""
    url = f"sqlite:///{tmp_path / 'conc2.db'}"
    init_db(url)
    errors: list[str] = []

    def writer(tag: str) -> None:
        c = connect(url)
        try:
            for i in range(30):
                c.execute(
                    "INSERT INTO projects (project_id, name, created_at)"
                    " VALUES (?, ?, datetime('now'))",
                    (f"p-{tag}-{i}", "x"),
                )
                c.commit()
        except Exception as e:  # noqa: BLE001
            errors.append(f"{tag}: {e}")
        finally:
            c.close()

    threads = [threading.Thread(target=writer, args=(t,)) for t in ("x", "y", "z")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert errors == [], f"busy_timeout 兜底失败: {errors}"
    with _engine(url).connect() as c:
        n = c.execute(text("SELECT COUNT(*) FROM projects")).scalar_one()
    assert n == 90


# ---------------------------------------------------------------------------
# Alembic 迁移（subprocess 隔离运行，验证全新库 upgrade/downgrade）
# ---------------------------------------------------------------------------

def _alembic(args: list[str], db_file: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable, "-m", "alembic",
            "-x", f"db_url=sqlite:///{db_file}",
            *args,
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        env={
            "PATH": "/usr/bin:/bin",
            "VIRTUAL_ENV": str(PROJECT_ROOT / ".venv"),
        },
    )


def test_alembic_upgrade_head_on_fresh_db(tmp_path: Path) -> None:
    db_file = tmp_path / "mig.db"
    r = _alembic(["upgrade", "head"], db_file)
    assert r.returncode == 0, f"upgrade head 失败:\n{r.stdout}\n{r.stderr}"
    c = connect(f"sqlite:///{db_file}")
    try:
        tables = {
            row[0]
            for row in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    finally:
        c.close()
    expected = {
        "projects", "cases", "series", "mask_versions", "qc_reports",
        "review_tasks", "model_versions", "license_state", "usage_ledger",
    }
    assert expected <= tables, f"缺表: {expected - tables}"


def test_alembic_downgrade_up_roundtrip(tmp_path: Path) -> None:
    db_file = tmp_path / "mig2.db"
    assert _alembic(["upgrade", "head"], db_file).returncode == 0
    r_down = _alembic(["downgrade", "base"], db_file)
    assert r_down.returncode == 0, f"downgrade 失败:\n{r_down.stdout}\n{r_down.stderr}"
    c = connect(f"sqlite:///{db_file}")
    try:
        tables = {
            row[0]
            for row in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    finally:
        c.close()
    assert not (tables & {"projects", "cases", "series", "mask_versions"})
    r_up = _alembic(["upgrade", "head"], db_file)
    assert r_up.returncode == 0, f"re-upgrade 失败:\n{r_up.stdout}\n{r_up.stderr}"
