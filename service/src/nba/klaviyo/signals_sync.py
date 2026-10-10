"""Live customer signals from Klaviyo, replacing the hand-made signals CSV.

Two read-only pulls, both incremental:

1. Profiles updated since the last sync: consent per channel, locale,
   timezone, country and Klaviyo's predicted CLV and churn probability.
   Requested with a sparse fieldset, so email, phone, name and address are
   never returned.
2. Behavioural and conversion events into `customer_events`, keeping only
   the whitelisted keys per metric. App events carry device ids and push
   tokens and order events carry addresses; none of that is stored.

`derive_signals` then turns the stored rows into a CustomerSignals per
customer as of a point in time. Fields Klaviyo cannot provide (care
contacts, paid-channel share, first purchase channel, plan type) still come
from the warehouse file and are merged underneath.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

from ..context import CustomerSignals
from ..countries import to_code
from ..store import Store
from .client import KlaviyoClient
from .outcomes import LATE_OVERLAP, events_params, profile_key
from .registry import ALL_METRICS

# metric name -> (keys kept, key holding the monetary value)
EVENT_WHITELIST: Dict[str, Tuple[Tuple[str, ...], Optional[str]]] = {
    "session_start": ((), None),
    "first_open": ((), None),
    "virtual_location_toggle": ((), None),
    "safe_browsing_toggle": ((), None),
    "referral_link_shared": ((), None),
    "referral_code_copied": ((), None),
    "Swell Referral Completed": ((), None),
    "Viewed Product": ((), None),
    "Added to Cart": ((), None),
    "Checkout Started": ((), None),
    "view_item": ((), None),
    "begin_checkout": ((), None),
    "Coupon Used": ((), None),
    "Submitted NPS Score": (("nps_total_score", "nps_group", "distribution_channel"), None),
    "esim_installation_start": (("order_id", "product_id"), None),
    "esim_installation_complete": (("order_id", "product_id"), None),
    "hly_order_confirmed": (("order_number", "financial_status", "customer_type"), "$value"),
    "Top-up order created": ((), "$value"),
    "hly_always_on_activated": (("parent_sku",), None),
    "hly_consumption_start": (("variant_esim",), None),  # Always On / light-plan data started (B1 outcome, to confirm)
    "hly_always_on_consumption_threshold": (("parent_sku", "percentage"), None),
    "Refunded Order": ((), "$value"),
    "Cancelled Order": ((), "$value"),
}

APP_METRICS = ("session_start", "first_open")
TOGGLE_METRICS = ("virtual_location_toggle", "safe_browsing_toggle")
INTENT_METRICS = ("Viewed Product", "Added to Cart", "Checkout Started", "view_item", "begin_checkout")
ORDER_METRICS = ("hly_order_confirmed", "Top-up order created")
REFERRAL_METRICS = ("referral_link_shared", "referral_code_copied")

PROFILE_FIELDS = ",".join((
    "external_id", "locale", "location.timezone", "location.country", "updated",
    "subscriptions.email.marketing.can_receive_email_marketing",
    "subscriptions.mobile_push.marketing.can_receive_push_marketing",
    "subscriptions.whatsapp.marketing.can_receive",
    "predictive_analytics.predicted_clv", "predictive_analytics.churn_probability",
))


def _num(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _get(d: Dict[str, Any], *path: str) -> Any:
    for p in path:
        if not isinstance(d, dict):
            return None
        d = d.get(p)
    return d


def profile_row(rec: Dict[str, Any]) -> Dict[str, Any]:
    a = rec.get("attributes") or {}
    loc = a.get("location") or {}
    subs = a.get("subscriptions") or {}
    pa = a.get("predictive_analytics") or {}
    return {
        "profile_id": rec["id"],
        "customer_id": a.get("external_id") or rec["id"],
        "locale": a.get("locale"),
        "timezone": loc.get("timezone"),
        "country": to_code(loc.get("country")) if loc.get("country") else None,
        "can_email": _get(subs, "email", "marketing", "can_receive_email_marketing"),
        "can_push": _get(subs, "mobile_push", "marketing", "can_receive_push_marketing"),
        "can_whatsapp": _get(subs, "whatsapp", "marketing", "can_receive"),
        "predicted_clv": _num(pa.get("predicted_clv")),
        "churn_probability": _num(pa.get("churn_probability")),
        "updated_at": a.get("updated"),
    }


def pull_profile_signals(client: KlaviyoClient, store: Store, now: datetime,
                         lookback: timedelta = timedelta(days=30), max_pages: Optional[int] = None) -> int:
    key = "profiles:updated"
    cursor = store.get_cursor(key)
    since = cursor or (now - lookback).strftime("%Y-%m-%dT%H:%M:%SZ")
    params = {
        "filter": f"greater-than(updated,{since})",
        "fields[profile]": PROFILE_FIELDS,
        "additional-fields[profile]": "subscriptions,predictive_analytics",
        "sort": "updated",
        "page[size]": "100",
    }
    rows: List[Dict[str, Any]] = []
    last = since
    for rec, _ in client.iter_records("/api/profiles", params, max_pages):
        r = profile_row(rec)
        rows.append(r)
        if r["updated_at"]:
            last = max(last, r["updated_at"])
    n = store.upsert_profile_signals(rows)
    store.set_cursor(key, last)
    return n


def event_row(metric: str, rec: Dict[str, Any], included) -> Optional[Dict[str, Any]]:
    pid, ext = profile_key(rec, included)
    if not pid:
        return None
    a = rec.get("attributes") or {}
    props = a.get("event_properties") or {}
    keys, value_key = EVENT_WHITELIST.get(metric, ((), None))
    return {
        "event_id": rec["id"],
        "customer_id": ext or pid,
        "profile_id": pid,
        "metric": metric,
        "occurred_at": datetime.fromisoformat(a["datetime"]),
        "value": _num(props.get(value_key)) if value_key else None,
        "attrs": {k: props.get(k) for k in keys if props.get(k) not in (None, "")},
    }


def pull_customer_events(client: KlaviyoClient, store: Store, now: datetime,
                         metrics: Optional[Iterable[str]] = None, lookback: timedelta = timedelta(days=90),
                         max_pages: Optional[int] = None) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for name in metrics or EVENT_WHITELIST:
        mid = ALL_METRICS.get(name)
        if not mid:
            continue
        key = f"events:{mid}:customer"
        cursor = store.get_cursor(key)
        since = min(datetime.fromisoformat(cursor), now - LATE_OVERLAP) if cursor else now - lookback
        rows, last = [], since
        for rec, inc in client.iter_records("/api/events", events_params(mid, since, now), max_pages):
            r = event_row(name, rec, inc)
            if r:
                rows.append(r)
                last = max(last, r["occurred_at"])
        out[name] = store.add_customer_events(rows)
        store.set_cursor(key, last.isoformat())
    return out


# ------------------------------------------------------------ derivation


def derive_signals(store: Store, now: datetime, base: Optional[Dict[str, CustomerSignals]] = None,
                   customers: Optional[Iterable[str]] = None) -> Dict[str, CustomerSignals]:
    """CustomerSignals as of `now` from stored profiles and events, layered
    over `base` (warehouse fields). Only events before `now` are read, so a
    back-dated run sees what it would have seen then."""
    base = dict(base or {})
    profiles = store.profile_signals()
    ids = set(customers) if customers is not None else set(base) | set(profiles) | {
        r["customer_id"] for r in store.db.execute("SELECT DISTINCT customer_id FROM customer_events")}
    out: Dict[str, CustomerSignals] = {}
    for cid in ids:
        s = base.get(cid) or CustomerSignals(customer_id=cid)
        p = profiles.get(cid)
        kw: Dict[str, Any] = {}
        if p is not None:
            kw["klaviyo_profile_id"] = p["profile_id"]
            if p["locale"]:
                kw["locale"] = p["locale"]
            if p["timezone"]:
                kw["home_timezone"] = p["timezone"]
            if p["country"] and len(p["country"]) == 2:
                kw["market"] = p["country"]
            if p["can_email"] is not None:
                kw["consent_email"] = bool(p["can_email"])
            if p["can_whatsapp"] is not None:
                kw["consent_whatsapp"] = bool(p["can_whatsapp"])
            if p["predicted_clv"] is not None:
                kw["predicted_clv"] = p["predicted_clv"]
            if p["churn_probability"] is not None:
                kw["churn_probability"] = p["churn_probability"]
        ev = store.events(cid, until=now, since=now - timedelta(days=400))
        if not ev:
            out[cid] = replace(s, **kw)  # no Klaviyo behaviour: keep the warehouse values
            continue
        by: Dict[str, List[datetime]] = {}
        last_attrs: Dict[str, Dict[str, Any]] = {}
        for r in ev:
            t = datetime.fromisoformat(r["occurred_at"])
            by.setdefault(r["metric"], []).append(t)
            last_attrs[r["metric"]] = json.loads(r["attrs"] or "{}")

        def last(*metrics: str) -> Optional[datetime]:
            ts = [t for m in metrics for t in by.get(m, [])]
            return max(ts) if ts else None

        def within(t: Optional[datetime], hours: float) -> bool:
            return t is not None and now - t <= timedelta(hours=hours)

        # Klaviyo events can only switch a signal on; absence of an event keeps
        # whatever the warehouse said (it may see channels Klaviyo does not).
        app = last(*APP_METRICS)
        if app is not None:
            kw["app_installed"] = True
            kw["app_identity"] = "linked"
            session = last("session_start")
            if session is not None:
                kw["in_app_session_14d"] = s.in_app_session_14d or within(session, 14 * 24)
                kw["in_trip_app_session"] = s.in_trip_app_session or within(session, 48)
            if p is not None and p["can_push"] is not None:
                # Klaviyo's push marketing "can receive" needs a registered token and consent.
                kw["push_token"] = bool(p["can_push"])
            first = min(by.get("first_open", [])) if by.get("first_open") else None
            kw["just_installed"] = s.just_installed or within(first, 24)
        if last(*TOGGLE_METRICS):
            kw["feature_used"] = True
        order = last(*ORDER_METRICS)
        if order is not None:
            kw["hours_since_order"] = (now - order).total_seconds() / 3600
            kw["order_last_24h"] = s.order_last_24h or within(order, 24)
            kw["order_last_30d"] = s.order_last_30d or within(order, 30 * 24)
            kw["order_last_60d"] = s.order_last_60d or within(order, 60 * 24)
        intent = last(*INTENT_METRICS)
        if intent is not None and within(intent, 24) and (order is None or order < intent):
            kw["intent_24h"] = True
        topup = last("Top-up order created")
        if within(topup, 24):
            kw["topped_up_24h"] = True
        start, done = last("esim_installation_start"), last("esim_installation_complete")
        if start is not None:
            kw["install_started"] = True
            kw["install_completed"] = s.install_completed or (done is not None and done >= start)
            kw["hours_since_install_start"] = (now - start).total_seconds() / 3600
        elif done is not None:
            kw["install_completed"] = True
        ao = last("hly_always_on_activated")
        if ao is not None:
            kw["always_on_status"] = "active"
        if within(last("hly_always_on_consumption_threshold"), 48):
            kw["always_on_threshold_event"] = True
        nps = last("Submitted NPS Score")
        if nps is not None:
            score = _num(last_attrs["Submitted NPS Score"].get("nps_total_score"))
            if score is not None:
                kw["nps_score"] = int(score)
                kw["days_since_nps"] = (now - nps).days
        if within(topup, 24) or within(ao, 24) or (nps is not None and within(nps, 24) and (kw.get("nps_score") or 0) >= 9):
            kw["success_moment"] = True
        if last("Coupon Used"):
            kw["coupon_user"] = True
        out[cid] = replace(s, **kw)
    return out
