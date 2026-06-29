#!/usr/bin/env python3
"""Generate the static map artifacts for Mission Control.

HONESTY SCOPE (critic-gated):
  * stations.geojson = WHERE THE OBSERVATIONS ARE (monitoring coverage), NOT a model
    prediction surface. The deployed bacteria model is a per-station tabular nowcast;
    the lab's own `bacteria_hgbt_spatial` is a registered WASH, so there is NO honest
    continuous confidence grid. We show coverage + observed exceedance rate + support.
  * Raw public data downloads are intentionally disabled. The static site can still
    render governed summaries and map coverage, but rebuilds must not recreate CSV,
    Markdown, or full-snapshot download bundles.

Run from mbal-mission-control/ with the monorepo as the parent. Requires duckdb.
Re-run when the station geo or master observations change. Front-end reads the
emitted map layers; it never hard-codes raw data download links.
"""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
EXCEED_MPN = 104  # AB411 single-sample enterococcus standard

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

# ---------- 1b. lakes_cyano.geojson (US lakes — coverage only; public EPA CyAN) ----------
def build_cyano_lakes():
    import duckdb
    con = duckdb.connect()
    src = ROOT / "reports" / "hab" / "cyano" / "cyano_discovery_lakeyear.parquet"
    if not src.exists():
        print("cyano lake-year parquet not found — skipping lakes domain")
        return
    rows = con.execute(f"""
        select lake_id, any_value(lake_name) as lk_name, any_value(state) as lk_state,
               avg(lat) as lk_lat, avg(lon) as lk_lon,
               sum(obs_weeks) as n_obs,
               sum(bloom_weeks) * 1.0 / nullif(sum(obs_weeks), 0) as bloom_rate
        from read_parquet('{src.as_posix()}')
        where lat is not null and lon is not null
        group by lake_id
    """).fetchall()
    feats = []
    for lid, name, state, lat, lon, n, br in rows:
        n = int(n or 0)
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [round(float(lon), 5), round(float(lat), 5)]},
            "properties": {
                "station_id": lid, "name": name or lid, "county": state or "",
                "n_obs": n, "base_rate": (round(float(br), 4) if br is not None else None),
                "low_support": n < 20, "hazard": "cyano",
                "n_test": 0, "events": 0, "model_auc": None, "mem_auc": None,
            },
        })
    fc = {"type": "FeatureCollection",
          "meta": {"hazard": "cyano", "stations": len(feats), "scored_stations": 0,
                   "unit": "obs-weeks", "value_label": "bloom frequency", "has_skill": False,
                   "note": "US lakes from the public EPA CyAN satellite record. Marker size = weeks "
                           "observed; color = fraction of observed weeks with a cyanobacteria bloom. "
                           "Coverage only — the cyano work is onset/triage, not a per-lake exceedance "
                           "classifier, so there is no honest per-lake model-skill layer here."},
          "features": feats}
    (HERE / "lakes_cyano.geojson").write_text(json.dumps(fc, separators=(",", ":")), encoding="utf-8")
    print(f"lakes_cyano.geojson: {len(feats)} US lakes")


