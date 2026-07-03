# MBAL breakthrough validation — run on the local RTX 5090

**Why this runs here and not in Claude's sandbox:** the Claude sandbox on WSL2
cannot see `/dev/dxg` (the WSL GPU device), so it can't touch the 5090. Your
real WSL terminal can. This package runs there; results write back into `out/`,
which Claude reads through the shared folder.

## Run it

Open a **WSL terminal** and:

```bash
cd <repo-root>/gpu_fold_run
bash run_fold.sh
```

First run installs a Blackwell-compatible PyTorch (cu128) + Chai-1 and downloads
~5 GB of model weights — budget 15–20 min. Later runs reuse the venv and are
just the fold (a few minutes).

When it finishes, tell Claude **"fold done"**.

## Staging (matches your setup)

- venv + ~5 GB Chai weights → `$HOME` in WSL (native, no OneDrive sync churn)
- job inputs → `C:\Users\jondi\gpu-work\in`
- full outputs → `C:\Users\jondi\gpu-work\out`
- small results (summary.json, best model .cif, 2PBW.cif) are copied back into
  this folder's `out/` so Claude can read them through the shared OneDrive path

No SSH / Tailscale needed — this runs locally on the machine with the GPU.
No conda needed — native python3.12 venv + pip cu128 wheels.
Driver 591.86 / CUDA 13.1 is backward-compatible with the cu128 wheels.

## What it does

1. Confirms the GPU is visible (`nvidia-smi`).
2. Builds a venv with `torch` cu128 (sm_120 / Blackwell support) + `chai_lab`.
3. Folds the **kainate receptor (iGluR5/GluK1) ligand-binding domain + domoic
   acid** — domoic acid is MBAL's Pseudo-nitzschia toxin.
4. Downloads the deposited crystal structure **PDB 2PBW** and computes Cα RMSD
   of the best predicted model against it.
5. Writes `out/summary.json` (scores + RMSD) and `out/*.cif` (structures).

## Success criteria

- `iptm > 0.5`  → confident protein–ligand interface
- `ca_rmsd < 2.0 Å` → prediction matches the real crystal structure

Pass = the platform can reconstruct a real toxin–receptor complex, which
unlocks the biosensor-design track (epitope mapping, aptamer screening).

## Files

- `iglur5_domoic.fasta` — fold input (protein seq + domoic acid SMILES)
- `run_fold.sh` — one-command driver (env setup + fold + score)
- `fold_and_score.py` — Chai-1 inference + Kabsch RMSD vs 2PBW
- `out/` — created on run: structures, scores, `summary.json`

## Note on the RMSD

The Cα RMSD aligns the two chains over their overlapping length from the
N-terminus (both are the same LBD construct). It's a first-pass global check;
if it's borderline, Claude will refine with a sequence-matched superposition on
the binding-site residues.
