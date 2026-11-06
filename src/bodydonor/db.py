"""SQLite（默认）数据库：连接、PRAGMA、实体建表。PG 为可选 profile（docker-compose 只含 PG）。

## 单一写入纪律（SQLite 并发纪律，M0-fix）

SQLite + InProcessQueue 组合下，M1/M2 有三方并发写：FastAPI 请求、CLI 批量导入、
后台 Worker 更新任务状态。默认配置极易抛 `database is locked`。纪律：

1. **WAL 模式 + busy_timeout 30s**：所有连接建立时必须经 `connect()`，由它统一设置
   `journal_mode=WAL` 与 `busy_timeout=30000`，不要裸 `sqlite3.connect()`。
2. **单一写入路径**：所有写操作经队列串行化（后台 Worker 单线程消费）/或经一个进程内
   写锁（`write_lock`），不要开多个连接并发写。WAL 只允许「单写多读」——并发读可以，
   并发写仍然互斥，靠 busy_timeout 只是兜底而非设计。
3. **写事务要短**：写事务里不要做推理、IO 等长操作，攒批后短事务提交。
4. **PG 仍为可选 profile**：多进程 Worker 部署时再切（proposal §13）。

测试 `tests/test_db.py::test_concurrent_writers_no_locked` 锁定该纪律。
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from sqlalchemy import create_engine, event, text

from .models import Base

BUSY_TIMEOUT_MS = 30_000  # 30s，M0-fix：database is locked 兜底

# 进程内写锁：单一写入路径（纪律 2）。同进程内所有写操作共享此锁；
# RLock 语义：init_db 等嵌套获取不死锁（同线程可重入）。
# 跨进程并发写由 WAL + busy_timeout 兜底（M1 多进程 worker 时再评估切 PG）。
write_lock = threading.RLock()

# M0 旧版 SCHEMA 保留：cases / series 已纳入实体体系（models.Case/Series）。
# jobs 保留为通用流水线任务表（对应 proposal §6 的 8 阶段触发/状态），
# 无 ORM 实体（队列状态走 InProcessQueue，落库在 M1）——init_db 兜底创建。
# 此常量仅供外部工具参考；Alembic 初始迁移不含 jobs。
SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id      TEXT PRIMARY KEY,
    case_id     TEXT,
    stage       TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'queued',
    payload     TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT
);
"""


def connect(db_url: str) -> sqlite3.Connection:
    """db_url 形如 sqlite:///./bodydonor.db。仅支持 SQLite（PG 后置）。

    统一设置 WAL + busy_timeout（单一写入纪律 1）。裸 sqlite3.connect() 绕过
    本函数会导致 busy_timeout 缺失，并发写时直接抛 database is locked。
    """
    if not db_url.startswith("sqlite:///"):
        raise ValueError(f"M0 仅支持 sqlite:///，收到: {db_url}")
    path = db_url.removeprefix("sqlite:///")
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p, timeout=BUSY_TIMEOUT_MS / 1000)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _engine(db_url: str):
    """SQLAlchemy Engine，URL 解析与 PRAGMA 纪律复用 connect()（event 监听）。"""
    if not db_url.startswith("sqlite:///"):
        raise ValueError(f"M0 仅支持 sqlite:///，收到: {db_url}")
    path = db_url.removeprefix("sqlite:///")
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{p}", connect_args={"timeout": BUSY_TIMEOUT_MS / 1000})
    event.listen(engine, "connect", _set_sqlite_pragma)
    return engine


def _set_sqlite_pragma(dbapi_connection, _connection_record) -> None:
    """与 connect() 同一纪律：WAL + busy_timeout + FK。"""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def init_db(db_url: str) -> None:
    """建全部实体表 + jobs 兜底表（幂等）。

    正式部署走 Alembic 迁移（`alembic upgrade head`）；本函数用于测试与
    FastAPI lifespan 快速启动。jobs 表不在迁移内（无 ORM 实体，M1 落库）。
    """
    engine = _engine(db_url)
    try:
        with write_lock:
            Base.metadata.create_all(engine)
            with engine.connect() as conn:
                conn.execute(text(SCHEMA))
                conn.commit()
    finally:
        engine.dispose()
