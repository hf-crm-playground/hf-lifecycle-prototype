"""Map a TripSnapshot to the Klaviyo profile properties named in the design
document's schema, and to a Create-or-Update-Profile request body.

Klaviyo date properties must be ISO 8601 strings; booleans and numbers are
passed as-is. Property names are prefixed `trip_` / `nba_` exactly as in the
schema so segments and flow filters can be built on them.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, Optional

from .models import TripSnapshot


def _iso(v: Optional[object]) -> Optional[str]:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    return str(v)


def to_profile_properties(snap: TripSnapshot) -> Dict[str, Any]:
    """Flat dict of profile properties. None values are omitted so that a
    property cleared upstream is not overwritten with an empty string; use a
    separate "Remove property" call when a field must be unset."""
    props: Dict[str, Any] = {
        "trip_state": snap.trip_state,
        "trip_confidence": snap.trip_confidence,
        "trip_id": snap.trip_id,
        "trip_started_at": _iso(snap.trip_started_at),
        "trip_destination": snap.trip_destination,
        "trip_days_in_trip": snap.days_in_trip,
        "trip_days_since_last_usage": snap.days_since_last_usage,
        "trip_usage_fresh": snap.usage_fresh,
        "trip_data_remaining_pct": snap.data_remaining_pct,
        "trip_projected_runout_at": _iso(snap.projected_runout_at),
        "trip_plan_end_at": _iso(snap.plan_end_at),
        "trip_plan_ends_within_48h": snap.plan_ends_within_48h,
        "trip_pending_activation": snap.pending_activation,
        "trip_test_install_detected": snap.test_install_detected,
        "trip_departure_likely": snap.departure_likely,
        "trip_long_trip": snap.long_trip,
        "trips_completed": snap.trips_completed,
        "trip_last_ended_day": _iso(snap.last_trip_ended_day),
        "trip_state_as_of": _iso(snap.as_of),
        "trip_state_reason": "; ".join(snap.reasons)[:500],
    }
    return {k: v for k, v in props.items() if v is not None}


def to_profile_import_body(snap: TripSnapshot, external_id: str,
                           email: Optional[str] = None) -> Dict[str, Any]:
    """Request body for Klaviyo's Create or Update Profile endpoint
    (POST /api/profile-import/). The caller adds headers and revision."""
    attributes: Dict[str, Any] = {
        "external_id": external_id,
        "properties": to_profile_properties(snap),
    }
    if email:
        attributes["email"] = email
    return {"data": {"type": "profile", "attributes": attributes}}
