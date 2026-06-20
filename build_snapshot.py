#!/usr/bin/env python3
"""
Build the static data snapshot for the public site.

Pulls the REAL state from the running Mission Control cockpit (http://127.0.0.1:8770)
and bundles the evidence files findings/models reference, so the published site shows
the same data + drill-downs offline. Re-run this whenever you want to refresh the
published snapshot, then commit + push data.json.

Usage:  python build_snapshot.py
"""
import json
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

COCKPIT = "http://127.0.0.1:8770"
HERE = Path(__file__).resolve().parent


def _get(path):
    with urllib.request.urlopen(COCKPIT + path, timeout=30) as r:
        return json.loads(r.read().decode())


def _public(state):
    """Keep the science in full; drop local-machine ops + scrub absolute paths so the
    public site can't leak the user's filesystem / username / process list."""
    keep = ("findings", "models", "gates", "data_assets", "next_actions",
            "overall_status", "snapshot_generated", "counts")
    out = {k: state.get(k) for k in keep if k in state}
    # lakehouse inventory: keep dataset names/sizes/sources, drop absolute paths
    inv = state.get("inventory", {}) or {}
    def clean_store(s):
        if not s:
            return s
        ds = []
        for d in s.get("datasets", []):
            d = dict(d)
            d.pop("path", None)
            d.pop("store_root", None)
            ds.append(d)
        s = dict(s)
        s["datasets"] = ds
        return s
    out["inventory"] = {"lake": clean_store(inv.get("lake")),
                        "shadow": clean_store(inv.get("shadow")),
                        "built_iso": inv.get("built_iso")}
    # git: branch + last commit message only (drop sample paths / surface lists)
    g = state.get("git", {}) or {}
    out["git"] = {k: g.get(k) for k in ("branch", "risk", "changed_path_count",
                                        "review_surface_count", "last_commit")}
    # findings/models: drop nothing of the science, but blank absolute evidence paths -> keep relative only
    return out


def main():
    state = _public(_get("/api/state"))

    # bundle the evidence JSON each finding/model points at (so drill-downs work offline)
    evidence = {}
    paths = set()
    for f in state.get("findings", []):
        if f.get("evidence"):
            paths.add(f["evidence"])
    for m in state.get("models", []):
        if m.get("evidence"):
            paths.add(m["evidence"])
    for p in paths:
        try:
            j = _get("/api/finding?evidence=" + urllib.parse.quote(p))
            if j.get("data") is not None:
                evidence[p] = j["data"]
        except Exception:
            pass

    snap = {
        "built_iso": datetime.now(timezone.utc).isoformat(),
        "built_pacific": time.strftime("%Y-%m-%d %H:%M %Z"),
        "state": state,
        "evidence": evidence,
    }
    out = HERE / "data.json"
    text = json.dumps(snap, separators=(",", ":"))
    # final scrub: strip any absolute local prefixes that survived inside strings
    import os
    import re
    user = os.environ.get("USERNAME") or os.environ.get("USER") or ""
    # strip absolute local prefixes (generic user segment) so no machine paths/usernames leak
    text = re.sub(r"C:[\\\\/]+Users[\\\\/]+[^\\\\/\"]+[\\\\/]+AI-Machine[\\\\/]+(?:projects|_deagent)?[\\\\/]+", "", text)
    text = re.sub(r"C:[\\\\/]+Users[\\\\/]+[^\\\\/\"]+[\\\\/]+", "", text)
    if user:
        text = text.replace(user, "lab")
    out.write_text(text, encoding="utf-8")
    kb = round(out.stat().st_size / 1024, 1)
    print(f"wrote {out}  ({kb} KB)  · findings={len(state.get('findings',[]))} "
          f"models={len(state.get('models',[]))} evidence_files={len(evidence)}")


if __name__ == "__main__":
    import urllib.parse  # noqa: E402
    main()
