"""BodyDonor TUI（M2，proposal §4.4）：Textual 监控面板。

只做一件事：通过 HTTP 轮询本地 FastAPI 后端，展示实时 GPU 显存、队列与
流水线 ①→⑧ 状态机（proposal §6 的 8 阶段）。CLI 批处理与交互式 TUI 互不耦合
——TUI 不直接跑流水线，import 走磁盘路径直连（见 import_local）。

设计（frontend-design）：
- 色系取放射科窗宽窗位（琥珀骨窗 + 状态绿/警示红/运行青蓝），不用默认单强调色
- 显存条用终端块字符（▓░），是终端原生视觉语言
- ①→⑧ 编号是真实序列（8 阶段流水线），是信息结构不是装饰
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Header, Input, Static

# 轮询间隔（秒）：GPU 快照 nvidia-smi 本身 ~100ms，2s 足够"实时"
POLL_SECONDS = 2.0

# 放射科窗宽窗位色系（frontend-design 定稿）
CSS = """
Screen {
    background: #0e1412;
    color: #c9d1c9;
}

#topline {
    height: 1;
    padding: 0 1;
    background: #131c19;
    color: #e8a33d;
}

#main {
    height: 1fr;
}

#gpu-panel {
    width: 1fr;
    padding: 1 2;
    border-right: solid #1f2b27;
}

#queue-panel {
    width: 1fr;
    padding: 1 2;
}

#pipeline-panel {
    height: auto;
    padding: 1 2;
    border-top: solid #1f2b27;
}

#gpu-name {
    color: #8aa39b;
    margin-bottom: 1;
}

#gpu-membar {
    color: #e8a33d;
    margin-bottom: 1;
}

#gpu-detail {
    color: #c9d1c9;
}

#queue-size {
    color: #5b9bb5;
    margin-bottom: 1;
}

#queue-jobs {
    color: #8aa39b;
}

.stage {
    margin-right: 2;
}

