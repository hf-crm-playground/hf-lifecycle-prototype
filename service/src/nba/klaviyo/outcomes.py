"""Pull message events from Klaviyo into the cap ledger (nightly, and before
each hourly run for the last few hours).

Only routing fields are kept: event id, profile id (and external_id when the
profile has one), time, channel, kind, flow id, message id and message type.
Subjects, addresses and every other property are dropped on read, and the
profile is fetched with a sparse fieldset (`fields[profile]=external_id`)
so no email or phone number ever reaches the service.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

from ..store import Store
from .client import KlaviyoClient
from .registry import MESSAGE_METRICS, TRANSACTIONAL_FLOW_IDS


LATE_OVERLAP = timedelta(hours=48)


def _iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def events_params(metric_id: str, since: datetime, until: Optional[datetime] = None,
                  page_size: int = 1000, profile_fields: str = "external_id") -> Dict[str, str]:
    flt = f'equals(metric_id,"{metric_id}"),greater-or-equal(datetime,{_iso_z(since)})'
    if until:
        flt += f",less-than(datetime,{_iso_z(until)})"
    return {
        "filter": f"and({flt})",
        "fields[event]": "datetime,event_properties",
        "include": "profile",
        "fields[profile]": profile_fields,
        "sort": "datetime",
        "page[size]": str(page_size),
    }


def profile_key(rec: Dict[str, Any], included: Dict[Tuple[str, str], Dict[str, Any]]) -> Tuple[Optional[str], Optional[str]]:
    """(profile_id, external_id) of an event record."""
    rel = ((rec.get("relationships") or {}).get("profile") or {}).get("data") or {}
    pid = rel.get("id")
    ext = None
    if pid and ("profile", pid) in included:
        ext = (included[("profile", pid)].get("attributes") or {}).get("external_id")
    return pid, ext


def to_ledger_row(rec: Dict[str, Any], included, kind: str, channel: str,
                  dispatcher_flow_id: Optional[str]) -> Optional[Dict[str, Any]]:
    pid, ext = profile_key(rec, included)
    if not pid:
        return None
    attrs = rec.get("attributes") or {}
    props = attrs.get("event_properties") or {}
    flow_id = props.get("$flow") or None
    mtype = props.get("Message Type") or ("flow" if flow_id else "campaign")
    return {
        "event_id": rec["id"],
        "customer_id": ext or pid,
        "profile_id": pid,
        "kind": kind,
        "channel": channel,
        "occurred_at": datetime.fromisoformat(attrs["datetime"]),
        "flow_id": flow_id,
        "message_id": props.get("$message") or None,
        "message_type": str(mtype).lower(),
        "transactional": flow_id in TRANSACTIONAL_FLOW_IDS,
        "nba": bool(dispatcher_flow_id and flow_id == dispatcher_flow_id),
    }


def pull_message_events(
    client: KlaviyoClient,
    store: Store,
    now: datetime,
    lookback: timedelta = timedelta(days=60),
    metrics: Optional[Dict[str, Tuple[str, str, str]]] = None,
    dispatcher_flow_id: Optional[str] = None,
    max_pages: Optional[int] = None,
    page_size: int = 1000,
) -> Dict[str, int]:
    """Incremental: each metric resumes from its last stored event time
    (inclusive; duplicates are dropped by event id).
    Returns new rows per metric name."""
    out: Dict[str, int] = {}
    for name, (metric_id, kind, channel) in (metrics or MESSAGE_METRICS).items():
        key = f"events:{metric_id}"
        cursor = store.get_cursor(key)
        # Re-read an overlap behind the cursor: late-ingested events (buffered
        # app events, delayed webhooks) still land; event ids dedupe.
        since = min(datetime.fromisoformat(cursor), now - LATE_OVERLAP) if cursor else now - lookback
        rows: List[Dict[str, Any]] = []
        last = since
        for rec, included in client.iter_records("/api/events", events_params(metric_id, since, now, page_size), max_pages):
            row = to_ledger_row(rec, included, kind, channel, dispatcher_flow_id)
            if row is None:
                continue
            rows.append(row)
            if row["profile_id"] and row["customer_id"] != row["profile_id"]:
                store.map_profile(row["customer_id"], row["profile_id"])
            last = max(last, row["occurred_at"])
        out[name] = store.add_message_events(rows)
        store.set_cursor(key, last.isoformat())
    return out
