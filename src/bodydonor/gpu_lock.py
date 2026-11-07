"""GPU 文件锁（M2-align 3.3）：开发机单卡（Quadro RTX 5000）互斥。

机制：

- **文件锁**：锁文件放数据目录（`~/data/BodyDonor/.gpu.lock`），不放 /tmp
  （内存盘不跨进程、且数据目录是 WSL2 原生盘）。`O_CREAT|O_EXCL` 原子创建
  即抢锁；持有者身份（pid + 任务名 + job_id + 模式）写进锁文件。
- **互斥**：同一时间只有一个持有者。批量推理（TS worker）`acquire(wait=...)`
  排队等待（轮询 + 超时）；交互会话（nnInteractive / 审阅会话加载权重）
  `acquire(wait=0)` 立即失败返回（不排队），明确报"有批量推理任务在跑"。
- **stale 锁回收**：持有进程已死（pid 存活检查）则可回收，回收记日志。
- **空闲释放**：交互持有者 `release_on_idle(idle_seconds=600, on_save=None)`
  ——闲置 10 分钟或收到"保存修正"信号时立即释放（M2 落机制 + 单测 + 文档；
  nnInteractive 本体是 M4 的事，不在本轮接它）。

使用约定（写进 M2-ALIGN-REPORT）：

    with gpu_lock.acquire(reason="batch", task="total", job_id=job_id,
                          settings=settings, wait=1800) as lock:
        ...  # 推理全程持锁，with 退出即释放

    lock = gpu_lock.acquire(reason="interactive", task="nninteractive", ...)
    if lock is None:
        ...  # 立即失败："有批量推理任务在跑"
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# 交互会话空闲释放默认值（M2-align 3.3：闲置 10 分钟释放）
DEFAULT_IDLE_SECONDS = 600

# 批量推理默认排队等待上限（秒）：TS 一例 ~2min，留足余量
DEFAULT_BATCH_WAIT_SECONDS = 3600


class GpuLockError(Exception):
    """GPU 锁异常（路径非法、锁文件损坏等）。"""


@dataclass
class LockInfo:
    """锁文件内容（持有者身份）。"""

    pid: int
    reason: str  # batch | interactive
    task: str  # total | nninteractive | review ...
    job_id: str
    acquired_at: float  # epoch 秒

    def to_dict(self) -> dict[str, Any]:
        return {
            "pid": self.pid,
            "reason": self.reason,
            "task": self.task,
            "job_id": self.job_id,
            "acquired_at": self.acquired_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> LockInfo:
        return cls(
            pid=int(d["pid"]),
            reason=str(d.get("reason", "")),
            task=str(d.get("task", "")),
            job_id=str(d.get("job_id", "")),
            acquired_at=float(d.get("acquired_at", 0.0)),
        )


def _pid_alive(pid: int) -> bool:
    """pid 存活检查（os.kill(pid, 0)：存在且有权发信号则不 raise）。"""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # 存在但属他人（容器场景）
    except OSError:
        return True  # 保守：检查异常时不回收
    return True


def _read_lock(path: Path) -> LockInfo | None:
    """读锁文件；损坏（空/坏 JSON）返回 None（视为可回收）。"""
    try:
        raw = path.read_text(encoding="utf-8")
        if not raw.strip():
            return None
        return LockInfo.from_dict(json.loads(raw))
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


def _write_lock(path: Path, info: LockInfo) -> None:
    """原子写锁文件（写临时文件 + rename，防半写）。"""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(info.to_dict(), ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def recover_stale_lock(path: Path) -> bool:
    """stale 锁回收：持有进程已死则删除锁文件。回收了返回 True。

    锁文件损坏（读不出 LockInfo）也视为 stale（可回收）。
    """
    info = _read_lock(path)
    if info is None:
        # 空文件 / 损坏：回收（记日志）
        if path.exists():
            path.unlink(missing_ok=True)
            _log(f"[gpu_lock] 回收损坏锁文件：{path}")
            return True
        return False
    if not _pid_alive(info.pid):
        path.unlink(missing_ok=True)
        _log(
            f"[gpu_lock] 回收 stale 锁：持有者 pid={info.pid} 已死"
            f"（task={info.task} job={info.job_id}）"
        )
        return True
    return False


def _log(msg: str) -> None:
    print(msg, file=os.sys.stderr, flush=True)


@dataclass
class GpuLock:
    """一次持锁会话（with 上下文；交互会话可手动 release / release_on_idle）。"""

    path: Path
    info: LockInfo
    released: bool = False

    def release(self) -> None:
        """立即释放（幂等；只有持有者本人能释放）。"""
        if self.released:
            return
        self.released = True
        current = _read_lock(self.path)
        if current is not None and current.pid == self.info.pid:
            self.path.unlink(missing_ok=True)
            _log(
                f"[gpu_lock] 释放：pid={self.info.pid} task={self.info.task} "
                f"job={self.info.job_id}"
            )

    def release_on_idle(
        self,
        idle_seconds: int = DEFAULT_IDLE_SECONDS,
        on_save: Any = None,
    ) -> bool:
        """交互会话空闲释放：闲置 idle_seconds 或收到"保存修正"信号时立即释放。

        - on_save 信号：调用方传入的可调用/事件回调，返回 truthy 即视为
          "保存修正"信号（立即释放）。M2 只定义约定与机制，nnInteractive
          本体是 M4 的事（本轮不接）。
        - 返回 True = 已释放；False = 仍持锁（未闲置、未收到保存信号）。

        用法（交互会话主循环里周期调）：

            if lock.release_on_idle(idle_seconds=600, on_save=save_flag):
                break  # 显存已释放
        """
        if self.released:
            return True
        # 1. 保存修正信号：立即释放
        if on_save is not None:
            try:
                save_signal = on_save() if callable(on_save) else bool(on_save)
            except Exception:  # noqa: BLE001 — 信号回调异常不挡释放判定
                save_signal = False
            if save_signal:
                self.release()
                _log("[gpu_lock] 交互会话释放（收到保存修正信号）")
                return True
        # 2. 闲置超时：立即释放（闲置时长从锁文件读——外部也可更新 acquired_at）
        current = _read_lock(self.path)
        acquired_at = current.acquired_at if current is not None else self.info.acquired_at
        idle_for = time.time() - acquired_at
        if idle_for >= idle_seconds:
            self.release()
            _log(
                f"[gpu_lock] 交互会话释放（闲置 {idle_for:.0f}s >= {idle_seconds}s）"
            )
            return True
        return False

    def __enter__(self) -> GpuLock:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.release()


def try_acquire(
    path: Path,
    reason: str = "batch",
    task: str = "",
    job_id: str = "",
) -> GpuLock | None:
    """尝试立即抢锁（O_CREAT|O_EXCL 原子创建）。抢到返回 GpuLock，否则 None。

    抢锁前先做 stale 回收（持有进程已死则先回收再抢）。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    # stale 回收（别人进程死了 -> 锁可回收）
    recover_stale_lock(path)
    info = LockInfo(
        pid=os.getpid(),
        reason=reason,
        task=task,
        job_id=job_id,
        acquired_at=time.time(),
    )
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return None
    except OSError as e:
        raise GpuLockError(f"锁文件创建失败：{e}") from e
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(info.to_dict(), f, ensure_ascii=False)
    _log(f"[gpu_lock] 获得：pid={info.pid} reason={reason} task={task} job={job_id}")
    return GpuLock(path=path, info=info)


