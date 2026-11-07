"""StorageBackend / QueueBackend 接口抽象（proposal §9、M0 里程碑）。

默认实现：本地文件系统 + 进程内队列。MinIO / Redis / PG 为后置可替换 profile。
"""

from __future__ import annotations

import json
import queue
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

class StorageBackend(ABC):
    """存储抽象：按 case_id/series_id/ 目录组织（proposal §9 存储层）。"""

    @abstractmethod
    def save(self, key: str, data: bytes) -> Path:
        """写入对象，返回落盘路径。key 形如 'case-001/series-001/image.nii.gz'。"""

    @abstractmethod
    def load(self, key: str) -> bytes:
        """读取对象字节。不存在则 raise KeyError。"""

    @abstractmethod
    def exists(self, key: str) -> bool: ...

    @abstractmethod
    def delete(self, key: str) -> None: ...

    @abstractmethod
    def list_prefix(self, prefix: str) -> list[str]:
        """列出前缀下的所有 key。"""


class LocalFsStorage(StorageBackend):
    """本地文件系统实现。根目录 = {data_dir}/storage。"""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        # key 白名单校验：拒绝路径穿越
        if ".." in Path(key).parts or Path(key).is_absolute():
            raise ValueError(f"invalid storage key: {key!r}")
        return self.root / key

    def save(self, key: str, data: bytes) -> Path:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return p

    def load(self, key: str) -> bytes:
        p = self._path(key)
        if not p.exists():
            raise KeyError(key)
        return p.read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def delete(self, key: str) -> None:
        p = self._path(key)
        if p.exists():
            p.unlink()

    def list_prefix(self, prefix: str) -> list[str]:
        base = self.root / prefix if prefix else self.root
        if not base.exists():
            return []
        return sorted(
            str(p.relative_to(self.root)) for p in base.rglob("*") if p.is_file()
        )


# ---------------------------------------------------------------------------
# Queue
# ---------------------------------------------------------------------------

@dataclass
class Job:
    """流水线任务单元。stage 对应 proposal §6 的 8 阶段。"""

    job_id: str
    case_id: str
    stage: str
    payload: dict[str, Any] = field(default_factory=dict)


class QueueBackend(ABC):
    """队列抽象。SQLite+进程内队列是默认组合，PG 是可选 profile（GRILL-REVIEW 1.10）。"""

    @abstractmethod
    def enqueue(self, job: Job) -> None: ...

    @abstractmethod
    def dequeue(self, timeout: float | None = None) -> Job | None:
        """取出一个任务；超时返回 None。"""

    @abstractmethod
    def qsize(self) -> int: ...

    @abstractmethod
    def ack(self, job: Job) -> None:
        """确认完成。"""

    @abstractmethod
    def nack(self, job: Job, requeue: bool = True) -> None:
        """失败处理；requeue=True 则重新入队。"""


class InProcessQueue(QueueBackend):
    """进程内线程安全队列（单卡 16GB 本来就是串行，不需要分布式队列，proposal §9）。"""

    def __init__(self) -> None:
        self._q: queue.Queue[Job] = queue.Queue()
        self._pending: set[str] = set()
        self._lock = threading.Lock()

    def enqueue(self, job: Job) -> None:
        with self._lock:
            self._pending.add(job.job_id)
        self._q.put(job)

    def dequeue(self, timeout: float | None = None) -> Job | None:
        try:
            return self._q.get(timeout=timeout)
        except queue.Empty:
            return None

    def qsize(self) -> int:
        return self._q.qsize()

    def pending_ids(self) -> set[str]:
        """未完成 job_id 集合（监控端点/TUI 只读）。"""
        with self._lock:
            return set(self._pending)

    def ack(self, job: Job) -> None:
        with self._lock:
            self._pending.discard(job.job_id)

    def nack(self, job: Job, requeue: bool = True) -> None:
        if requeue:
            self._q.put(job)
        else:
            with self._lock:
                self._pending.discard(job.job_id)


class SqliteJobsQueue(QueueBackend):
    """SQLite jobs 表队列（M3，M2-REPORT 遗留 7：队列状态源落库）。

    M0 schema 的 jobs 表（db.SCHEMA）为真相源：job_id PK / case_id / stage /
    status（queued|running|done|failed）/ payload / created_at / updated_at。
    与 InProcessQueue 的差别：**跨进程可见**（FastAPI 与 CLI worker 各自进程
    都读写同一张表），重启不丢。

    dequeue：status=queued 最老一条置 running（单一写入纪律：写路径短事务）。
    ack/nack：置 done / failed（nack requeue=True 则回到 queued）。
    """

    def __init__(self, db_url: str) -> None:
        from . import db as _db

        self._db_url = db_url
        self._db = _db
        # 幂等建 jobs 表（init_db 兜底；CLI 直连路径无 lifespan）
        _db.init_db(db_url)

    def _connect(self):
        return self._db.connect(self._db_url)

    def enqueue(self, job: Job) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO jobs (job_id, case_id, stage, status, payload)"
                " VALUES (?, ?, ?, 'queued', ?)",
                (job.job_id, job.case_id, job.stage, json.dumps(job.payload, ensure_ascii=False)),
            )
            conn.commit()

    def dequeue(self, timeout: float | None = None) -> Job | None:
        import time as _time

        deadline = _time.monotonic() + (timeout or 0.0)
        while True:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT job_id, case_id, stage, payload FROM jobs"
                    " WHERE status='queued' ORDER BY created_at LIMIT 1"
                ).fetchone()
                if row is not None:
                    conn.execute(
                        "UPDATE jobs SET status='running', updated_at=datetime('now')"
                        " WHERE job_id=?",
                        (row["job_id"],),
                    )
                    conn.commit()
                    return Job(
                        job_id=row["job_id"], case_id=row["case_id"] or "",
                        stage=row["stage"],
                        payload=json.loads(row["payload"]) if row["payload"] else {},
                    )
            if timeout is None or _time.monotonic() >= deadline:
                return None
            _time.sleep(0.2)

    def qsize(self) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM jobs WHERE status='queued'"
            ).fetchone()
            return int(row["n"])

    def pending_ids(self) -> set[str]:
        """未完成 job_id 集合（queued + running；监控端点/TUI 只读）。"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT job_id FROM jobs WHERE status IN ('queued','running')"
            ).fetchall()
            return {r["job_id"] for r in rows}

    def ack(self, job: Job) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE jobs SET status='done', updated_at=datetime('now')"
                " WHERE job_id=?",
                (job.job_id,),
            )
            conn.commit()

    def nack(self, job: Job, requeue: bool = True) -> None:
        status = "queued" if requeue else "failed"
        with self._connect() as conn:
            conn.execute(
                "UPDATE jobs SET status=?, updated_at=datetime('now') WHERE job_id=?",
                (status, job.job_id),
            )
            conn.commit()

    def status_counts(self) -> dict[str, int]:
        """status 分布（监控端点/TUI 展示）。"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS n FROM jobs GROUP BY status"
            ).fetchall()
            return {r["status"]: int(r["n"]) for r in rows}
