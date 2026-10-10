"""The heuristic Trip State Builder.

Implements the state machine from the design document:

    order confirmed ............................ pre_trip
    activation + usage (outside home) .......... in_trip          (trip start)
    activation at home, tiny usage ............. stays pre_trip   (test install)
    14 days without usage ...................... trip_end_provisional
    usage arrives late ......................... back to in_trip  (trip reopens)
    16 days without usage ...................... post_trip        (confirmed end)
    30 days after the end ...................... between_trips
    new order at any point ..................... pre_trip, or in_trip with
                                                 pending_activation=True

Trips are computed per customer as the union of usage across all of the
customer's eSIMs, so a family buying three eSIMs for one holiday is one trip,
and a Japan -> Korea back-to-back journey with no 14-day gap is one trip.

Pure functions, no I/O, no third-party dependencies.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .config import DEFAULT_CONFIG, TripConfig
from .models import (
    Activation,
    Customer,
    Entitlement,
    Order,
    Trip,
    TripSnapshot,
    UsageDay,
)

STATE_NO_TRIP = "no_trip"
STATE_PRE_TRIP = "pre_trip"
STATE_IN_TRIP = "in_trip"
STATE_END_PROVISIONAL = "trip_end_provisional"
STATE_POST_TRIP = "post_trip"
STATE_BETWEEN = "between_trips"

CONF_HIGH = "high"
CONF_MEDIUM = "medium"
CONF_LOW = "low"
CONF_NONE = "none"

_EXCLUDED_ORDER_STATUS = {"refunded", "cancelled"}


# ------------------------------------------------------------- utilities


def _utc(dt: datetime) -> datetime:
    """Treat naive datetimes as UTC; convert aware ones to UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _day_start(d: date) -> datetime:
    return datetime.combine(d, time.min, tzinfo=timezone.utc)


def _day_end(d: date) -> datetime:
    return datetime.combine(d, time.max, tzinfo=timezone.utc)


def _cluster_days(days: Sequence[date], gap_days: int) -> List[List[date]]:
    """Split sorted usage days into trips: a gap of MORE than `gap_days`
    days between two usage days starts a new trip (14 full days without
    usage end a trip; usage on day 15 belongs to a new one)."""
    clusters: List[List[date]] = []
    current: List[date] = []
    prev: Optional[date] = None
    for d in days:
        if prev is not None and (d - prev).days > gap_days:
            clusters.append(current)
            current = []
        current.append(d)
        prev = d
    if current:
        clusters.append(current)
    return clusters


# ------------------------------------------------------------ the builder


