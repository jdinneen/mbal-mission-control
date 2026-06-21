#!/usr/bin/env python3
"""Generate the static map + downloads artifacts for Mission Control.

HONESTY SCOPE (critic-gated):
  * stations.geojson = WHERE THE OBSERVATIONS ARE (monitoring coverage), NOT a model
    prediction surface. The deployed bacteria model is a per-station tabular nowcast;
    the lab's own `bacteria_hgbt_spatial` is a registered WASH, so there is NO honest
    continuous confidence grid. We show coverage + observed exceedance rate + support.
  * downloads.json is built from an EXPLICIT ALLOWLIST. The snapshot build reads the
    working tree, so git-ignore is NOT a safety boundary — only files on the allowlist
    are ever published (no data/ raw parquets, no research-only / licensed datasets).

Run from mbal-mission-control/ with the monorepo as the parent. Requires duckdb.
Re-run when the station geo or master observations change. Front-end reads the
emitted manifests; it never hard-codes links.
"""
import json, hashlib, re, csv, io, os
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
EXCEED_MPN = 104  # AB411 single-sample enterococcus standard

def redact(text):
    text = re.sub(r"C:[\\/]+Users[\\/]+[^\\/\"]+[\\/]+", "", text)
    text = re.sub(r"/Users/[^/\\\"]+/", "/[redacted]/", text)
    text = re.sub(r"\b(?:sk|ghp|github_pat|glpat|xox[baprs])[-_][-A-Za-z0-9_]{16,}\b", "[token-redacted]", text)
    text = re.sub(r"\bAIza[0-9A-Za-z_-]{20,}\b", "[api-key-redacted]", text)
    return text

def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()

def _auc(labels, scores):
    """Rank-based AUC (Mann-Whitney), tie-aware. None if a class is absent."""
    pos = sum(labels); neg = len(labels) - pos
    if pos == 0 or neg == 0:
        return None
    order = sorted(range(len(scores)), key=lambda i: scores[i])
    ranks = [0.0] * len(scores)
    i = 0
    while i < len(order):
        j = i
        while j < len(order) and scores[order[j]] == scores[order[i]]:
            j += 1
        avg = (i + j - 1) / 2.0 + 1.0
        for k in range(i, j):
            ranks[order[k]] = avg
        i = j
    sum_pos = sum(ranks[i] for i in range(len(labels)) if labels[i] == 1)
    return (sum_pos - pos * (pos + 1) / 2.0) / (pos * neg)


def _per_station_skill(con):
    """Real held-out per-station model skill from the frozen test predictions.
    Returns {station_id: {n_test, events, model_auc, mem_auc}}. AUC only where the
    station has enough held-out events to be meaningful — never invented."""
    pred = ROOT / "reports" / "error_atlas" / "test_predictions.parquet"
    if not pred.exists():
        return {}
    rows = con.execute(f"""
        select station_id, exceed, p_model_cal, p_station_memory
        from read_parquet('{pred.as_posix()}')
        where p_model_cal is not null
    """).fetchall()
    by = {}
    for sid, ex, pm, pmem in rows:
        by.setdefault(sid, []).append((int(ex), float(pm), float(pmem) if pmem is not None else 0.0))
    out = {}
    for sid, recs in by.items():
        y = [r[0] for r in recs]
        n = len(y); ev = sum(y)
        rec = {"n_test": n, "events": ev}
        # meaningful per-station skill needs both classes + a few events
        if ev >= 3 and (n - ev) >= 3 and n >= 20:
            rec["model_auc"] = round(_auc(y, [r[1] for r in recs]) or 0, 3)
            rec["mem_auc"] = round(_auc(y, [r[2] for r in recs]) or 0, 3)
        out[sid] = rec
    return out


