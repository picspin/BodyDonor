"""SqliteJobsQueue 单测（M3，M2-REPORT 遗留 7：队列状态源落 SQLite jobs 表）。"""

from __future__ import annotations

from bodydonor.storage import Job, SqliteJobsQueue


def test_enqueue_dequeue_fifo(tmp_path):
    q = SqliteJobsQueue(f"sqlite:///{tmp_path / 't.db'}")
    q.enqueue(Job(job_id="a:seg", case_id="a", stage="3_teacher_inference"))
    q.enqueue(Job(job_id="b:seg", case_id="b", stage="3_teacher_inference"))
    job = q.dequeue()
    assert job is not None
    assert job.job_id == "a:seg"
    # dequeue 后 size=1（b 还在 queued）
    assert q.qsize() == 1


def test_dequeued_job_is_running_not_queued(tmp_path):
    q = SqliteJobsQueue(f"sqlite:///{tmp_path / 't.db'}")
    q.enqueue(Job(job_id="a", case_id="a", stage="4_auto_qc"))
    q.dequeue()
    # running 不算 queued，但在 pending_ids 里
    assert q.qsize() == 0
    assert q.pending_ids() == {"a"}


def test_ack_marks_done(tmp_path):
    q = SqliteJobsQueue(f"sqlite:///{tmp_path / 't.db'}")
    q.enqueue(Job(job_id="a", case_id="a", stage="1_ingest"))
    job = q.dequeue()
    q.ack(job)
    assert q.pending_ids() == set()
    assert q.status_counts() == {"done": 1}


def test_nack_requeue_back_to_queued(tmp_path):
    q = SqliteJobsQueue(f"sqlite:///{tmp_path / 't.db'}")
    q.enqueue(Job(job_id="a", case_id="a", stage="4_auto_qc"))
    job = q.dequeue()
    q.nack(job, requeue=True)
    assert q.qsize() == 1


def test_nack_no_requeue_failed(tmp_path):
    q = SqliteJobsQueue(f"sqlite:///{tmp_path / 't.db'}")
    q.enqueue(Job(job_id="a", case_id="a", stage="4_auto_qc"))
    job = q.dequeue()
    q.nack(job, requeue=False)
    assert q.pending_ids() == set()
    assert q.status_counts() == {"failed": 1}


def test_payload_roundtrip(tmp_path):
    q = SqliteJobsQueue(f"sqlite:///{tmp_path / 't.db'}")
    q.enqueue(Job(job_id="a", case_id="a", stage="1_ingest", payload={"path": "/x/y", "n": 3}))
    job = q.dequeue()
    assert job.payload == {"path": "/x/y", "n": 3}


def test_restart_persistence(tmp_path):
    """跨进程/重启可见：新实例读同一张表（M3 落库的实质理由）。"""
    url = f"sqlite:///{tmp_path / 't.db'}"
    q1 = SqliteJobsQueue(url)
    q1.enqueue(Job(job_id="keep", case_id="a", stage="4_auto_qc"))
    q2 = SqliteJobsQueue(url)  # 模拟 worker 进程 / 重启
    assert q2.qsize() == 1
    assert q2.pending_ids() == {"keep"}


def test_dequeue_empty_returns_none(tmp_path):
    q = SqliteJobsQueue(f"sqlite:///{tmp_path / 't.db'}")
    assert q.dequeue() is None
