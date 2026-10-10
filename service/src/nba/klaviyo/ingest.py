"""Turn Klaviyo trip events into Trip State Builder inputs.

Klaviyo holds orders, activations and plan end dates; it does not hold daily
usage (gap 2), which comes from the warehouse. What was verified on the live
account (keys only) and how each event is used:

- activation_event: order_id, activation_country (English name),
  purchased_destination, number_of_days_purchased -> Activation and, if the
  order event is missing, an Order. No esim_id: eSIMs are keyed by order_id.
- expiration_date_eSIM_v2: order_id, expiration_date_eSIM_v2, Days ->
  Entitlement.plan_end_at (also carries an ICCID, which is not stored).
- hly_order_confirmed: order_number, items (JSON string with sku),
  destination_localized, financial_status -> Order. Its address, name, email
  and phone fields are never read.
- Refunded Order / Cancelled Order -> order status.
- hly_always_on_consumption_threshold -> signal for I3.

Only the fields named above are read from each event. Unknown destination
names are counted and reported, never guessed.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

from trip_state.models import Activation, Customer, Entitlement, Order

from ..countries import to_code
from .client import KlaviyoClient
from .outcomes import events_params, profile_key
from .registry import TRIP_METRICS


@dataclass
class TripInputs:
    customers: Dict[str, Customer] = field(default_factory=dict)
    orders: Dict[str, Order] = field(default_factory=dict)  # by order key
    activations: List[Activation] = field(default_factory=list)
    entitlements: List[Entitlement] = field(default_factory=list)
    always_on_threshold: Dict[str, datetime] = field(default_factory=dict)
    profile_ids: Dict[str, str] = field(default_factory=dict)
    unknown_destinations: Counter = field(default_factory=Counter)
    skipped: Counter = field(default_factory=Counter)


def _dt(v: Any) -> Optional[datetime]:
    if not v:
        return None
    s = str(v).strip().replace(" ", "T", 1).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _int(v: Any) -> Optional[int]:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _dest(props: Dict[str, Any], inputs: TripInputs, *keys: str) -> str:
    for k in keys:
        v = props.get(k)
        if v:
            code = to_code(v)
            if code:
                return code
            inputs.unknown_destinations[str(v)[:40]] += 1
    return ""


def _sku_destination(items_raw: Any) -> Optional[str]:
    try:
        items = json.loads(items_raw) if isinstance(items_raw, str) else (items_raw or [])
    except ValueError:
        return None
    for it in items if isinstance(items, list) else []:
        sku = (it or {}).get("sku") if isinstance(it, dict) else None
        if sku:
            return str(sku)
    return None


def _cust(inputs: TripInputs, cid: str) -> None:
    inputs.customers.setdefault(cid, Customer(customer_id=cid))


def apply_event(inputs: TripInputs, metric_name: str, rec: Dict[str, Any],
                included: Dict[Tuple[str, str], Dict[str, Any]]) -> None:
    pid, ext = profile_key(rec, included)
    if not pid:
        inputs.skipped["no_profile"] += 1
        return
    cid = ext or pid
    inputs.profile_ids[cid] = pid
    attrs = rec.get("attributes") or {}
    props = attrs.get("event_properties") or {}
    at = _dt(attrs.get("datetime"))
    order_key = str(props.get("order_id") or props.get("order_number") or "")

    if metric_name == "activation_event":
        if not order_key or not at:
            inputs.skipped["activation_without_order"] += 1
            return
        _cust(inputs, cid)
        dest = _dest(props, inputs, "purchased_destination")
        inputs.activations.append(Activation(
            customer_id=cid, esim_id=order_key, activated_at=at,
            first_network_country=_dest(props, inputs, "activation_country") or None,
        ))
        if order_key not in inputs.orders:
            inputs.orders[order_key] = Order(
                customer_id=cid, order_id=order_key, esim_id=order_key, ordered_at=at,
                destination=dest, plan_days=_int(props.get("number_of_days_purchased")),
            )
    elif metric_name == "expiration_date_eSIM_v2":
        end = _dt(props.get("expiration_date_eSIM_v2"))
        if not order_key or not end:
            inputs.skipped["expiration_without_date"] += 1
            return
        _cust(inputs, cid)
        inputs.entitlements.append(Entitlement(customer_id=cid, esim_id=order_key, plan_end_at=end))
    elif metric_name == "hly_order_confirmed":
        if not order_key or not at:
            inputs.skipped["order_without_key"] += 1
            return
        _cust(inputs, cid)
        dest = to_code(_sku_destination(props.get("items"))) or _dest(props, inputs, "destination_localized")
        prev = inputs.orders.get(order_key)
        inputs.orders[order_key] = Order(
            customer_id=cid, order_id=order_key, esim_id=order_key, ordered_at=at,
            destination=dest or (prev.destination if prev else ""),
            plan_days=prev.plan_days if prev else None,
            status="confirmed",
        )
    elif metric_name in ("Refunded Order", "Cancelled Order"):
        o = inputs.orders.get(order_key)
        if o:
            o.status = "refunded" if metric_name == "Refunded Order" else "cancelled"
    elif metric_name == "hly_always_on_consumption_threshold":
        if at:
            inputs.always_on_threshold[cid] = max(at, inputs.always_on_threshold.get(cid, at))
    else:
        inputs.skipped[f"unused:{metric_name}"] += 1


INGEST_ORDER = (
    "hly_order_confirmed", "activation_event", "expiration_date_eSIM_v2",
    "Refunded Order", "Cancelled Order", "hly_always_on_consumption_threshold",
)


def pull_trip_inputs(client: KlaviyoClient, now: datetime, lookback: timedelta = timedelta(days=120),
                     max_pages: Optional[int] = None) -> TripInputs:
    """Read the trip events of the last `lookback` days. Orders are read first
    so refunds and activations can attach to them."""
    inputs = TripInputs()
    since = now - lookback
    for name in INGEST_ORDER:
        mid = TRIP_METRICS[name]
        for rec, included in client.iter_records("/api/events", events_params(mid, since, now), max_pages):
            apply_event(inputs, name, rec, included)
    return inputs
