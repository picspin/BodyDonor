"""M5 GPU 探针测试（scripts/gpu_probe.py）。

解析/选择/决策逻辑用本机实录 nvidia-smi 输出（Quadro RTX 5000，
驱动 556.18，算力 7.5）+ 注入式验算函数测分支；真实验算由 CLI 在真机
上跑（见 M5-REPORT §4），本文件不伪造硬件结果。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from gpu_probe import parse_smi_csv, probe, select_candidate

# 本机实录（2026-11-11，/usr/lib/wsl/lib/nvidia-smi --query-gpu=... --format=csv）
QUADRO_CSV = """index, name, uuid, driver_version, compute_cap, memory.total [MiB]
0, Quadro RTX 5000, GPU-fdda0ac9-446d-b461-6d0e-7b1a3087c848, 556.18, 7.5, 16384 MiB
"""


def test_parse_real_quadro_output() -> None:
    gpus = parse_smi_csv(QUADRO_CSV)
    assert len(gpus) == 1
    g = gpus[0]
    assert g["index"] == 0
    assert g["name"] == "Quadro RTX 5000"
    assert g["uuid"] == "GPU-fdda0ac9-446d-b461-6d0e-7b1a3087c848"
    assert g["driver_version"] == "556.18"  # 低于 SegMed CU12.6 门槛 560.76
    assert g["compute_cap"] == 7.5
    assert g["memory_total_mib"] == 16384


def test_parse_name_with_comma() -> None:
    # 产品名含逗号时从右取字段（SegMed 同款坑）
    text = (
        "index, name, uuid, driver_version, compute_cap, memory.total [MiB]\n"
        "0, NVIDIA GeForce RTX 4090, Laptop GPU, GPU-aaaaaaaa-0000-0000-0000-000000000000, "
        "570.65, 8.9, 16384 MiB\n"
    )
    gpus = parse_smi_csv(text)
    assert len(gpus) == 1
    assert gpus[0]["name"] == "NVIDIA GeForce RTX 4090, Laptop GPU"
    assert gpus[0]["driver_version"] == "570.65"


def test_parse_drops_bad_rows() -> None:
    text = QUADRO_CSV + "1, Fake Card, NOT-A-UUID, 1.0, 7.5, 8192 MiB\n"
    assert len(parse_smi_csv(text)) == 1  # uuid 非 GPU- 开头丢弃


def test_select_highest_compute_cap_first() -> None:
    gpus = [
        {
            "index": 1,
            "name": "B",
            "uuid": "GPU-2",
            "driver_version": "1",
            "compute_cap": 8.9,
            "memory_total_mib": 16384,
        },
        {
            "index": 0,
            "name": "A",
            "uuid": "GPU-1",
            "driver_version": "1",
            "compute_cap": 7.5,
            "memory_total_mib": 16384,
        },
    ]
    assert select_candidate(gpus)["name"] == "B"
    assert select_candidate([]) is None


def test_probe_offline_sample_never_claims_cuda(tmp_path: Path) -> None:
    sample = tmp_path / "smi.csv"
    sample.write_text(QUADRO_CSV, encoding="utf-8")
    d = probe(sample_file=sample)
    assert d["backend"] == "cpu"  # 离线只校验选择逻辑，不宣称 CUDA 可用
    assert d["candidate"]["name"] == "Quadro RTX 5000"
    assert d["cuda_check"]["ran"] is False


def test_probe_cuda_check_pass_selects_cuda(monkeypatch) -> None:  # noqa: ANN001
    import gpu_probe

    monkeypatch.setattr(gpu_probe, "detect_gpus", lambda smi_text=None: parse_smi_csv(QUADRO_CSV))
    d = probe(cuda_check_fn=lambda: (True, {"torch": "2.5.1+cu124", "elapsed_s": 0.5}))
    assert d["backend"] == "cuda"
    assert d["candidate"]["driver_version"] == "556.18"  # 556.18 照样选 CUDA
    assert d["cuda_check"]["ok"] is True


def test_probe_cuda_check_fail_falls_back_to_cpu(monkeypatch) -> None:  # noqa: ANN001
    import gpu_probe

    monkeypatch.setattr(gpu_probe, "detect_gpus", lambda smi_text=None: parse_smi_csv(QUADRO_CSV))
    d = probe(cuda_check_fn=lambda: (False, {"error": "torch.cuda.is_available() is False"}))
    assert d["backend"] == "cpu"
    assert "CPU 兜底" in d["reason"]


def test_probe_no_gpu_cpu(monkeypatch) -> None:  # noqa: ANN001
    import gpu_probe

    monkeypatch.setattr(gpu_probe, "detect_gpus", lambda smi_text=None: [])
    d = probe(cuda_check_fn=lambda: (True, {}))
    assert d["backend"] == "cpu"
    assert d["candidate"] is None


def test_main_output_writes_pure_json(tmp_path: Path) -> None:
    """--output 落盘为纯 JSON（json.dump 直写，不走 shell 重定向）。

    离线 sample 模式（不实跑 CUDA、不占 GPU 锁）；json.load 通过 +
    锁日志（stderr）不混入文件。
    """
    import json

    import gpu_probe

    sample = tmp_path / "smi.csv"
    sample.write_text(QUADRO_CSV, encoding="utf-8")
    out = tmp_path / "nested" / "probe.json"
    rc = gpu_probe.main(["--sample-file", str(sample), "--json", "--output", str(out)])
    assert rc == 0
    decision = json.loads(out.read_text(encoding="utf-8"))  # 纯 JSON 可解析
    assert decision["backend"] == "cpu"  # 离线模式恒 cpu
    assert decision["candidate"]["name"] == "Quadro RTX 5000"
    assert decision["cuda_check"]["ran"] is False
    # 逐字段完整（backend/candidate/cuda_check/reason）
    assert "reason" in decision


def test_main_output_missing_sample_exit_2(tmp_path: Path) -> None:
    """--output 与不存在的 sample-file 并存时仍退出 2（不写文件）。"""
    import gpu_probe

    out = tmp_path / "probe.json"
    rc = gpu_probe.main(
        ["--sample-file", str(tmp_path / "no-such.csv"), "--output", str(out)]
    )
    assert rc == 2
    assert not out.exists()
