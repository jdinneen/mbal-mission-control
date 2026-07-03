#!/usr/bin/env python3
"""
Chai-1 co-fold of the iGluR5/GluK1 ligand-binding domain + domoic acid,
then RMSD vs the deposited crystal structure (PDB 2PBW).

Writes:
  out/pred.model_idx_{0..4}.cif   — predicted complexes
  out/scores.model_idx_{N}.npz    — Chai confidence arrays
  out/2PBW.cif                    — reference crystal structure
  out/summary.json                — scores + Calpha RMSD (best model)
Run via run_fold.sh (sets CHAI_DOWNLOADS_DIR and the venv).
"""
import json, os, shutil, urllib.request
from pathlib import Path
import numpy as np

HERE = Path(__file__).resolve().parent
# Primary output goes to gpu-work/out (kept off OneDrive); a copy of the small
# result files is mirrored to RESULTS_BACK so the Claude sandbox can read them.
GPUWORK = Path(os.environ.get("GPUWORK", "/mnt/c/Users/jondi/gpu-work"))
OUT = GPUWORK / "out"
OUT.mkdir(parents=True, exist_ok=True)
RESULTS_BACK = Path(os.environ.get("RESULTS_BACK", HERE / "out"))
RESULTS_BACK.mkdir(parents=True, exist_ok=True)
FASTA = (GPUWORK / "in" / "iglur5_domoic.fasta")
if not FASTA.exists():
    FASTA = HERE / "iglur5_domoic.fasta"

# ---- 1. Chai-1 inference -------------------------------------------------
from chai_lab.chai1 import run_inference
import torch

# clear a previous run (Chai refuses a non-empty output_dir)
pred_dir = OUT / "chai"
if pred_dir.exists():
    shutil.rmtree(pred_dir)
pred_dir.mkdir(parents=True)

print("Running Chai-1 (num_diffn_timesteps=200, recycles=3)...")
candidates = run_inference(
    fasta_file=FASTA,
    output_dir=pred_dir,
    num_trunk_recycles=3,
    num_diffn_timesteps=200,
    seed=42,
    device="cuda:0",
    use_esm_embeddings=True,
)

def scalar_value(value):
    arr = np.asarray(value)
    return float(arr.reshape(-1)[0])

def ranking_value(ranking_data, model_idx, key):
    if hasattr(ranking_data, key):
        return scalar_value(getattr(ranking_data, key))
    score_file = pred_dir / f"scores.model_idx_{model_idx}.npz"
    if score_file.exists():
        with np.load(score_file) as score_npz:
            if key in score_npz:
                return scalar_value(score_npz[key])
    raise AttributeError(f"Chai ranking data does not contain {key!r}")

scores = []
for i, rd in enumerate(candidates.ranking_data):
    scores.append(dict(
        model=i,
        aggregate_score=ranking_value(rd, i, "aggregate_score"),
        ptm=ranking_value(rd, i, "ptm"),
        iptm=ranking_value(rd, i, "iptm"),
    ))
scores.sort(key=lambda s: s["aggregate_score"], reverse=True)
best = scores[0]
print("Chai scores:", json.dumps(scores, indent=2))

# copy predicted cif files up to out/
for cif in pred_dir.glob("pred.model_idx_*.cif"):
    shutil.copy(cif, OUT / cif.name)
best_cif = OUT / f"pred.model_idx_{best['model']}.cif"

# ---- 2. reference structure (2PBW) --------------------------------------
ref_cif = OUT / "2PBW.cif"
if not ref_cif.exists():
    urllib.request.urlretrieve("https://files.rcsb.org/download/2PBW.cif", ref_cif)

# ---- 3. Calpha RMSD (Kabsch) via gemmi -----------------------------------
import gemmi

AA3_TO_1 = {
    "ALA": "A", "ARG": "R", "ASN": "N", "ASP": "D", "CYS": "C",
    "GLN": "Q", "GLU": "E", "GLY": "G", "HIS": "H", "ILE": "I",
    "LEU": "L", "LYS": "K", "MET": "M", "PHE": "F", "PRO": "P",
    "SER": "S", "THR": "T", "TRP": "W", "TYR": "Y", "VAL": "V",
    "MSE": "M",
}

def protein_ca_chains(path):
    st = gemmi.read_structure(str(path))
    st.setup_entities()
    model = st[0]
    chains = []
    for ch in model:
        residues = []
        for r in ch:
            aa = AA3_TO_1.get(r.name.upper())
            if aa is None:
                continue
            atom = r.find_atom("CA", "*")
            if atom is None:
                continue
            residues.append(dict(
                resid=(int(r.seqid.num), str(r.seqid.icode).strip()),
                residue_number=int(r.seqid.num),
                aa=aa,
                xyz=np.array([atom.pos.x, atom.pos.y, atom.pos.z], dtype=float),
            ))
        if residues:
            chains.append(dict(chain=ch.name, residues=residues))
    if not chains:
        raise ValueError(f"No protein C-alpha atoms found in {path}")
    return chains