def build_trip_state(
    customer: Customer,
    orders: Iterable[Order],
    activations: Iterable[Activation],
    usage: Iterable[UsageDay],
    entitlements: Iterable[Entitlement],
    as_of: datetime,
    usage_data_as_of: Optional[datetime] = None,
    config: TripConfig = DEFAULT_CONFIG,
) -> Tuple[TripSnapshot, List[Trip]]:
    """Compute one customer's trip state as of `as_of`.

    usage_data_as_of: when the usage feed was last loaded. If None, the feed
    is assumed current (usage_fresh=True). Pass the real load time in
    production so run-out decisions stop when the carrier feed lags.
    """
    as_of = _utc(as_of)
    today = as_of.date()
    feed_as_of = _utc(usage_data_as_of) if usage_data_as_of else as_of
    usage_fresh = (as_of - feed_as_of) <= timedelta(hours=config.usage_stale_hours)

    cid = customer.customer_id
    orders = [o for o in orders if o.customer_id == cid]
    activations = [a for a in activations if a.customer_id == cid]
    usage = [u for u in usage if u.customer_id == cid]
    entitlements = [e for e in entitlements if e.customer_id == cid]

    reasons: List[str] = []

    # 1. Refunded or cancelled orders suppress their eSIM entirely.
    excluded = {o.esim_id for o in orders if o.status in _EXCLUDED_ORDER_STATUS}
    order_by_esim: Dict[str, Order] = {
        o.esim_id: o for o in orders if o.status == "confirmed"
    }
    if excluded:
        reasons.append(f"excluded eSIMs (refund/cancel): {sorted(excluded)}")

    act_by_esim: Dict[str, Activation] = {}
    for a in activations:
        if a.esim_id in excluded:
            continue
        prev = act_by_esim.get(a.esim_id)
        if prev is None or _utc(a.activated_at) < _utc(prev.activated_at):
            act_by_esim[a.esim_id] = a
    plan_end_by_esim: Dict[str, datetime] = {
        e.esim_id: _utc(e.plan_end_at)
        for e in entitlements
        if e.plan_end_at is not None and e.esim_id not in excluded
    }

    # 2. Usage per calendar day, union across eSIMs.
    usage = [u for u in usage if u.esim_id not in excluded and u.mb_used >= 0]
    by_day: Dict[date, List[UsageDay]] = defaultdict(list)
    for u in usage:
        by_day[u.day].append(u)
    home = customer.home_country

    # A day with only tiny usage in the home country neither starts nor
    # extends a trip: it is the signature of a test install (and of the
    # last few MB after landing back home). Days with unknown country stay.
    def _home_tiny(d: date) -> bool:
        rows = by_day[d]
        if home is None or any(r.network_country is None for r in rows):
            return False
        return (all(r.network_country == home for r in rows)
                and sum(r.mb_used for r in rows) < config.test_install_max_mb)

    ignored_days = [d for d in by_day if _home_tiny(d)]
    home_tiny_esims = {r.esim_id for d in ignored_days for r in by_day[d]}
    days = sorted(d for d in by_day if d not in ignored_days)
    if ignored_days:
        reasons.append(
            f"ignored {len(ignored_days)} home-country day(s) under "
            f"{config.test_install_max_mb:.0f} MB (test install / landing)"
        )

    # 3. Cluster days into candidate trips; drop residual test installs.
    trips: List[Trip] = []
    tested_esims: set = set(home_tiny_esims)
    for cluster in _cluster_days(days, config.trip_gap_days):
        rows = [r for d in cluster for r in by_day[d]]
        esims = sorted({r.esim_id for r in rows})
        total_mb = sum(r.mb_used for r in rows)
        max_day_mb = max(sum(r.mb_used for r in by_day[d]) for d in cluster)
        countries: List[str] = []
        for d in cluster:
            for r in by_day[d]:
                if r.network_country and r.network_country not in countries:
                    countries.append(r.network_country)

        if not countries:
            outside_home: Optional[bool] = None
        elif home is None:
            outside_home = None
        else:
            outside_home = any(c != home for c in countries)

        # Test install: activated at home, hardly any data, never left.
        activated_at_home = all(
            (act_by_esim.get(e) is not None
             and act_by_esim[e].first_network_country is not None
             and act_by_esim[e].first_network_country == home)
            for e in esims
        ) if home else False
        is_test_install = total_mb < config.test_install_max_mb and (
            outside_home is False
            or (outside_home is None and activated_at_home)
        )
        if is_test_install:
            tested_esims.update(esims)
            reasons.append(
                f"test install ignored: {esims} used {total_mb:.0f} MB at home "
                f"on {cluster[0].isoformat()}"
            )
            continue

        first_day, last_day = cluster[0], cluster[-1]

        # Trip start: the activation, if it happened shortly before first use.
        window_start = first_day - timedelta(days=config.activation_to_usage_max_days)
        candidate_starts = [
            _utc(act_by_esim[e].activated_at)
            for e in esims
            if e in act_by_esim
            and window_start <= _utc(act_by_esim[e].activated_at).date() <= first_day
        ]
        if candidate_starts:
            started_at, start_source = min(candidate_starts), "activation"
        else:
            started_at, start_source = _day_start(first_day), "usage"

        if not countries:
            # Fall back to the plan's destination(s), in activation order.
            ordered_esims = sorted(
                esims,
                key=lambda e: _utc(act_by_esim[e].activated_at) if e in act_by_esim else _day_start(first_day),
            )
            dests: List[str] = []
            for e in ordered_esims:
                o = order_by_esim.get(e)
                if o and o.destination not in dests:
                    dests.append(o.destination)
            destinations = dests
        else:
            destinations = countries

        days_since = (today - last_day).days
        trips.append(
            Trip(
                trip_id=f"{cid}:{first_day.isoformat()}",
                customer_id=cid,
                started_at=started_at,
                start_source=start_source,
                first_usage_day=first_day,
                last_usage_day=last_day,
                provisional_end_day=last_day + timedelta(days=config.trip_gap_days),
                confirmed=days_since > config.trip_end_confirm_days,
                esim_ids=esims,
                destinations=destinations,
                usage_days=len(cluster),
                mb_used_total=total_mb,
                outside_home_country=outside_home,
            )
        )

    # Keep `max_day_mb` per trip for confidence (recomputed cheaply).
    def _trip_max_day_mb(t: Trip) -> float:
        return max(
            sum(r.mb_used for r in by_day[d])
            for d in days
            if t.first_usage_day <= d <= t.last_usage_day
        )

    # 4. Pending eSIMs: confirmed, never used in a real trip, not expired.
    used_in_trips = {e for t in trips for e in t.esim_ids}
    pending: List[str] = []
    for esim, o in sorted(order_by_esim.items(), key=lambda kv: _utc(kv[1].ordered_at)):
        if esim in used_in_trips:
            continue
        pe = plan_end_by_esim.get(esim)
        if pe is not None and pe <= as_of:
            reasons.append(f"eSIM {esim} expired unused on {pe.date().isoformat()}")
            continue
        pending.append(esim)
    # "Activation at home, no usage" (or only tiny usage at home) is a test
    # install: the eSIM stays pending and the trip has not started.
    test_install_detected = any(
        e in act_by_esim and (
            e in tested_esims
            or act_by_esim[e].first_network_country is None
            or act_by_esim[e].first_network_country == home
        )
        for e in pending
    )

    # 5. State from the most recent trip.
    snap = TripSnapshot(customer_id=cid, as_of=as_of, trip_state=STATE_NO_TRIP,
                        trip_confidence=CONF_NONE, usage_fresh=usage_fresh)
    current = trips[-1] if trips else None

    if current is not None:
        days_since = (today - current.last_usage_day).days
        if days_since <= config.trip_gap_days:
            snap.trip_state = STATE_IN_TRIP
            reasons.append(
                f"in_trip: last usage {current.last_usage_day.isoformat()} "
                f"({days_since} d ago), within the {config.trip_gap_days}-day rule"
            )
        elif days_since <= config.trip_end_confirm_days:
            snap.trip_state = STATE_END_PROVISIONAL
            reasons.append(
                f"trip_end_provisional: {days_since} d without usage; confirmed "
                f"after {config.trip_end_confirm_days} d"
            )
        else:
            since_end = (today - current.provisional_end_day).days
            if since_end <= config.post_trip_days:
                snap.trip_state = STATE_POST_TRIP
                reasons.append(
                    f"post_trip: trip ended {current.provisional_end_day.isoformat()} "
                    f"({since_end} d ago)"
                )
            else:
                snap.trip_state = STATE_BETWEEN
                reasons.append(
                    f"between_trips: last trip ended {current.provisional_end_day.isoformat()}"
                )

        # Confidence of the current/last trip.
        if current.outside_home_country is True:
            conf, why = CONF_HIGH, f"usage outside home {home}: {current.destinations}"
        elif current.outside_home_country is None and _trip_max_day_mb(current) >= config.high_confidence_day_mb:
            conf, why = CONF_HIGH, "country unknown but a day of real usage seen"
        elif current.outside_home_country is None:
            conf, why = CONF_MEDIUM, "country unknown and usage light"
        else:
            conf, why = CONF_MEDIUM, f"usage only in home country {home}"
        snap.trip_confidence = conf
        reasons.append(f"confidence {conf}: {why}")

        snap.trip_id = current.trip_id
        snap.trip_started_at = current.started_at
        snap.trip_destination = current.destinations[0] if current.destinations else None
        snap.days_since_last_usage = days_since
        snap.days_in_trip = (today - current.started_at.date()).days + 1
        snap.long_trip = (
            snap.trip_state in (STATE_IN_TRIP, STATE_END_PROVISIONAL)
            and snap.days_in_trip > config.long_trip_days
        )
        snap.trips_completed = sum(1 for t in trips if t.confirmed)
        ended = [t for t in trips if t.confirmed]
        if ended:
            snap.last_trip_ended_day = ended[-1].provisional_end_day

    # 6. Pending orders: pre_trip, or in_trip with a pending activation.
    if pending:
        snap.pending_esim_ids = pending
        if snap.trip_state in (STATE_IN_TRIP, STATE_END_PROVISIONAL):
            snap.pending_activation = True
            reasons.append(f"pending activation during trip: {pending}")
        else:
            snap.trip_state = STATE_PRE_TRIP
            snap.pending_activation = True
            snap.test_install_detected = test_install_detected
            snap.trip_confidence = CONF_MEDIUM if test_install_detected else CONF_LOW
            o = order_by_esim[pending[0]]
            snap.trip_destination = o.destination
            snap.trip_id = None
            snap.trip_started_at = None
            snap.days_in_trip = None
            reasons.append(
                f"pre_trip: {pending} ordered, not yet used"
                + (" (activated at home, test install)" if test_install_detected else "")
            )
            if (
                customer.declared_travel_start is not None
                and customer.declared_travel_start <= today
                and not any(e in act_by_esim for e in pending)
            ):
                snap.departure_likely = True
                reasons.append(
                    f"departure likely: declared {customer.declared_travel_start.isoformat()} "
                    "reached without activation"
                )

    # 7. In-trip data metrics (run-out projection, plan end).
    if snap.trip_state in (STATE_IN_TRIP, STATE_END_PROVISIONAL) and current is not None:
        _fill_data_metrics(snap, current, usage, order_by_esim, plan_end_by_esim,
                           as_of, usage_fresh, config, reasons)

    snap.reasons = reasons
    return snap, trips


