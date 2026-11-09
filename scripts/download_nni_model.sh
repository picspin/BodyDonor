#!/usr/bin/env bash
# Download nnInteractive default model weights (CPU/network only, no CUDA).
set -u
cd /home/gogvf/work/BodyDonor
LOG=logs/m4s3-model-download.log
export NNINTERACTIVE_MODEL_DIR="$HOME/data/BodyDonor/nninteractive-models"
echo "=== model download start $(date -Is) ===" >>"$LOG"
.venv-nninteractive/bin/python - >>"$LOG" 2>&1 <<'PYEOF'
import time
t0 = time.monotonic()
from nnInteractive.model_management import (
    ensure_model_available,
    get_default_model_id,
    is_model_downloaded,
)
mid = get_default_model_id()
print(f"model_id={mid}", flush=True)
already = is_model_downloaded(mid)
print(f"already_downloaded={already}", flush=True)
path = ensure_model_available(mid)
dt = time.monotonic() - t0
import subprocess
du = subprocess.run(["du", "-sm", str(path)], capture_output=True, text=True)
print(f"path={path}", flush=True)
print(f"size_mb={du.stdout.split()[0]}", flush=True)
print(f"elapsed_s={dt:.1f}", flush=True)
PYEOF
echo "=== model download exit $? $(date -Is) ===" >>"$LOG"
