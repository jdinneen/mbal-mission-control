#!/usr/bin/env bash
# =====================================================================
# MBAL breakthrough validation — Chai-1 co-fold on the local RTX 5090
# Target: kainate receptor (iGluR5/GluK1) LBD + domoic acid  (PDB 2PBW)
#
# Run this INSIDE a WSL terminal (not from the Claude sandbox):
#   cd /mnt/c/Users/jondi/OneDrive/Documents/mbal-mission-control/gpu_fold_run
#   bash run_fold.sh
#
# Staging (per your setup):
#   venv + ~5 GB Chai weights -> $HOME  (WSL native, no OneDrive churn)
#   job inputs/outputs        -> C:\Users\jondi\gpu-work\{in,out}
#   small results copied back -> this folder\out  (so Claude can read them)
# =====================================================================
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# --- staging dirs -----------------------------------------------------
GPUWORK="/mnt/c/Users/jondi/gpu-work"
export GPUWORK
mkdir -p "$GPUWORK/in" "$GPUWORK/out"
cp -f "$SCRIPT_DIR/iglur5_domoic.fasta" "$GPUWORK/in/"
# where to copy the small result files so the Claude sandbox can read them:
export RESULTS_BACK="$SCRIPT_DIR/out"
mkdir -p "$RESULTS_BACK"

echo "== 0. sanity: GPU visible in WSL? =="
nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv || {
  echo "ERROR: nvidia-smi failed. GPU not visible in WSL — fix that first."; exit 1; }

VENV="$HOME/.mbal_fold_venv"
if [ ! -d "$VENV" ]; then
  echo "== 1. create venv (native python3.12) =="
  python3 -m venv "$VENV"
fi
# shellcheck disable=SC1091
source "$VENV/bin/activate"
python -m pip install -q --upgrade pip

echo "== 2. install Blackwell-compatible PyTorch (cu128) =="
# RTX 5090 is sm_120 (Blackwell) — needs CUDA 12.8 wheels (torch >= 2.7).
# Driver 591.86 / CUDA 13.1 is backward-compatible with cu128 wheels.
python - <<'PY'
import importlib.util, subprocess, sys
need = True
if importlib.util.find_spec("torch"):
    try:
        import torch
        cu = getattr(torch.version, "cuda", "") or ""
        need = not (torch.cuda.is_available() and cu.startswith("12.8"))
    except Exception as exc:
        print("torch import failed; reinstalling cu128 stack:", exc)
        need = True
if need:
    subprocess.check_call([sys.executable,"-m","pip","install","-q",
        "--index-url","https://download.pytorch.org/whl/cu128",
        "torch","torchvision","torchaudio"])
else:
    print("torch cu128 already present")
PY

echo "== 3. install Chai-1 + deps =="
python - <<'PY'
import importlib.util, subprocess, sys
if importlib.util.find_spec("chai_lab") and importlib.util.find_spec("gemmi"):
    print("chai_lab/gemmi already present")
else:
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "chai_lab", "gemmi"])
    except subprocess.CalledProcessError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "chai_lab", "gemmi"])
PY

echo "== 3b. re-assert Blackwell-compatible PyTorch after Chai deps =="
# chai_lab 0.6.1 declares torch<2.7, and pip can downgrade the cu128 wheel
# to a cu124 build that cannot execute on RTX 5090 / sm_120. Keep Chai
# installed, but restore the full Blackwell-capable Torch stack and CUDA deps.
python - <<'PY'
import importlib.util, subprocess, sys
need = True
if importlib.util.find_spec("torch"):
    try:
        import torch
        cu = getattr(torch.version, "cuda", "") or ""
        need = not (torch.cuda.is_available() and cu.startswith("12.8"))
    except Exception as exc:
        print("torch import failed after Chai install; reinstalling cu128 stack:", exc)
        need = True
if need:
    subprocess.check_call([
        sys.executable, "-m", "pip", "install", "-q", "--force-reinstall",
        "--index-url", "https://download.pytorch.org/whl/cu128",
        "torch==2.11.0", "torchvision==0.26.0", "torchaudio==2.11.0",
    ])
else:
    print("torch cu128 still present after Chai deps")
PY

echo "== 4. verify GPU from torch =="
python - <<'PY'
import torch
print("torch", torch.__version__, "cuda", torch.version.cuda)
assert torch.cuda.is_available(), "torch cannot see the GPU"
print("device:", torch.cuda.get_device_name(0),
      "| capability sm_%d%d" % torch.cuda.get_device_capability(0))
PY

echo "== 5. run the fold =="
export CHAI_DOWNLOADS_DIR="$HOME/.chai_downloads"
mkdir -p "$CHAI_DOWNLOADS_DIR"
python "$SCRIPT_DIR/fold_and_score.py"

echo ""
echo "== DONE =="
echo "Full outputs : $GPUWORK/out"
echo "Copied for Claude: $RESULTS_BACK"
ls -la "$RESULTS_BACK"
echo ""
echo "Now tell Claude: 'fold done'"
