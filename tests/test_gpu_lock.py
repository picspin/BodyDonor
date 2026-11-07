"""M2-align 3.3：GPU 文件锁测试（互斥 / 排队超时 / stale 回收 / 交互 fast-fail / 空闲释放）。"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from bodydonor.gpu_lock import (
    DEFAULT_IDLE_SECONDS,
    GpuLock,
    acquire,
    lock_status,
    recover_stale_lock,
    try_acquire,
)


@pytest.fixture
def lock_path(tmp_path: Path) -> Path:
    return tmp_path / "data" / "BodyDonor" / ".gpu.lock"


class TestMutex:
    def test_two_holders_mutex(self, lock_path: Path) -> None:
        """两持有者互斥：第一个 acquire 成功，第二个 try_acquire 立即失败。"""
        lock1 = try_acquire(lock_path, reason="batch", task="total", job_id="j1")
        assert lock1 is not None
        lock2 = try_acquire(lock_path, reason="batch", task="total", job_id="j2")
        assert lock2 is None
        lock1.release()
        # 释放后可再抢
        lock3 = try_acquire(lock_path, reason="batch", task="total", job_id="j3")
        assert lock3 is not None
        lock3.release()

    def test_batch_waits_for_release(self, lock_path: Path) -> None:
        """批量推理排队：被占时等待，持有者释放后拿到。"""
        lock1 = try_acquire(lock_path, reason="batch", task="total", job_id="j1")
        assert lock1 is not None

        import threading

        def release_soon():
            time.sleep(0.3)
            lock1.release()

        t = threading.Thread(target=release_soon)
        t.start()
        t0 = time.monotonic()
        lock2 = acquire(lock_path, reason="batch", task="total", job_id="j2", wait=5.0)
        elapsed = time.monotonic() - t0
        t.join()
        assert lock2 is not None
        assert elapsed >= 0.2  # 确实等了
        lock2.release()

    def test_batch_wait_timeout(self, lock_path: Path) -> None:
        """批量推理排队超时：超时报清晰错误（谁在持有）。"""
        lock1 = try_acquire(lock_path, reason="batch", task="total", job_id="j1")
        assert lock1 is not None
        with pytest.raises(TimeoutError, match="GPU 锁等待超时"):
            acquire(lock_path, reason="batch", task="total", job_id="j2", wait=0.2)
        lock1.release()


class TestInteractiveFastFail:
    def test_interactive_fast_fail_no_queue(self, lock_path: Path) -> None:
        """交互会话：获取不到锁立即失败返回 None（不排队），错误信息明确。"""
        lock1 = try_acquire(lock_path, reason="batch", task="total", job_id="j1")
        assert lock1 is not None
        t0 = time.monotonic()
        lock2 = acquire(lock_path, reason="interactive", task="nninteractive", job_id="j2", wait=0)
        elapsed = time.monotonic() - t0
        assert lock2 is None  # 立即失败
        assert elapsed < 0.5  # 没排队
        # 锁文件里能查到持有者身份（"有批量推理任务在跑"的依据）
        st = lock_status(lock_path)
        assert st["held"] is True
        assert st["info"]["task"] == "total"
        lock1.release()

    def test_interactive_acquire_when_free(self, lock_path: Path) -> None:
        """空闲时交互会话可获取。"""
        lock = acquire(lock_path, reason="interactive", task="review", job_id="j1", wait=0)
        assert lock is not None
        assert lock.info.reason == "interactive"
        lock.release()


class TestStaleLock:
    def test_stale_lock_recovered(self, lock_path: Path) -> None:
        """stale 锁回收：持有进程已死则可回收（pid 存活检查），回收记日志。"""
        # 模拟 stale：锁文件里的 pid 是个已死进程
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(10)"]
        )
        pid = proc.pid
        proc.kill()
        proc.wait()
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        import json

        lock_path.write_text(
            json.dumps(
                {
                    "pid": pid,
                    "reason": "batch",
                    "task": "total",
                    "job_id": "dead",
                    "acquired_at": time.time(),
                }
            )
        )
        # 死进程 pid 仍可被复用——用 killed 且 wait 的进程，os.kill(pid,0) 应抛 ProcessLookupError
        recovered = recover_stale_lock(lock_path)
        assert recovered is True
        assert not lock_path.exists()
        # 回收后可抢
        lock = try_acquire(lock_path, reason="batch", task="total", job_id="fresh")
        assert lock is not None
        lock.release()

    def test_live_lock_not_recovered(self, lock_path: Path) -> None:
        """存活持有者的锁不被回收。"""
        lock = try_acquire(lock_path, reason="batch", task="total", job_id="live")
        assert lock is not None
        assert recover_stale_lock(lock_path) is False
        assert lock_path.exists()
        lock.release()

    def test_corrupt_lock_recovered(self, lock_path: Path) -> None:
        """损坏锁文件（空/坏 JSON）回收。"""
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text("")
        assert recover_stale_lock(lock_path) is True
        lock_path.write_text("{bad json")
        assert recover_stale_lock(lock_path) is True


class TestLockInfo:
    def test_lock_info_written(self, lock_path: Path) -> None:
        """持有者身份（pid + 任务名 + job_id）写进锁文件。"""
        lock = try_acquire(lock_path, reason="batch", task="total", job_id="j1")
        assert lock is not None
        import json

        info = json.loads(lock_path.read_text(encoding="utf-8"))
        assert info["pid"] == os.getpid()
        assert info["task"] == "total"
        assert info["job_id"] == "j1"
        assert info["reason"] == "batch"
        lock.release()

    def test_context_manager_releases(self, lock_path: Path) -> None:
        """with 退出即释放。"""
        with try_acquire(lock_path, reason="batch", task="total", job_id="j1") as lock:
            assert isinstance(lock, GpuLock)
            assert lock_path.exists()
        assert not lock_path.exists()

    def test_release_idempotent(self, lock_path: Path) -> None:
        """释放幂等。"""
        lock = try_acquire(lock_path, reason="batch", task="total", job_id="j1")
        lock.release()
        lock.release()
        assert not lock_path.exists()


class TestReleaseOnIdle:
    def test_release_on_save_signal(self, lock_path: Path) -> None:
        """交互持有者收到"保存修正"信号时立即释放。"""
        lock = acquire(lock_path, reason="interactive", task="nninteractive", job_id="j1", wait=0)
        assert lock is not None
        released = lock.release_on_idle(idle_seconds=DEFAULT_IDLE_SECONDS, on_save=lambda: True)
        assert released is True
        assert not lock_path.exists()

    def test_release_on_idle_timeout(self, lock_path: Path) -> None:
        """交互持有者闲置超时释放（acquired_at 起算）。"""
        lock = acquire(lock_path, reason="interactive", task="nninteractive", job_id="j1", wait=0)
        assert lock is not None
        # 把 acquired_at 改到 11 分钟前（模拟闲置 > 10 分钟）
        import json

        info = json.loads(lock_path.read_text(encoding="utf-8"))
        info["acquired_at"] = time.time() - 660
        lock_path.write_text(json.dumps(info))
        released = lock.release_on_idle(idle_seconds=600, on_save=lambda: False)
        assert released is True
        assert not lock_path.exists()

    def test_no_release_when_active(self, lock_path: Path) -> None:
        """未闲置、未收到保存信号 -> 仍持锁。"""
        lock = acquire(lock_path, reason="interactive", task="nninteractive", job_id="j1", wait=0)
        assert lock is not None
        released = lock.release_on_idle(idle_seconds=600, on_save=lambda: False)
        assert released is False
        assert lock_path.exists()
        lock.release()

    def test_idle_defaults_documented(self) -> None:
        """默认闲置 10 分钟（M2-align 3.3 约定）。"""
        assert DEFAULT_IDLE_SECONDS == 600