# ---------- 1c. domoic-acid piers (CalHABMAP/SCCOOS — public; coverage only) ----------
def build_da_piers():
    import duckdb
    con = duckdb.connect()
    src = ROOT / "reports" / "hab" / "model_queue" / "panels" / "hab_sota_panel.parquet"
    if not src.exists():
        print("DA panel not found — skipping piers domain"); return
    rows = con.execute(f"""
        select station as sid, avg(latitude) as lk_lat, avg(longitude) as lk_lon,
               count(*) as n_obs,
               avg(case when try_cast(exceed as double) > 0 then 1.0 else 0.0 end) as base_rate
        from read_parquet('{src.as_posix()}')
        where latitude is not null and longitude is not null and station is not null
        group by station
    """).fetchall()
    feats = [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(float(lon), 5), round(float(lat), 5)]},
              "properties": {"station_id": sid, "name": sid, "county": "", "n_obs": int(n),
                             "base_rate": (round(float(br), 4) if br is not None else None),
                             "low_support": int(n) < 30, "hazard": "domoic", "n_test": 0, "events": 0,
                             "model_auc": None, "mem_auc": None}}
             for sid, lat, lon, n, br in rows]
    fc = {"type": "FeatureCollection",
          "meta": {"hazard": "domoic", "stations": len(feats), "scored_stations": 0,
                   "unit": "samples", "value_label": "domoic-acid exceedance", "has_skill": False,
                   "note": "CalHABMAP / SCCOOS pier network (public). Marker size = samples; color = "
                           "share over the domoic-acid action level. Coverage only — note how sparse "
                           "the pier network is; that sparsity is itself a limit on the forecast."},
          "features": feats}
    (HERE / "piers_domoic.geojson").write_text(json.dumps(fc, separators=(",", ":")), encoding="utf-8")
    print(f"piers_domoic.geojson: {len(feats)} piers")


# ---------- 1d. Ireland bathing water (public EU open data; coverage only) ----------
def build_ireland():
    import duckdb
    con = duckdb.connect()
    src = ROOT / "data" / "external_curated" / "ireland_bathing_water" / "ireland_bathing_water.parquet"
    if not src.exists():
        print("Ireland parquet not found — skipping"); return
    rows = con.execute(f"""
        select beach_id as sid, any_value(beach_name) as nm, any_value(county_name) as cty,
               avg(latitude) as lk_lat, avg(longitude) as lk_lon, count(*) as n_obs,
               avg(case when sample_water_quality_status not in ('Excellent','Good') then 1.0 else 0.0 end) as base_rate
        from read_parquet('{src.as_posix()}')
        where latitude is not null and longitude is not null and beach_id is not null
        group by beach_id
    """).fetchall()
    feats = [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(float(lon), 5), round(float(lat), 5)]},
              "properties": {"station_id": sid, "name": nm or sid, "county": cty or "", "n_obs": int(n),
                             "base_rate": (round(float(br), 4) if br is not None else None),
                             "low_support": int(n) < 10, "hazard": "ireland", "n_test": 0, "events": 0,
                             "model_auc": None, "mem_auc": None}}
             for sid, nm, cty, lat, lon, n, br in rows]
    fc = {"type": "FeatureCollection",
          "meta": {"hazard": "ireland", "stations": len(feats), "scored_stations": 0,
                   "unit": "samples", "value_label": "below-Good rate", "has_skill": False,
                   "note": "Ireland EPA designated bathing waters (public EU open data). Marker size = "
                           "samples; color = share of samples classified below 'Good'. This is the "
                           "region the CA bacteria model is transferred to — coverage only here."},
          "features": feats}
    (HERE / "ireland_beaches.geojson").write_text(json.dumps(fc, separators=(",", ":")), encoding="utf-8")
    print(f"ireland_beaches.geojson: {len(feats)} beaches")


# ---------- 2. downloads.json — intentionally empty ----------
def build_manifest():
    (HERE / "downloads.json").write_text(
        json.dumps(
            {
                "items": [],
                "downloads_enabled": False,
                "note": "Raw public downloads are disabled. Use the site UI for governed summaries and request controlled raw artifacts through the lab.",
            },
            indent=1,
        ),
        encoding="utf-8",
    )
    print("downloads.json: raw public downloads disabled")

def build_license():
    src = ROOT / "LICENSE"
    if src.exists() and not (HERE / "LICENSE").exists():
        (HERE / "LICENSE").write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        print("LICENSE copied from monorepo (Apache-2.0)")

def main():
    build_stations()
    build_cyano_lakes()
    build_da_piers()
    build_ireland()
    build_license()
    build_manifest()

if __name__ == "__main__":
    main()
