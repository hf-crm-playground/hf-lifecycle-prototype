"""Versioned feature schema for learning.

Every decision logs `feature_vector` under `FEATURE_VERSION`. The values come
only from the DecisionContext, which is frozen at decided_at, so a model
trained on the log never sees information from after the decision (no
leakage). Changing a feature means bumping the version; the trainer only
mixes rows of one version.

`cell()` is the coarse context the v1 learner conditions on. It is kept small
on purpose (phase x value tier x app x region = 144 cells) so each cell sees
enough traffic, and every cell falls back to its parent (the action overall)
until it has data. Richer models can use the full vector later.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Tuple

from .context import DecisionContext

FEATURE_VERSION = "f1"

LATAM = {"MX", "CO", "AR", "CL", "PE", "BR", "UY", "EC", "BO", "CR", "PA", "GT", "DO"}
EUROPE = {"ES", "FR", "DE", "IT", "PT", "GB", "NL", "BE", "AT", "CH", "IE", "PL", "SE", "DK", "NO", "FI", "GR"}


def region(market: str | None) -> str:
    m = (market or "").upper()
    if m == "US":
        return "us"
    if m in LATAM:
        return "latam"
    if m in EUROPE:
        return "europe"
    return "other"


def value_tier(c: DecisionContext) -> str:
    clv = c.s.predicted_clv
    if clv is not None:
        return "low" if clv < 30 else ("mid" if clv < 100 else "high")
    return "low" if c.trips_completed == 0 else ("mid" if c.trips_completed == 1 else "high")


def _bucket(v: float | None, edges: Tuple[float, ...]) -> str:
    if v is None:
        return "na"
    for e in edges:
        if v < e:
            return f"<{e:g}"
    return f">={edges[-1]:g}"


# name -> extractor. Booleans and small categoricals only: no ids, no free text.
FEATURES: Dict[str, Callable[[DecisionContext], Any]] = {
    "phase": lambda c: c.phase,
    "trip_confidence": lambda c: c.trip_confidence,
    "region": lambda c: region(c.market),
    "value_tier": value_tier,
    "trips_completed": lambda c: min(c.trips_completed, 5),
    "days_in_trip_b": lambda c: _bucket(c.days_in_trip, (3, 7, 15, 30)),
    "data_left_b": lambda c: _bucket(c.data_remaining_pct, (10, 20, 40, 70)),
    "runout_before_end": lambda c: c.runout_before_plan_end,
    "plan_end_b": lambda c: _bucket(c.plan_end_hours, (24, 48, 168)),
    "local_hour_known": lambda c: c.local_tz is not None,
    "app_installed": lambda c: c.s.app_installed,
    "app_active_14d": lambda c: c.s.in_app_session_14d,
    "push_ok": lambda c: c.s.push_token,
    "email_ok": lambda c: c.s.consent_email,
    "whatsapp_ok": lambda c: c.s.consent_whatsapp,
    "first_channel": lambda c: c.s.first_purchase_channel,
    "always_on": lambda c: c.s.always_on_status,
    "plan_type": lambda c: c.s.plan_type,
    "coupon_user": lambda c: c.s.coupon_user,
    "paid_share_b": lambda c: _bucket(c.s.paid_share, (0.15, 0.3, 0.5)),
    "churn_b": lambda c: _bucket(c.s.churn_probability, (0.2, 0.5, 0.8)),
    "nps_group": lambda c: "na" if c.s.nps_score is None else ("promoter" if c.s.nps_score >= 9 else ("passive" if c.s.nps_score >= 7 else "detractor")),
    "sends_30d": lambda c: min(c.h.sends_30d.get("total", 0), 8),
    "hours_since_send_b": lambda c: _bucket(c.h.hours_since_last_send, (24, 72, 168, 720)),
    "fatigued_any": lambda c: bool(c.h.fatigued_keys),
}


def feature_vector(c: DecisionContext) -> Dict[str, Any]:
    return {name: f(c) for name, f in FEATURES.items()}


def cell(c: DecisionContext) -> str:
    return "|".join((c.phase, value_tier(c), "app" if c.s.app_installed else "web", region(c.market)))


def schema() -> List[str]:
    return list(FEATURES)