def _fill_data_metrics(
    snap: TripSnapshot,
    trip: Trip,
    usage: List[UsageDay],
    order_by_esim: Dict[str, Order],
    plan_end_by_esim: Dict[str, datetime],
    as_of: datetime,
    usage_fresh: bool,
    config: TripConfig,
    reasons: List[str],
) -> None:
    trip_rows = [
        u for u in usage
        if u.esim_id in trip.esim_ids and trip.first_usage_day <= u.day <= trip.last_usage_day
    ]
    by_esim: Dict[str, List[UsageDay]] = defaultdict(list)
    for u in trip_rows:
        by_esim[u.esim_id].append(u)

    # Plan end across the trip's eSIMs.
    ends = [plan_end_by_esim[e] for e in trip.esim_ids if e in plan_end_by_esim]
    future_ends = [e for e in ends if e > as_of]
    if future_ends:
        snap.plan_end_at = min(future_ends)
        snap.plan_ends_within_48h = (snap.plan_end_at - as_of) <= timedelta(hours=config.plan_end_soon_hours)
        if snap.plan_ends_within_48h:
            reasons.append(f"plan ends within 48 h: {snap.plan_end_at.isoformat()}")
    past_ends = [e for e in ends if e <= as_of]
    if past_ends:
        latest_past = max(past_ends)
        if snap.plan_end_at is None:
            snap.plan_end_at = latest_past
        if trip.last_usage_day in (latest_past.date(), latest_past.date() - timedelta(days=1)):
            snap.plan_ended_usage_stopped = True
            reasons.append(
                f"plan ended {latest_past.date().isoformat()} and usage stopped: "
                "likely trip end by plan end (14-day rule still governs the record)"
            )

    if not usage_fresh:
        reasons.append("usage stale: run-out projection withheld")
        return

    # Rank finite-plan eSIMs by projected run-out, then by remaining share.
    candidates: List[Tuple[Tuple[float, float], str, Optional[float], Optional[datetime]]] = []
    for esim, rows in by_esim.items():
        o = order_by_esim.get(esim)
        if o is not None and o.plan_type == "unlimited":
            continue
        rows.sort(key=lambda r: r.day)
        latest = rows[-1]
        if latest.mb_remaining is None:
            continue
        # Only eSIMs still in use count: one the customer stopped using
        # (moved on to another destination) is not "running out".
        if latest.day < trip.last_usage_day - timedelta(days=config.runout_projection_days):
            continue
        recent = rows[-config.runout_projection_days:]
        burn = sum(r.mb_used for r in recent) / len(recent)
        pct = (latest.mb_remaining / o.plan_mb * 100.0) if (o and o.plan_mb) else None
        runout = None
        if burn > 0:
            runout = _day_end(latest.day) + timedelta(days=latest.mb_remaining / burn)
        key = (
            runout.timestamp() if runout else float("inf"),
            pct if pct is not None else float("inf"),
        )
        candidates.append((key, esim, pct, runout))
    if candidates:
        _, esim, pct, runout = min(candidates, key=lambda c: c[0])
        snap.esim_closest_to_runout = esim
        snap.data_remaining_pct = round(pct, 1) if pct is not None else None
        snap.projected_runout_at = runout
        if pct is not None and pct <= config.low_data_pct:
            reasons.append(f"low data: {esim} at {pct:.0f}% remaining")
        if runout is not None and snap.plan_end_at is not None and runout < snap.plan_end_at:
            reasons.append(
                f"projected run-out {runout.date().isoformat()} before plan end "
                f"{snap.plan_end_at.date().isoformat()}"
            )


