#!/usr/bin/env python3
"""Enrich data.json so every dataset carries the SIGNALS it actually contains (its parquet columns).

Reads each state.data_assets[*].path parquet schema from the projects root and adds:
  asset["signals"]    -> list of column names (the measured/derived fields in that dataset)
  asset["n_signals"]  -> count
Read-only on the parquet (schema only, no data). Idempotent. Run standalone after build_snapshot,
or import enrich(state) to fold into the build. Usage: python enrich_dataset_signals.py
"""
from __future__ import annotations
import json, os
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = Path(os.environ.get("MBAL_ROOT", HERE.parent)).resolve()  # projects root
DATA = HERE / "data.json"
try:
    import pyarrow.parquet as pq
except Exception:
    pq = None

# columns that are keys/plumbing rather than a "signal" — hidden from the signal list
_SKIP = {"index", "__index_level_0__", "level_0", "unnamed: 0"}


def _columns(rel_path: str):
    if not pq or not rel_path:
        return None
    p = (ROOT / rel_path)
    try:
        if p.is_file() and p.suffix == ".parquet":
            f = p
        elif p.is_dir():
            files = sorted(p.rglob("*.parquet"))
            if not files:
                return None
            f = files[0]
        else:
            return None
        names = list(pq.ParquetFile(f).schema.names)
        return [c for c in names if c.lower() not in _SKIP]
    except Exception:
        return None


def enrich(state: dict) -> int:
    n = 0
    for a in state.get("data_assets") or []:
        cols = _columns(a.get("path"))
        if cols:
            a["signals"] = cols
            a["n_signals"] = len(cols)
            n += 1
    return n


def main() -> int:
    doc = json.loads(DATA.read_text(encoding="utf-8"))
    state = doc.get("state", doc)
    n = enrich(state)
    DATA.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    total = sum(a.get("n_signals", 0) for a in state.get("data_assets") or [])
    print(f"enriched {n} datasets with per-dataset signals ({total} column-signals total) -> {DATA}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
