"""Integration seam between the prospective-pilot ledger and the lab's real code.

This is the ONE file the operator wires up at home. The ledger (run_pilot.py) is
model- and data-agnostic on purpose; everything that touches the frozen model,
the CalHABMAP feed, and the lab's scorer lives here.

Three functions to implement. Each has a working synthetic version below so the
harness self-test runs today without the real model; replace the bodies marked
TODO with calls into the research tree.

Do NOT re-implement average precision / bootstrap / permutation in score(). Call
the same scorer that produced reports/hab/da_forecast.json, so the prospective
numbers are comparable to the retrospective ones by construction.
"""

from __future__ import annotations

import hashlib
from typing import Any


# --------------------------------------------------------------------------- #
# REAL ADAPTER — fill these three in at home.                                  #
# --------------------------------------------------------------------------- #

class Adapter:
    """Points the ledger at the frozen model, the live feed, and the lab scorer."""

    def predict(self, decision_utc: str) -> list[dict[str, Any]]:
        """Return one prediction row per prior-clean station due for a call today.

        Each row MUST contain:
            station_id          : str
            target_visit_date   : str  (YYYY-MM-DD of the visit being predicted)
            model_score         : float (frozen da_forecast_hgbt risk, higher = riskier)
            baseline_score      : float (pda_roll3_value for the same station/visit)
            prior_clean         : bool  (True only if the station qualifies)

        Only eligible (prior_clean == True) stations should be returned; the pilot
        claim is scoped to prior-clean onset.
        """
        # TODO(home): load the frozen model from the research tree, pull the current
        # feature rows for prior-clean stations from the CalHABMAP feed, and return
        # model + baseline scores. Must use ONLY features available before the visit.
        raise NotImplementedError("wire predict() to the frozen model + live feed")

    def resolve_outcomes(self, pending: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Given pending predictions, return outcomes for any now-sampled visits.

        `pending` rows carry (station_id, target_visit_date). Return one row per
        RESOLVED visit with:
            station_id          : str
            target_visit_date   : str  (must match the prediction)
            outcome_utc         : str  (ISO time the lab result became available)
            da_value            : float
            is_onset            : int  (1 if toxic onset per the frozen definition, else 0)
            event_id            : str  (station x onset episode; the bootstrap cluster unit)

        Skip visits that have not been sampled yet — they stay pending.
        """
        # TODO(home): read the latest CalHABMAP lab results, apply the SAME onset
        # definition as reports/hab/da_forecast.json (see pilot_config.json), and
        # label each resolved visit.
        raise NotImplementedError("wire resolve_outcomes() to the CalHABMAP results feed")

    def score(self, rows: list[dict[str, Any]], config: dict[str, Any]) -> dict[str, Any]:
        """Score resolved (prediction joined to outcome) rows with the LAB scorer.

        Each row has: model_score, baseline_score, is_onset, event_id.
        Return a dict with at least:
            n_events   : int   (independent onset events; the power count)
            d_ap       : float (AP(model) - AP(baseline))
            d_ap_ci95  : [float, float]  (clustered by event_id)
            perm_p     : float (one-sided; model beats baseline by chance)
        """
        # TODO(home): call the same average-precision / clustered-bootstrap /
        # permutation code that produced reports/hab/da_forecast.json. Do not write
        # a second implementation here.
        raise NotImplementedError("delegate score() to the lab's existing scorer")


# --------------------------------------------------------------------------- #
# SYNTHETIC ADAPTER — used only by `run_pilot.py selftest`. Proves the PLUMBING #
# (append-only logging, leakage-safe join, accrual counts). NOT a stats check.  #
# --------------------------------------------------------------------------- #

class SyntheticAdapter(Adapter):
    """Deterministic fake so the harness runs end-to-end without the real model."""

    def __init__(self, n_stations: int = 6):
        self.n_stations = n_stations

    @staticmethod
    def _h(*parts: str) -> float:
        raw = "|".join(parts).encode()
        return int(hashlib.sha256(raw).hexdigest(), 16) % 1000 / 1000.0

    def predict(self, decision_utc: str) -> list[dict[str, Any]]:
        day = decision_utc[:10]
        rows = []
        for i in range(self.n_stations):
            st = f"SYN-{i:02d}"
            rows.append({
                "station_id": st,
                "target_visit_date": day,
                # fake but deterministic; model is mildly informative vs baseline
                "model_score": round(0.55 * self._h(st, day, "m") + 0.45 * self._h(st, day, "truth"), 4),
                "baseline_score": round(self._h(st, day, "b"), 4),
                "prior_clean": True,
            })
        return rows

    def resolve_outcomes(self, pending: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for p in pending:
            st, day = p["station_id"], p["target_visit_date"]
            onset = 1 if self._h(st, day, "truth") > 0.7 else 0
            out.append({
                "station_id": st,
                "target_visit_date": day,
                "outcome_utc": f"{day}T23:59:00+00:00",  # after any same-day decision
                "da_value": round(10 * self._h(st, day, "truth"), 3),
                "is_onset": onset,
                "event_id": f"{st}:{day}",
            })
        return out

    def score(self, rows: list[dict[str, Any]], config: dict[str, Any]) -> dict[str, Any]:
        # Plumbing stub only. Returns shape-correct placeholders so the CLI path
        # runs; the REAL adapter must return values from the lab scorer.
        n_events = len({r["event_id"] for r in rows if r.get("is_onset") == 1})
        return {
            "n_events": n_events,
            "d_ap": None,
            "d_ap_ci95": None,
            "perm_p": None,
            "_synthetic": True,
            "_note": "selftest plumbing stub; not a statistical result",
        }


def get_adapter(synthetic: bool = False) -> Adapter:
    return SyntheticAdapter() if synthetic else Adapter()