# ------------------------------------------------------------- batch API


def build_many(
    customers: Iterable[Customer],
    orders: Iterable[Order],
    activations: Iterable[Activation],
    usage: Iterable[UsageDay],
    entitlements: Iterable[Entitlement],
    as_of: datetime,
    usage_data_as_of: Optional[datetime] = None,
    config: TripConfig = DEFAULT_CONFIG,
) -> Dict[str, Tuple[TripSnapshot, List[Trip]]]:
    """Run the builder for every customer. Inputs are grouped once, so this
    is linear in the number of rows."""
    o_by: Dict[str, List[Order]] = defaultdict(list)
    a_by: Dict[str, List[Activation]] = defaultdict(list)
    u_by: Dict[str, List[UsageDay]] = defaultdict(list)
    e_by: Dict[str, List[Entitlement]] = defaultdict(list)
    for o in orders:
        o_by[o.customer_id].append(o)
    for a in activations:
        a_by[a.customer_id].append(a)
    for u in usage:
        u_by[u.customer_id].append(u)
    for e in entitlements:
        e_by[e.customer_id].append(e)
    out: Dict[str, Tuple[TripSnapshot, List[Trip]]] = {}
    for c in customers:
        out[c.customer_id] = build_trip_state(
            c, o_by[c.customer_id], a_by[c.customer_id], u_by[c.customer_id],
            e_by[c.customer_id], as_of, usage_data_as_of, config,
        )
    return out
