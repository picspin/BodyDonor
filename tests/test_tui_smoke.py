"""M2：TUI 非交互冒烟验证（启动后端 + TUI 一次渲染/轮询循环不断言崩溃）。

交互式长驻不进 CI（proposal §4.4）：本测试用 Textual 的 test harness 跑
一次 mount + 轮询 + 渲染，断言不崩溃、关键 widget 存在。交互式按键流程
不测（长驻交互进 CI 没意义）。
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from textual.widgets import Static

httpx = pytest.importorskip("httpx")
textual = pytest.importorskip("textual")


class TestMonitorEndpoints:
    """TUI 轮询的数据源端点（后端侧冒烟）。"""

    def test_monitor_gpu(self) -> None:
        from fastapi.testclient import TestClient

        from bodydonor.app import app

        with TestClient(app) as c:
            r = c.get("/monitor/gpu")
        assert r.status_code == 200
        body = r.json()
        # available=true 时必有实测数字；false 时必有 reason（不猜数字）
        if body["available"]:
            assert body["memory_total_mi_b"] > 0
            assert body["memory_used_mi_b"] >= 0
        else:
            assert body["reason"]

    def test_monitor_pipeline(self) -> None:
        from fastapi.testclient import TestClient

        from bodydonor.app import app

        with TestClient(app) as c:
            r = c.get("/monitor/pipeline")
        assert r.status_code == 200
        body = r.json()
        stages = body["stages"]
        # 8 阶段注册表完整（proposal §6）
        assert len(stages) == 8
        assert "1_ingest" in stages and "8_recheck" in stages
        # queue_size 是进程内共享态（其他测试可能入队），只断言 >= 0
        assert body["queue_size"] >= 0

    def test_pipeline_stage_update(self) -> None:
        from fastapi.testclient import TestClient

        from bodydonor.app import app

        with TestClient(app) as c:
            r = c.post(
                "/monitor/pipeline/3_teacher_inference", params={"status": "running"}
            )
            assert r.status_code == 200
            body = c.get("/monitor/pipeline").json()
        assert body["stages"]["3_teacher_inference"] == "running"
        # 未知 stage -> 404
        with TestClient(app) as c:
            r = c.post("/monitor/pipeline/no_such_stage")
        assert r.status_code == 404


class TestTuiSmoke:
    """TUI 非交互冒烟：一次 mount + 渲染，断言不崩溃。"""

    @pytest.mark.asyncio
    async def test_tui_mount_and_render(self) -> None:
        """mount + 一次 _poll + _render 不断言崩溃（后端不可达也正常显示）。"""
        from bodydonor.tui import BodyDonorMonitor

        app = BodyDonorMonitor(base_url="http://127.0.0.1:1")  # 不存在的后端
        async with app.run_test() as pilot:
            await pilot.pause()
            # 关键 widget 存在（mount 成功，轮询失败不崩溃）
            app.query_one("#gpu-membar", Static)
            app.query_one("#pipeline-panel", Static)
            app.query_one("#status-line", Static)

    @pytest.mark.asyncio
    async def test_tui_render_with_mock_backend(self) -> None:
        """mock 后端返回 -> 渲染出显存条与 8 阶段。"""
        from bodydonor.tui import BodyDonorMonitor

        gpu = {
            "available": True,
            "name": "Quadro RTX 5000",
            "memory_used_mi_b": 2048,
            "memory_total_mi_b": 16384,
            "utilization_pct": 88,
        }
        pipeline = {
            "stages": {
                "1_ingest": "done",
                "2_deid": "done",
                "3_teacher_inference": "running",
                "4_auto_qc": "idle",
                "5_manual_review": "idle",
                "6_distill_train": "idle",
                "7_relabel": "idle",
                "8_recheck": "idle",
            },
            "queue_size": 2,
            "active_jobs": ["case-1:3_teacher_inference"],
        }

        app = BodyDonorMonitor(base_url="http://mock")
        async with app.run_test() as pilot:
            with patch.object(app, "_poll", side_effect=None):
                app.gpu = gpu
                app.pipeline = pipeline
                app._render()
                await pilot.pause()

                membar_text = str(app.query_one("#gpu-membar", Static).render())
                assert "▓" in membar_text  # 显存条渲染出块字符
                detail = str(app.query_one("#gpu-detail", Static).render())
                assert "2048 / 16384 MiB" in detail
                assert "88%" in detail
                qsize = str(app.query_one("#queue-size", Static).render())
                assert "2 个任务" in qsize

    def test_membar(self) -> None:
        """显存条：块字符比例正确。"""
        from bodydonor.tui import _membar

        bar = _membar(8192, 16384, width=20)
        assert bar == "▓" * 10 + "░" * 10
        assert _membar(0, 0) == "?" * 30
        full = _membar(16384, 16384, width=10)
        assert full == "▓" * 10