# ---------- 1. stations.geojson (coverage + real held-out model skill) ----------
def build_stations():
    import duckdb
    con = duckdb.connect()
    geo = ROOT / "reports" / "station_geo.parquet"
    mas = ROOT / "lakehouse" / "silver" / "bacteria_observations" / "master_bacteria.parquet"
    rows = con.execute(f"""
        with obs as (
          select station_id,
                 count(*) n_obs,
                 avg(case when try_cast(bacteria_result as double) > {EXCEED_MPN} then 1.0 else 0.0 end) base_rate
          from read_parquet('{mas.as_posix()}')
          where bacteria_result is not null
          group by station_id
        )
        select g.station_id, g.beach_name, g.county, g.latitude, g.longitude,
               coalesce(o.n_obs, 0) n_obs, o.base_rate
        from read_parquet('{geo.as_posix()}') g
        left join obs o using (station_id)
        where g.latitude is not null and g.longitude is not null
    """).fetchall()
    skill = _per_station_skill(con)
    feats = []
    for sid, name, county, lat, lon, n, br in rows:
        sk = skill.get(sid, {})
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [round(float(lon), 5), round(float(lat), 5)]},
            "properties": {
                "station_id": sid, "name": name or sid, "county": county or "",
                "n_obs": int(n), "base_rate": (round(float(br), 4) if br is not None else None),
                "low_support": int(n) < 30, "hazard": "bacteria",
                "n_test": sk.get("n_test", 0), "events": sk.get("events", 0),
                "model_auc": sk.get("model_auc"), "mem_auc": sk.get("mem_auc"),
            },
        })
    scored = sum(1 for f in feats if f["properties"]["model_auc"] is not None)
    fc = {"type": "FeatureCollection",
          "meta": {"hazard": "bacteria", "exceedance_threshold_mpn": EXCEED_MPN, "stations": len(feats),
                   "scored_stations": scored,
                   "note": "Monitoring stations with a known location. COVERAGE view: size = number of "
                           "lab samples, color = observed AB411 exceedance rate (>104 MPN/100mL). MODEL-SKILL "
                           "view: color = the calibrated model's held-out ranking skill (AUC) at that station "
                           "from the frozen test set; stations with too few held-out events to score honestly "
                           "are shown hollow ('not enough events'). Per-station AUC is noisy at low counts — "
                           "that is why thin stations are masked rather than colored. This is never an "
                           "interpolated surface over open water."},
          "features": feats}
    (HERE / "stations.geojson").write_text(json.dumps(fc, separators=(",", ":")), encoding="utf-8")
    nz = sum(1 for f in feats if f["properties"]["n_obs"] >= 30)
    print(f"stations.geojson: {len(feats)} stations ({nz} with >=30 samples; {scored} with honest per-station AUC)")
    aucs = [f["properties"]["model_auc"] for f in feats if f["properties"]["model_auc"] is not None]
    if aucs:
        print(f"  per-station model AUC range {min(aucs):.3f}–{max(aucs):.3f} mean {sum(aucs)/len(aucs):.3f}")

# ---------- 2. CSV exports from data.json (can't drift) ----------
def _csv(rows, cols):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow({c: r.get(c, "") for c in cols})
    return buf.getvalue()

def build_csvs(state):
    (HERE / "downloads").mkdir(exist_ok=True)
    fcols = ["id", "title", "kind", "headline", "plain"]
    findings = [{**f, "plain": (f.get("plain") or "").replace("\n", " ")} for f in state.get("findings", [])]
    (HERE / "downloads" / "findings.csv").write_text(_csv(findings, fcols), encoding="utf-8")
    mcols = ["model_id", "family", "task", "status", "claimable", "beats_baseline"]
    (HERE / "downloads" / "models.csv").write_text(_csv(state.get("models", []), mcols), encoding="utf-8")
    print(f"findings.csv ({len(findings)}), models.csv ({len(state.get('models', []))})")

# ---------- 3. PROGRAM_RESULTS.md (redacted copy) ----------
def build_program_results():
    src = ROOT / "mbal_program" / "reports" / "PROGRAM_RESULTS.md"
    if src.exists():
        (HERE / "downloads").mkdir(exist_ok=True)
        (HERE / "downloads" / "PROGRAM_RESULTS.md").write_text(redact(src.read_text(encoding="utf-8")), encoding="utf-8")
        print("PROGRAM_RESULTS.md copied (redacted)")
        return True
    print("PROGRAM_RESULTS.md not found — skipping")
    return False

# ---------- 4. downloads.json — EXPLICIT ALLOWLIST only ----------
def build_manifest(have_program):
    APACHE = "Apache-2.0"
    allow = [
        {"title": "Full snapshot (all findings, models, datasets, signals)", "path": "data.json", "type": "application/json", "license": APACHE},
        {"title": "Findings table (CSV)", "path": "downloads/findings.csv", "type": "text/csv", "license": APACHE},
        {"title": "Models registry (CSV)", "path": "downloads/models.csv", "type": "text/csv", "license": APACHE},
        {"title": "Monitoring-station coverage (GeoJSON)", "path": "stations.geojson", "type": "application/geo+json", "license": APACHE},
    ]
    if have_program:
        allow.append({"title": "Program results summary", "path": "downloads/PROGRAM_RESULTS.md", "type": "text/markdown", "license": APACHE})
    items = []
    for a in allow:
        p = HERE / a["path"]
        assert p.exists(), f"allowlisted file missing: {a['path']}"  # no dead links
        assert not a["path"].startswith(("data/", "../")), f"refused unsafe path: {a['path']}"
        items.append({**a, "bytes": p.stat().st_size, "sha256": sha256(p)})
    (HERE / "downloads.json").write_text(json.dumps({"items": items}, indent=1), encoding="utf-8")
    print(f"downloads.json: {len(items)} allowlisted artifacts")

def build_license():
    src = ROOT / "LICENSE"
    if src.exists() and not (HERE / "LICENSE").exists():
        (HERE / "LICENSE").write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        print("LICENSE copied from monorepo (Apache-2.0)")

def main():
    state = json.load(open(HERE / "data.json", encoding="utf-8"))["state"]
    build_stations()
    build_csvs(state)
    have = build_program_results()
    build_license()
    build_manifest(have)

if __name__ == "__main__":
    main()