def align_matching_residue_ids(pred_chain, ref_chain):
    pred_by_resid = {r["resid"]: r for r in pred_chain["residues"]}
    ref_by_resid = {r["resid"]: r for r in ref_chain["residues"]}
    aligned = []
    mismatches = []
    for resid in sorted(set(pred_by_resid) & set(ref_by_resid)):
        pred_res = pred_by_resid[resid]
        ref_res = ref_by_resid[resid]
        if pred_res["aa"] == ref_res["aa"]:
            aligned.append((resid, pred_res, ref_res))
        else:
            mismatches.append(dict(
                residue_number=resid[0],
                insertion_code=resid[1],
                predicted=pred_res["aa"],
                reference=ref_res["aa"],
            ))
    return aligned, mismatches

def kabsch_rmsd(P, Q):
    Pc = P - P.mean(0); Qc = Q - Q.mean(0)
    H = Pc.T @ Qc
    U, S, Vt = np.linalg.svd(H)
    d = np.sign(np.linalg.det(Vt.T @ U.T))
    D = np.diag([1, 1, d])
    R = Vt.T @ D @ U.T
    Pr = Pc @ R.T
    return float(np.sqrt(((Pr - Qc) ** 2).sum(1).mean()))

pred_chains = protein_ca_chains(best_cif)
ref_chains = protein_ca_chains(ref_cif)
alignment_candidates = []
for pred_chain in pred_chains:
    for ref_chain in ref_chains:
        aligned, mismatches = align_matching_residue_ids(pred_chain, ref_chain)
        if len(aligned) < 3:
            continue
        P = np.array([pred_res["xyz"] for _, pred_res, _ in aligned])
        Q = np.array([ref_res["xyz"] for _, _, ref_res in aligned])
        alignment_candidates.append(dict(
            pred_chain=pred_chain["chain"],
            reference_chain=ref_chain["chain"],
            n_residues_aligned=len(aligned),
            residue_number_start=aligned[0][0][0],
            residue_number_end=aligned[-1][0][0],
            sequence_mismatches=mismatches,
            ca_rmsd_angstrom=kabsch_rmsd(P, Q),
        ))
if not alignment_candidates:
    raise ValueError("No matching protein residue IDs found for RMSD alignment")

# Prefer the most complete protein-chain overlap; use RMSD only to break coverage ties.
alignment_candidates.sort(key=lambda x: (-x["n_residues_aligned"], x["ca_rmsd_angstrom"]))
best_alignment = alignment_candidates[0]
rmsd = best_alignment["ca_rmsd_angstrom"]
n = best_alignment["n_residues_aligned"]
print(
    f"\nCalpha RMSD (best model vs 2PBW chain {best_alignment['reference_chain']}, "
    f"n={n} residue-ID matched protein C-alpha atoms): {rmsd:.2f} A"
)

pass_iptm = bool(best["iptm"] > 0.5)
pass_rmsd = bool(rmsd < 2.0)
promotable = bool(pass_iptm and pass_rmsd)

summary = dict(
    status="completed",
    verdict="pass" if promotable else "fail",
    target="iGluR5/GluK1 LBD + domoic acid",
    split_or_heldout_unit="single held-out crystal reference; prediction scored after folding against PDB 2PBW",
    baseline_compared_against="RCSB PDB 2PBW crystal structure",
    reference_pdb="2PBW",
    main_metric=dict(
        ca_rmsd_angstrom=rmsd,
        iptm=best["iptm"],
    ),
    promotable=promotable,
    confusion_matrix_status="not_applicable",
    confusion_matrix_reason=(
        "Structure validation uses continuous RMSD and interface-confidence "
        "metrics; no hard labels or fixed probability threshold are produced."
    ),
    best_model=best,
    all_scores=scores,
    alignment_method="matching amino-acid residue IDs; non-protein ligand atoms excluded from C-alpha selection",
    alignment_candidates=alignment_candidates,
    reference_chain=best_alignment["reference_chain"],
    predicted_chain=best_alignment["pred_chain"],
    residue_number_start=best_alignment["residue_number_start"],
    residue_number_end=best_alignment["residue_number_end"],
    ca_rmsd_angstrom=rmsd,
    n_residues_aligned=n,
    pass_iptm=pass_iptm,
    pass_rmsd=pass_rmsd,
    torch_version=torch.__version__,
    cuda_version=torch.version.cuda,
    gpu=torch.cuda.get_device_name(0),
)
(OUT / "summary.json").write_text(json.dumps(summary, indent=2))
print("\nWrote", OUT / "summary.json")
print(json.dumps(summary, indent=2))

# ---- 4. mirror small results back to a Claude-readable folder -----------
for name in ["summary.json", best_cif.name, "2PBW.cif"]:
    src = OUT / name
    if src.exists():
        shutil.copy(src, RESULTS_BACK / name)
print("\nMirrored results to", RESULTS_BACK)
