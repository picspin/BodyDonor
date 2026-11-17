"""M2-align 3.2：JSON runner 契约测试（任务/结果 JSON + 隔离）。

TS 真卡推理不进 CI（proposal §11）：本文件只测契约层（stdin/stdout JSON、
损坏输入），不跑推理。R3 ④删 runner 侧白名单纵深镜像后，白名单判定只在
主进程 check_worker_task（blocked 一票否决，唯一防线）。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from bodydonor.segmentation import (
    RunnerResult,
    build_runner_cmd,
    runner_module_path,
)

RUNNER = runner_module_path()


def ts_python() -> str | None:
    """TS venv python（存在才跑 runner 契约测试；CI 无该 venv 时跳过）。"""
    p = Path.home() / "work" / "BodyDonor" / ".venv-totalseg" / "bin" / "python"
    return str(p) if p.exists() else None


def run_runner(task_json: dict) -> subprocess.CompletedProcess:
    """stdin 传任务 JSON，收 stdout（纯 JSON 契约）。"""
    return subprocess.run(  # noqa: S603 — repo 自身 venv 路径
        [str(ts_python()), str(RUNNER)],
        input=json.dumps(task_json),
        capture_output=True,
        text=True,
        timeout=60,
    )


class TestRunnerContract:
    @pytest.mark.skipif(ts_python() is None, reason="TS venv 不存在")
    def test_missing_input_clean_error(self) -> None:
        """输入不存在 -> status=error + 干净错误（不 traceback 进 stdout）。"""
        proc = run_runner(
            {
                "input_nii": "/no/such.nii",
                "task": "total",
                "device": "gpu",
                "output_dir": "/tmp/x",
                "job_id": "t-missing",
            }
        )
        out = json.loads(proc.stdout)
        assert out["status"] == "error"
        assert "输入不存在" in out["error"]
        # stdout 是纯 JSON（无 traceback 混入）
        assert "Traceback" not in proc.stdout

    @pytest.mark.skipif(ts_python() is None, reason="TS venv 不存在")
    def test_result_json_fields(self) -> None:
        """结果 JSON 含全部契约字段。"""
        proc = run_runner(
            {
                "input_nii": "/no/such.nii",
                "task": "total",
                "device": "gpu",
                "output_dir": "/tmp/x",
                "job_id": "t-fields",
            }
        )
        out = json.loads(proc.stdout)
        for key in ["status", "exit_code", "mask_path", "classes", "wall_seconds",
                    "peak_vram_mb", "error"]:
            assert key in out, key

    @pytest.mark.skipif(ts_python() is None, reason="TS venv 不存在")
    def test_bad_json_clean_error(self) -> None:
        """任务 JSON 损坏 -> 干净错误（status=error，stdout 纯 JSON）。"""
        proc = subprocess.run(  # noqa: S603
            [str(ts_python()), str(RUNNER)],
            input="{bad json",
            capture_output=True,
            text=True,
            timeout=60,
        )
        out = json.loads(proc.stdout)
        assert out["status"] == "error"
        assert "JSON" in out["error"]


class TestRunnerResult:
    def test_from_dict_roundtrip(self) -> None:
        d = {
            "status": "ok",
            "exit_code": 0,
            "mask_path": "/tmp/out",
            "classes": ["liver", "spleen"],
            "wall_seconds": 12.3,
            "peak_vram_mb": 5472.0,
            "error": "",
        }
        r = RunnerResult.from_dict(d)
        assert r.status == "ok"
        assert r.classes == ["liver", "spleen"]
        assert r.peak_vram_mb == 5472.0

    def test_from_dict_null_vram(self) -> None:
        """peak_vram_mb 不可用时为 null（不猜数字）。"""
        d = {
            "status": "ok",
            "exit_code": 0,
            "mask_path": "/tmp/out",
            "classes": [],
            "wall_seconds": 1.0,
            "peak_vram_mb": None,
            "error": "CPU 回退，peak 不可测",
        }
        r = RunnerResult.from_dict(d)
        assert r.peak_vram_mb is None


class TestRunnerPaths:
    def test_runner_module_exists(self) -> None:
        """ts_runner.py 在 src 内，由 TS venv python 执行。"""
        assert RUNNER.exists()
        assert RUNNER.name == "ts_runner.py"

    @pytest.mark.skipif(ts_python() is None, reason="TS venv 不存在")
    def test_build_runner_cmd(self) -> None:
        """命令 = TS venv python + runner 模块路径（JSON 契约通道）。"""
        cmd = build_runner_cmd()
        assert len(cmd) == 2
        assert ".venv-totalseg" in cmd[0]
        assert cmd[0].endswith("python")
        assert cmd[1].endswith("ts_runner.py")

    def test_main_venv_not_shared(self) -> None:
        """主 .venv 不含 totalsegmentator（Q7 隔离不变）。"""
        import sysconfig

        main_venv = Path(sysconfig.get_paths()["purelib"])
        assert not (main_venv / "totalsegmentator").exists()
