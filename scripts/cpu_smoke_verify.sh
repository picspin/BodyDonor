#!/usr/bin/env bash
# CPU small-input verification: load model -> point prompt -> mask -> save.
# CPU only (device=cpu); no CUDA. 20-min cap via correct_image's runner timeout.
set -u
cd /home/gogvf/work/BodyDonor
LOG=logs/m4s3-cpu-verify.log
export NNINTERACTIVE_MODEL_DIR="$HOME/data/BodyDonor/nninteractive-models"
echo "=== cpu verify start $(date -Is) ===" >>"$LOG"
.venv/bin/python - >>"$LOG" 2>&1 <<'PYEOF'
import json
import resource
import time
from pathlib import Path

from bodydonor.nninteractive_backend import correct_image

patch = Path("/tmp/nni-patch-zwz4ydrp/amos_0004_patch48.nii.gz")
out_dir = Path("/tmp/nni-cpu-verify/amos_0004")
t0 = time.monotonic()
result = correct_image(
    patch,
    out_dir,
    [{"type": "point", "x": 24.0, "y": 24.0, "z": 24.0, "include": True}],
    device="cpu",
    target_label=1,
    job_id="nni-cpu-verify-amos0004",
)
wall = time.monotonic() - t0
peak_kb = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss  # runner 子进程峰值（RUSAGE_SELF 只量父进程，不含 torch/模型）
sidecar = json.loads(Path(result.sidecar_path).read_text(encoding="utf-8"))
print(f"job_id={result.job_id}")
print(f"mask_path={result.mask_path}")
print(f"sidecar_path={result.sidecar_path}")
print(f"load_seconds={result.load_seconds}")
print(f"inference_seconds={result.inference_seconds}")
print(f"wall_seconds_total={wall:.1f}")
print(f"n_target_voxels={result.n_target_voxels}")
print(f"cpu_peak_rss_mb={peak_kb/1024:.0f}")
print(f"source={sidecar['source']}")
print(f"license={sidecar['license']}")
print(f"provenance={json.dumps(sidecar['provenance'], ensure_ascii=False)}")
PYEOF
echo "=== cpu verify exit $? $(date -Is) ===" >>"$LOG"
