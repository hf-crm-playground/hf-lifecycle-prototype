"""Request bodies for the activation contract between the Decision Service
and Klaviyo (design, "Klaviyo activation").

- One `NBA Decision` event per dispatched email, push or WhatsApp decision.
  `unique_id` = decision_id, so a re-run hour can never send twice.
  The NBA Dispatcher flow triggers on it and branches on action/channel.
- Profile properties (`nba_*`, `trip_*`) for every logged decision: the
  in-app slot, the capture gate, the holdout segment and the explanation.
  Sent in bulk-import jobs of up to 10,000 profiles.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

from ..config import PolicyConfig
from ..policy import Decision
from .registry import NBA_METRIC_NAME

BULK_MAX_PROFILES = 10_000
BULK_MAX_BYTES = 4_500_000  # Klaviyo's limit is 5 MB; keep a margin


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _profile_ref(customer_id: str, profile_id: Optional[str] = None) -> Dict[str, Any]:
    """Identify the profile by its Klaviyo id when known. Verification found
    external_id on only 8 of 50 recently updated profiles, in two formats, so
    it is a fallback only; the service never writes external_id onto a
    profile that is addressed by id."""
    if profile_id:
        return {"data": {"type": "profile", "id": profile_id, "attributes": {}}}
    return {"data": {"type": "profile", "attributes": {"external_id": customer_id}}}


def nba_decision_event(d: Decision, dispatched_at: datetime, p: PolicyConfig,
                       profile_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Create Event body. None for decisions that are not messages Klaviyo
    sends (no message, in-app, native capture flows)."""
    if d.outcome != "send" or d.channel not in ("email", "push", "whatsapp"):
        return None
    props = {
        "decision_id": d.decision_id,
        "action": d.action,
        "content_key": d.content_key,
        "channel": d.channel,
        "trip_phase": d.trip_phase,
        "destination": d.destination,
        "locale": d.locale,
        "reason_codes": d.reason_codes,
        "experiment_id": d.experiment_id,
        "arm": d.arm,
        "tier": d.tier,
        "policy_version": d.policy_version,
        "expires_at": _iso(dispatched_at + timedelta(hours=p.nba_event_ttl_hours)),
    }
    return {
        "data": {
            "type": "event",
            "attributes": {
                "properties": props,
                "time": _iso(dispatched_at),
                "unique_id": d.decision_id,
                "metric": {"data": {"type": "metric", "attributes": {"name": NBA_METRIC_NAME}}},
                "profile": _profile_ref(d.customer_id, profile_id),
            },
        }
    }


def nba_profile_properties(d: Decision, now: datetime, p: PolicyConfig) -> Dict[str, Any]:
    sends = dict(d.cap_state)
    after = int(sends.get("used", 0)) + (1 if d.counts_toward_cap else 0)
    props: Dict[str, Any] = {
        "nba_holdout_group": "holdout" if d.arm == "holdout" else "treatment",
        "nba_last_action": d.content_key or "none",
        "nba_last_decision_id": d.decision_id,
        "nba_last_decision_at": _iso(d.decided_at),
        "nba_last_reason": d.sentence,
        "nba_sends_30d_total": after,
        "nba_policy_version": d.policy_version,
    }
    for ch in ("email", "push", "whatsapp"):
        props[f"nba_sends_30d_{ch}"] = int(sends.get(ch, 0)) + (1 if d.counts_toward_cap and d.channel == ch else 0)
    if d.outcome == "send" and d.channel == "in_app":
        props["nba_inapp_action"] = d.content_key
        props["nba_inapp_expires_at"] = _iso(now + timedelta(days=p.inapp_ttl_days))
    if d.outcome == "send" and d.action == "C1":
        props["nba_capture_allowed"] = True
        props["nba_capture_allowed_until"] = _iso(now + timedelta(hours=24))
    if d.flags.get("rating_prompt_allowed"):
        props["rating_prompt_allowed"] = True
    return props


def profile_import_body(customer_id: str, properties: Dict[str, Any],
                        profile_id: Optional[str] = None) -> Dict[str, Any]:
    data = _profile_ref(customer_id, profile_id)["data"]
    data["attributes"]["properties"] = properties
    return {"data": data}


def bulk_import_bodies(profiles: Iterable[Tuple[str, Dict[str, Any], Optional[str]]]) -> Iterator[Dict[str, Any]]:
    """Chunk (customer_id, properties, profile_id) into bulk-import job bodies
    within Klaviyo's 10,000-profile and 5 MB limits."""
    chunk: List[Dict[str, Any]] = []
    size = 0
    for cid, props, pid in profiles:
        item = profile_import_body(cid, props, pid)["data"]
        n = len(json.dumps(item, default=str))
        if chunk and (len(chunk) >= BULK_MAX_PROFILES or size + n > BULK_MAX_BYTES):
            yield _bulk(chunk)
            chunk, size = [], 0
        chunk.append(item)
        size += n
    if chunk:
        yield _bulk(chunk)


def _bulk(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "data": {
            "type": "profile-bulk-import-job",
            "attributes": {"profiles": {"data": items}},
        }
    }
