"""StorageBackend / QueueBackend / config 负测试与行为测试（M0 CI）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from bodydonor.config import Settings, load_settings
from bodydonor.storage import InProcessQueue, Job, LocalFsStorage

# ---------------------------------------------------------------------------
# Settings / 配置化限制
# ---------------------------------------------------------------------------

def test_default_settings_on_prem_no_upload_limit() -> None:
    s = Settings()
    assert s.deployment_mode == "on_prem"
    assert s.max_upload_mb is None  # on_prem 不限上传


def test_cloud_demo_upload_limit() -> None:
    s = Settings(deployment_mode="cloud_demo")
    assert s.max_upload_mb == 100  # 仅 cloud_demo 有限制


def test_data_dir_defaults_to_home_native_fs() -> None:
    s = Settings()
    assert s.resolved_data_dir == Path.home() / "data" / "BodyDonor"


def test_data_dir_mnt_c_rejected() -> None:
    """数据目录禁 /mnt/c（9P 协议慢一个量级，proposal §10）。"""
    s = Settings(data_dir="/mnt/c/Users/foo/data")
    with pytest.raises(ValueError, match="/mnt/c"):
        _ = s.resolved_data_dir


def test_load_settings_from_project_config() -> None:
    s = load_settings()  # 项目内 config/settings.yaml
    assert s.deployment_mode in ("cloud_demo", "on_prem")
    # W1/W14：路由判定唯一来源是许可注册表（license_registry.check_task），
    # 配置项只作展示——blocked_tasks 一票否决在注册表层，不在配置
    assert "total" in s.teacher.allowed_tasks
    assert s.teacher.blocked_tasks == []  # R3 ④：brain_aneurysm 商用理由已删


# ---------------------------------------------------------------------------
# StorageBackend
# ---------------------------------------------------------------------------

def test_local_fs_storage_roundtrip(tmp_path: Path) -> None:
    st = LocalFsStorage(tmp_path / "storage")
    p = st.save("case-001/series-001/image.nii.gz", b"fake-nifti")
    assert p.exists()
    assert st.load("case-001/series-001/image.nii.gz") == b"fake-nifti"
    assert st.exists("case-001/series-001/image.nii.gz")
    assert st.list_prefix("case-001") == ["case-001/series-001/image.nii.gz"]
    st.delete("case-001/series-001/image.nii.gz")
    assert not st.exists("case-001/series-001/image.nii.gz")


def test_local_fs_storage_missing_key_raises(tmp_path: Path) -> None:
    st = LocalFsStorage(tmp_path / "storage")
    with pytest.raises(KeyError):
        st.load("no/such/key")


def test_local_fs_storage_rejects_path_traversal(tmp_path: Path) -> None:
    st = LocalFsStorage(tmp_path / "storage")
    with pytest.raises(ValueError, match="invalid storage key"):
        st.save("../../etc/passwd", b"x")
    with pytest.raises(ValueError, match="invalid storage key"):
        st.save("/absolute/path", b"x")


# ---------------------------------------------------------------------------
# QueueBackend
# ---------------------------------------------------------------------------

def test_inprocess_queue_enqueue_dequeue() -> None:
    q = InProcessQueue()
    assert q.qsize() == 0
    j1 = Job(job_id="c1:ingest", case_id="c1", stage="ingest")
    j2 = Job(job_id="c1:teacher", case_id="c1", stage="teacher_inference")
    q.enqueue(j1)
    q.enqueue(j2)
    assert q.qsize() == 2
    got = q.dequeue(timeout=0.1)
    assert got is not None and got.job_id == "c1:ingest"
    q.ack(got)
    got2 = q.dequeue(timeout=0.1)
    assert got2 is not None and got2.job_id == "c1:teacher"
    assert q.dequeue(timeout=0.05) is None


def test_inprocess_queue_nack_requeue() -> None:
    q = InProcessQueue()
    j = Job(job_id="c1:ingest", case_id="c1", stage="ingest")
    q.enqueue(j)
    got = q.dequeue(timeout=0.1)
    assert got is not None
    q.nack(got, requeue=True)
    assert q.qsize() == 1  # 重新入队


def test_inprocess_queue_nack_no_requeue() -> None:
    q = InProcessQueue()
    j = Job(job_id="c1:ingest", case_id="c1", stage="ingest")
    q.enqueue(j)
    got = q.dequeue(timeout=0.1)
    assert got is not None
    q.nack(got, requeue=False)
    assert q.qsize() == 0
