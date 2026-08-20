#!/usr/bin/env python3
"""Build the public HAB-relevant source catalog from Lab metadata only.

This script never reads table data pages. It uses registry records, validation
receipts, the protected source inventory, and (only when a receipt lacks a
schema) Parquet footers for the exact registered source path.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _iso_date(value: Any) -> str | None:
    if value in (None, "", "None"):
        return None
    text = str(value)
    match = re.match(r"(\d{4}-\d{2}-\d{2})", text)
    return match.group(1) if match else text[:40]


def _public_provider(spec: Any) -> str | None:
    for url in list(getattr(spec, "doc_urls", []) or []):
        lowered = str(url).lower()
        looks_like_direct_download = any(
            marker in lowered
            for marker in ("/download", ".csv", ".parquet", ".nc", ".zip")
        )
        if (
            isinstance(url, str)
            and url.startswith(("https://", "http://"))
            and not looks_like_direct_download
        ):
            return url
    # Never expose registry endpoints here: some are direct files, query APIs,
    # or templated download URLs. The static Site links only to documentation.
    return None


def _footer_columns(project_root: Path, status_row: dict[str, Any]) -> list[str]:
    """Read schema names only for the exact curated path recorded by the status matrix."""
    raw = status_row.get("curated_path")
    if not raw:
        return []
    path = project_root / str(raw).replace("\\", "/")
    try:
        import pyarrow.parquet as pq
    except ImportError:
        return []
    files: list[Path]
    if path.is_file() and path.suffix.lower() == ".parquet":
        files = [path]
    elif path.is_dir():
        files = sorted(path.glob("*.parquet"))
    else:
        files = []
    columns: list[str] = []
    seen: set[str] = set()
    for file_path in files[:100]:
        try:
            names = pq.ParquetFile(file_path).schema_arrow.names
        except Exception:
            continue
        for name in names:
            if name not in seen:
                seen.add(name)
                columns.append(name)
    return columns


def build_catalog(project_root: Path) -> dict[str, Any]:
    sys.path.insert(0, str(project_root))
    from ops.data_fetch.registry import REGISTRY  # pylint: disable=import-outside-toplevel
    from research.hab.north_america_hab_atlas import (  # pylint: disable=import-outside-toplevel
        build_source_ledger,
        build_support_ledger,
    )

    status_payload = _read_json(
        project_root / "reports/data_fetch/fetch_status_matrix.json", {"sources": []}
    )
    status_rows = {
        row["source"]: row
        for row in status_payload.get("sources", [])
        if row.get("source")
    }
    source_manifest = _read_json(
        project_root / "lakehouse/silver/external_curated/source_manifest.json", []
    )
    manifest_rows = {row.get("source"): row for row in source_manifest if row.get("source")}

    primary_frame, primary_fingerprints = build_source_ledger()
    support_frame, support_fingerprints = build_support_ledger()
    primary_rows = {
        str(row["source_key"]): row
        for row in primary_frame.to_dict("records")
        if bool(row.get("hab_scope_included"))
    }
    support_rows = {
        str(row["source_key"]): row for row in support_frame.to_dict("records")
    }
    overlap = set(primary_rows) & set(support_rows)
    if overlap:
        raise ValueError(f"HAB and environmental ledgers are not disjoint: {sorted(overlap)}")

    # These hydrology records entered the primary audit because their descriptions name a
    # possible HAB use. Their stored rows remain contextual hydrology, not HAB observations.
    context_overrides = {
        "ca_cdec_reservoir_ops": ("watershed_nutrients", "Watershed / nutrients"),
        "usgs_nwis_lake_levels": ("watershed_nutrients", "Watershed / nutrients"),
        "usbr_rise_reservoir_ops": ("watershed_nutrients", "Watershed / nutrients"),
        "upwelling_indices": ("water_physics", "Water physics"),
    }

    output_rows: list[dict[str, Any]] = []
    for key in sorted(set(primary_rows) | set(support_rows)):
        status = status_rows.get(key, {})
        spec = REGISTRY.get(key)
        if spec is None:
            raise KeyError(f"Audited catalog key is missing from the live registry: {key}")
        primary = primary_rows.get(key)
        support = support_rows.get(key)
        is_hab_source = primary is not None and key not in context_overrides
        title = str(getattr(spec, "title", "") or status.get("title") or key)
        validation_path = project_root / "reports" / "data_fetch" / key / "validation.json"
        validation = _read_json(validation_path, {})
        manifest = manifest_rows.get(key, {})
        if manifest.get("column_names"):
            columns = list(manifest["column_names"])
            schema_basis = "Lab source manifest"
        elif validation.get("column_names"):
            columns = list(validation["column_names"])
            schema_basis = "validation receipt"
        else:
            columns = _footer_columns(project_root, status)
            if columns:
                schema_basis = "Parquet footer"
            else:
                columns = list(getattr(spec, "required_columns", []) or [])
                schema_basis = "registered required fields only"
        if support:
            category_code = str(support.get("support_category") or "environmental_context")
            category_label = str(support.get("support_category_label") or "Environmental context")
        elif key in context_overrides:
            category_code, category_label = context_overrides[key]
        else:
            category_code = "hab_primary"
            category_label = "HAB audit-ledger source"

        role_code = str((primary or support or {}).get("display_role") or "unclassified")
        role_label = str((primary or support or {}).get("role_label") or "Unclassified")
        passed = validation.get("passed")
        if passed is True:
            validation_label = "passed"
        elif passed is False:
            validation_label = "failed"
        else:
            validation_label = "not_recorded"

        license_text = str(getattr(spec, "license", "") or "").strip()
        output_rows.append(
            {
                "source_id": key,
                "name": title,
                "relationship": "HAB-audited source" if is_hab_source else "environmental context",
                "category": category_code,
                "category_label": category_label,
                "role": role_code,
                "role_label": role_label,
                "inclusion_basis": (
                    "Frozen HAB source-scope audit"
                    if is_hab_source
                    else "Primary HAB audit entry reclassified as environmental context"
                    if primary is not None
                    else "Separate environmental-support audit"
                ),
                "rows": int(status.get("rows") or validation.get("rows") or 0),
                "column_count": len(columns) or int(status.get("columns") or 0),
                "columns": columns,
                "date_start": _iso_date(status.get("date_min") or validation.get("date_min")),
                "date_end": _iso_date(status.get("date_max") or validation.get("date_max")),
                "acquisition_status": status.get("status") or getattr(spec, "default_status", None),
                "validation": validation_label,
                "approved_for_modeling": bool(manifest.get("ready_for_modeling", False)),
                "provider_url": _public_provider(spec),
                "reuse_rights": license_text or "Not verified in the Lab registry",
                "schema_basis": schema_basis,
            }
        )

    output_rows.sort(key=lambda row: (row["relationship"] != "HAB-audited source", row["name"].lower()))
    category_labels = {
        row["category"]: row["category_label"] for row in output_rows
    }
    category_counts = Counter(row["category"] for row in output_rows)
    hab_count = sum(row["relationship"] == "HAB-audited source" for row in output_rows)
    landed_count = sum(row["rows"] > 0 for row in output_rows)
    return {
        "generated_utc": datetime.fromtimestamp(
            (project_root / "reports/data_fetch/fetch_status_matrix.json").stat().st_mtime,
            timezone.utc,
        ).replace(microsecond=0).isoformat(),
        "scope": {
            "title": "MBAL harmful algal bloom data catalog",
            "selection": (
                "Registered sources whose recorded title, description, columns or units "
                "passed the frozen HAB holdings or environmental-support scope contracts, refreshed "
                "against the current source registry and fetch receipts. Inclusion is not evidence "
                "of correlation, causation, model usefulness or permission for commercial reuse."
            ),
            "row_definition": (
                "Rows are source-specific stored records; one row is not necessarily one sample, "
                "one location or one independent event."
            ),
            "columns_definition": (
                "For landed sources, column names are measured schema metadata. Registered-only "
                "sources show planned or required fields and are labeled as such. Unless a source "
                "has a verified deep dictionary, names alone do not establish meaning or units."
            ),
            "readiness_definition": (
                "Approved for modeling is a separate Lab gate. A validation receipt passing means "
                "the landed table passed its registered structural checks, not that every scientific "
                "use is valid."
            ),
        },
        "summary": {
            "sources": len(output_rows),
            "hab_sources": hab_count,
            "context_sources": len(output_rows) - hab_count,
            "landed_sources": landed_count,
            "registered_only_sources": len(output_rows) - landed_count,
            "recorded_rows": sum(row["rows"] for row in output_rows),
            "validation_passed": sum(row["validation"] == "passed" for row in output_rows),
            "approved_for_modeling": sum(row["approved_for_modeling"] for row in output_rows),
            "category_counts": dict(sorted(category_counts.items())),
        },
        "category_labels": category_labels,
        "scope_fingerprints": {
            **primary_fingerprints,
            **support_fingerprints,
        },
        "sources": output_rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = build_catalog(args.project_root.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(
        f"wrote {args.output}: {payload['summary']['sources']} sources, "
        f"{payload['summary']['recorded_rows']:,} recorded rows"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
