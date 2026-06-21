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

# ---------- 1. stations.geojson (coverage, not prediction) ----------
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
    feats = []
    for sid, name, county, lat, lon, n, br in rows:
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [round(float(lon), 5), round(float(lat), 5)]},
            "properties": {
                "station_id": sid, "name": name or sid, "county": county or "",
                "n_obs": int(n), "base_rate": (round(float(br), 4) if br is not None else None),
                "low_support": int(n) < 30, "hazard": "bacteria",
            },
        })
    fc = {"type": "FeatureCollection",
          "meta": {"hazard": "bacteria", "exceedance_threshold_mpn": EXCEED_MPN,
                   "note": "Monitoring stations with a known location. Marker size = number of "
                           "lab samples; color = observed AB411 exceedance rate (>104 MPN/100mL); "
                           "hatched/low = fewer than 30 samples (thin support). This is observation "
                           "coverage, not a model prediction surface.",
                   "stations": len(feats)},
          "features": feats}
    (HERE / "stations.geojson").write_text(json.dumps(fc, separators=(",", ":")), encoding="utf-8")
    nz = sum(1 for f in feats if f["properties"]["n_obs"] >= 30)
    print(f"stations.geojson: {len(feats)} stations ({nz} with >=30 samples)")
    # sanity on base rate
    brs = [f["properties"]["base_rate"] for f in feats if f["properties"]["base_rate"] is not None]
    if brs:
        print(f"  base_rate range {min(brs):.3f}–{max(brs):.3f} mean {sum(brs)/len(brs):.3f}")

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