.stage-idle { color: #4a5a54; }
.stage-running { color: #5b9bb5; text-style: bold; }
.stage-done { color: #5fa867; }
.stage-blocked { color: #c75450; }

#status-line {
    height: 1;
    padding: 0 1;
    color: #4a5a54;
}

#import-bar {
    height: 3;
    display: none;
    padding: 0 1;
}

#import-bar.visible { display: block; }

Footer {
    background: #131c19;
}
"""

STAGE_LABELS = {
    "1_ingest": "① Ingest",
    "2_deid": "② De-ID",
    "3_teacher_inference": "③ 教师",
    "4_auto_qc": "④ QC",
    "5_manual_review": "⑤ 抽检",
    "6_distill_train": "⑥ 蒸馏",
    "7_relabel": "⑦ 重标",
    "8_recheck": "⑧ 复检",
}

STAGE_ORDER = list(STAGE_LABELS)


def _membar(used: int, total: int, width: int = 30) -> str:
    """显存条：终端块字符（▓░），放射科剂量条的终端对应物。"""
    if total <= 0:
        return "?" * width
    filled = round(used / total * width)
    filled = max(0, min(width, filled))
    return "▓" * filled + "░" * (width - filled)


class BodyDonorMonitor(App[None]):
    """BodyDonor 监控面板：轮询 /monitor/gpu 与 /monitor/pipeline。"""

    TITLE = "BodyDonor"
    CSS = CSS

    def __init__(self, base_url: str = "http://127.0.0.1:8000") -> None:
        super().__init__()
        self.base_url = base_url.rstrip("/")
        self.gpu: dict[str, Any] = {"available": False, "reason": "尚未轮询"}
        self.pipeline: dict[str, Any] = {"stages": {}, "queue_size": 0, "active_jobs": []}
        self.poll_error: str = ""

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static(f"BodyDonor monitor — {self.base_url}", id="topline")
        with Horizontal(id="main"):
            with Vertical(id="gpu-panel"):
                yield Static("GPU", id="gpu-name")
                yield Static("", id="gpu-membar")
                yield Static("", id="gpu-detail")
            with Vertical(id="queue-panel"):
                yield Static("", id="queue-size")
                yield Static("", id="queue-jobs")
        yield Static("", id="pipeline-panel")
        yield Static("", id="status-line")
        with Horizontal(id="import-bar"):
            yield Input(
                placeholder="输入磁盘路径导入（复用 ingest()，不走 HTTP）", id="import-input"
            )
        yield Footer()

    def on_mount(self) -> None:
        self.set_interval(POLL_SECONDS, self._poll)
        # 启动立即轮询一次（首屏不等 2s）
        self.call_later(self._poll)

    # -- 轮询 ---------------------------------------------------------------

    def _poll(self) -> None:
        try:
            r = httpx.get(f"{self.base_url}/monitor/gpu", timeout=3.0)
            self.gpu = (
                r.json()
                if r.status_code == 200
                else {"available": False, "reason": f"HTTP {r.status_code}"}
            )
            self.poll_error = ""
        except Exception as e:  # noqa: BLE001 — 后端不可达是常态（未启动），显示不猜数字
            self.poll_error = f"{type(e).__name__}: {e}"
            self.gpu = {"available": False, "reason": "后端不可达"}
        try:
            r = httpx.get(f"{self.base_url}/monitor/pipeline", timeout=3.0)
            self.pipeline = (
                r.json()
                if r.status_code == 200
                else {"stages": {}, "queue_size": 0, "active_jobs": []}
            )
        except Exception:  # noqa: BLE001
            self.pipeline = {"stages": {}, "queue_size": 0, "active_jobs": []}
        self._render()

    # -- 渲染 ---------------------------------------------------------------

    def _render(self) -> None:
        # GPU 面板
        gpu_name = self.query_one("#gpu-name", Static)
        membar = self.query_one("#gpu-membar", Static)
        detail = self.query_one("#gpu-detail", Static)
        if self.gpu.get("available"):
            used = self.gpu["memory_used_mi_b"]
            total = self.gpu["memory_total_mi_b"]
            gpu_name.update(f"GPU · {self.gpu.get('name', '?')}")
            membar.update(_membar(used, total))
            util = self.gpu.get("utilization_pct")
            util_s = f" · 利用率 {util}%" if util is not None else ""
            detail.update(f"{used} / {total} MiB{util_s}")
        else:
            reason = self.gpu.get("reason", "?")
            gpu_name.update("GPU · 不可用")
            membar.update("░" * 30)
            detail.update(reason + (f"（轮询：{self.poll_error}）" if self.poll_error else ""))

        # 队列面板
        qsize = self.query_one("#queue-size", Static)
        qjobs = self.query_one("#queue-jobs", Static)
        qsize.update(f"队列 {self.pipeline.get('queue_size', 0)} 个任务")
        jobs = self.pipeline.get("active_jobs", [])
        if jobs:
            shown = "\n".join(f"  · {j}" for j in jobs[:6])
            if len(jobs) > 6:
                shown += f"\n  … 还有 {len(jobs) - 6} 个"
            qjobs.update(shown)
        else:
            qjobs.update("（空）")

        # 流水线 ①→⑧（真实序列；状态色 + 当前阶段箭头）
        stages = self.pipeline.get("stages", {})
        parts = []
        for i, key in enumerate(STAGE_ORDER):
            st = stages.get(key, "idle")
            cls = {
                "running": "stage-running",
                "done": "stage-done",
                "blocked": "stage-blocked",
            }.get(st, "stage-idle")
            arrow = " ▸" if st == "running" else ""
            parts.append(f"[{cls}]{STAGE_LABELS[key]}{arrow}[/{cls}]")
            if i < len(STAGE_ORDER) - 1:
                parts.append("[stage-idle]→[/stage-idle]")
        self.query_one("#pipeline-panel", Static).update(" ".join(parts))

        # 状态行
        status = self.query_one("#status-line", Static)
        if self.poll_error:
            status.update(
                f"⚠ 轮询失败 {self.poll_error} — 启动后端: uvicorn bodydonor.app:app"
            )
        else:
            status.update(
                f"已连接 {self.base_url} · 每 {POLL_SECONDS:.0f}s 轮询"
                " · i 导入 · r 刷新 · q 退出"
            )

    # -- 按键 ---------------------------------------------------------------

    def key_r(self) -> None:
        """立即刷新（不等下个轮询周期）。"""
        self._poll()

    def key_i(self) -> None:
        """打开导入条（本地磁盘导入，复用 ingest() 同一入口）。"""
        bar = self.query_one("#import-bar", Horizontal)
        bar.add_class("visible")
        self.query_one("#import-input", Input).focus()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "import-input":
            return
        path = event.value.strip()
        bar = self.query_one("#import-bar", Horizontal)
        bar.remove_class("visible")
        event.input.value = ""
        if path:
            await self.run_worker(self._import_local(path), exclusive=True)

    async def _import_local(self, path: str) -> None:
        """TUI 内触发本地磁盘导入：复用 pipeline.scan + commit 同一入口。

        大批量走磁盘路径直读（proposal §4.4 TUI 通道），不走 HTTP 上传。
        结果写状态行；worker 线程里跑，不阻塞渲染。
        """
        from .pipeline import commit, scan
        from .storage import LocalFsStorage

        status = self.query_one("#status-line", Static)
        try:
            settings = load_settings()
            data_dir = settings.resolved_data_dir
        except Exception as e:  # noqa: BLE001
            self.call_from_thread(status.update, f"✘ 配置错误：{e}")
            return
        try:
            outcome = scan(os.fsdecode(path), settings, source_name=os.path.basename(path))
        except Exception as e:  # noqa: BLE001
            self.call_from_thread(status.update, f"✘ 导入失败：{e}")
            return
        try:
            storage = LocalFsStorage(data_dir / "storage")
            outcome = commit(outcome, settings, storage)
        except Exception as e:  # noqa: BLE001
            self.call_from_thread(status.update, f"✘ 落库失败：{e}")
            return
        self.call_from_thread(
            status.update,
            f"✔ 导入完成：Case {len(outcome.case_ids)} · Series {len(outcome.selected_series_ids)}",
        )
        self._poll()


def load_settings():  # noqa: ANN201 — 延迟导入避免循环
    from .config import load_settings as _ls

    return _ls()


def run_tui(base_url: str = "http://127.0.0.1:8000") -> None:
    """启动 TUI（bodydonor tui 入口）。"""
    BodyDonorMonitor(base_url=base_url).run()