def acquire(
    path: Path,
    reason: str = "batch",
    task: str = "",
    job_id: str = "",
    wait: float = DEFAULT_BATCH_WAIT_SECONDS,
    poll_interval: float = 2.0,
) -> GpuLock | None:
    """抢锁：批量推理（reason=batch）排队等待（wait 秒超时报错）；交互会话
    （reason=interactive）wait=0 立即失败返回 None（不排队）。

    返回：
    - GpuLock：抢到
    - None：交互 fast-fail（wait=0 且被占）

    超时（batch）：raise TimeoutError（带清晰错误：谁在持有、多久了）。
    """
    if reason == "interactive" and wait == 0:
        lock = try_acquire(path, reason=reason, task=task, job_id=job_id)
        if lock is None:
            current = _read_lock(path)
            holder = (
                f"pid={current.pid} task={current.task} job={current.job_id}"
                if current
                else "未知持有者"
            )
            _log(f"[gpu_lock] 交互会话 fast-fail：有批量推理任务在跑（{holder}）")
        return lock

    deadline = time.monotonic() + wait
    while True:
        lock = try_acquire(path, reason=reason, task=task, job_id=job_id)
        if lock is not None:
            return lock
        if wait <= 0 or time.monotonic() >= deadline:
            current = _read_lock(path)
            holder = (
                f"pid={current.pid} task={current.task} job={current.job_id} "
                f"held_for={time.time() - current.acquired_at:.0f}s"
                if current
                else "未知持有者"
            )
            raise TimeoutError(
                f"GPU 锁等待超时（wait={wait}s）：有批量推理任务在跑（{holder}）"
            )
        time.sleep(poll_interval)


def lock_status(path: Path) -> dict[str, Any]:
    """锁状态查看（TUI/监控用）：held=是否被占，info=持有者身份。"""
    if not path.exists():
        return {"held": False, "info": None}
    info = _read_lock(path)
    if info is None:
        return {"held": False, "info": None, "stale_or_corrupt": True}
    return {
        "held": _pid_alive(info.pid),
        "info": info.to_dict(),
    }


__all__ = [
    "DEFAULT_BATCH_WAIT_SECONDS",
    "DEFAULT_IDLE_SECONDS",
    "GpuLock",
    "GpuLockError",
    "LockInfo",
    "acquire",
    "lock_status",
    "recover_stale_lock",
    "try_acquire",
]
