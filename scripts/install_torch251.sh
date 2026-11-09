#!/usr/bin/env bash
# Downgrade torch 2.15.0 (picked by nnInteractive resolver) back to 2.5.1 in .venv-nninteractive.
# Reason: project torch alignment is 2.5.1+cu124 (same as .venv-totalseg / .venv-nnunet);
# nnInteractive==2.6.0 requires torch!=2.9.*,>=2.1.2 so 2.5.1 satisfies it.
# torchvision must move to the matching 0.20.1 or it breaks (it pins torch==2.15.0 exactly).
# First try the cu124 index; if pypi.nvidia.com DNS is still broken, fall back to plain PyPI
# (PyPI's linux torch 2.5.1 wheel is the same +cu124 build).
set -u
cd /home/gogvf/work/BodyDonor
LOG=logs/m4s3-torch-downgrade.log
PY=.venv-nninteractive/bin/python
echo "=== torch 2.5.1 downgrade start $(date -Is) ===" >>"$LOG"
if getent hosts pypi.nvidia.com >/dev/null 2>&1; then
  echo "DNS pypi.nvidia.com OK -> trying cu124 index first" >>"$LOG"
  "$PY" -m pip install torch==2.5.1 torchvision==0.20.1 --index-url https://download.pytorch.org/whl/cu124 >>"$LOG" 2>&1
  st=$?
else
  echo "DNS pypi.nvidia.com FAILED -> installing torch==2.5.1 torchvision==0.20.1 from plain PyPI" >>"$LOG"
  "$PY" -m pip install torch==2.5.1 torchvision==0.20.1 >>"$LOG" 2>&1
  st=$?
fi
echo "=== pip exit $st $(date -Is) ===" >>"$LOG"
"$PY" -c "import torch; print('import torch:', torch.__version__, 'cuda_built', torch.backends.cuda.is_built())" >>"$LOG" 2>&1
echo "=== torch import exit $? $(date -Is) ===" >>"$LOG"
"$PY" -c "import nnInteractive; print('import nnInteractive ok')" >>"$LOG" 2>&1
echo "=== nnInteractive import exit $? $(date -Is) ===" >>"$LOG"
"$PY" -c "from nnInteractive.model_management import list_models; print('list_models import ok')" >>"$LOG" 2>&1
echo "=== list_models import exit $? $(date -Is) ===" >>"$LOG"
echo "=== torch downgrade end $(date -Is) ===" >>"$LOG"
