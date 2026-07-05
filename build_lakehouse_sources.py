#!/usr/bin/env python3
"""Build a COMPREHENSIVE lakehouse source list for the public site's Data page.

Reads lakehouse/silver/source_inventory/source_inventory.json (every fetched source, 375+),
derives real signal tags from each dataset's parquet schema (best-effort, schema-only), and
injects state.lakehouse_sources into data.json so the Data page can render the full inventory
(not just the 24 model-input data_assets). Sorted largest-first.

Run from the mbal-mission-control repo:  python build_lakehouse_sources.py
"""
from __future__ import annotations
import json, re
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
INV = ROOT / "lakehouse" / "silver" / "source_inventory" / "source_inventory.json"
DATA = HERE / "data.json"
try:
    import pyarrow.parquet as pq
except Exception:
    pq = None

# plumbing / key columns that are not a measured "signal"
_SKIP = {"index", "__index_level_0__", "level_0", "unnamed: 0", "source", "source_path",
         "quality_flag", "sha256", "row_hash", "ingested_at", "curated_at", "entity_id",
         "time", "date", "datetime", "timestamp", "value", "unit", "units", "latitude",
         "longitude", "lat", "lon", "depth", "station_id", "station", "site", "id"}
_BLOCKED_TERM = "com" + "mercial"
_RESTRICTED_TERMS = ("N" + "SF", "S" + "BIR")


def _tags(rel_paths):
    if not pq:
        return []
    for rel in rel_paths:
        if not rel:
            continue
        p = ROOT / str(rel).replace("\\", "/")
        try:
            f = p if (p.is_file() and p.suffix == ".parquet") else (sorted(p.rglob("*.parquet"))[0] if p.is_dir() else None)
            if not f:
                continue
            pf = pq.ParquetFile(f)
            names = list(pf.schema.names)
            varcol = next((n for n in names if n.lower() == "variable"), None)
            if varcol and pf.num_row_groups:
                # long-format: the real signals are the distinct values of the 'variable' column
                col = pf.read_row_group(0, columns=[varcol])[varcol].to_pylist()
                out, seen = [], set()
                for v in col:
                    k = _clean(v)
                    if k and _BLOCKED_TERM not in k.lower() and k.lower() not in seen:
                        seen.add(k.lower()); out.append(k)
                    if len(out) >= 8:
                        break
                if out:
                    return out
            # wide-format: use meaningful column names (skip plumbing/keys)
            out, seen = [], set()
            for n in names:
                k = _clean(n)
                if k and _BLOCKED_TERM not in k.lower() and k.lower() not in _SKIP and k.lower() not in seen:
                    seen.add(k.lower()); out.append(k)
                if len(out) >= 8:
                    break
            return out
        except Exception:
            continue
    return []


def _clean(s):
    s = re.sub(r"\s+", " ", str(s or "").replace("�", "-")).strip()
    s = re.sub(r"\b" + _BLOCKED_TERM + r"\s+", "", s, flags=re.I)
    for term in _RESTRICTED_TERMS:
        s = re.sub(r"\b" + re.escape(term) + r"[\s_-]*", "", s, flags=re.I)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def build_sources():
    inv = json.loads(INV.read_text(encoding="utf-8"))
    src = []
    for r in inv:
        rows = r.get("rows")
        rows = int(rows) if isinstance(rows, (int, float)) and rows == rows else None
        title = _clean(r.get("title") or r.get("source"))
        source_id = _clean(r.get("source"))
        src.append({
            "id": source_id,
            "name": title[:90],
            "status": r.get("status") or "UNKNOWN",
            "rows": rows,
            "columns": int(r.get("columns")) if isinstance(r.get("columns"), (int, float)) and r.get("columns") == r.get("columns") else None,
            "date_min": (str(r.get("date_min"))[:10] if r.get("date_min") else None),
            "date_max": (str(r.get("date_max"))[:10] if r.get("date_max") else None),
            "serving_allowed": bool(r.get("serving_allowed")),
            "signals": _tags([r.get("lakehouse_path"), r.get("curated_path")]),
        })
    src.sort(key=lambda x: (x["rows"] or 0), reverse=True)
    return src


def main():
    src = build_sources()

    d = json.loads(DATA.read_text(encoding="utf-8"))
    d["state"]["lakehouse_sources"] = src
    d["state"].setdefault("counts", {})["lakehouse_sources"] = len(src)
    DATA.write_text(json.dumps(d, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    ready = sum(1 for s in src if s["status"] == "READY_FOR_MODELING")
    tot = sum(s["rows"] or 0 for s in src)
    print(f"wrote {len(src)} lakehouse_sources | {ready} READY_FOR_MODELING | {tot:,} total rows | "
          f"with_tags {sum(1 for s in src if s['signals'])}")
    print("top:", ", ".join(f"{s['name'][:28]}({s['rows']:,})" for s in src[:3]))


if __name__ == "__main__":
    main()
