"""Scenario builders shared by the tests. Every scenario is a small,
readable story about one customer; dates are chosen around AS_OF."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import List, Optional

from trip_state.models import Activation, Customer, Entitlement, Order, UsageDay

AS_OF = datetime(2026, 9, 23, 10, 0, tzinfo=timezone.utc)
TODAY = AS_OF.date()


def d(days_ago: int) -> date:
    return TODAY - timedelta(days=days_ago)


def ts(days_ago: int, hour: int = 8) -> datetime:
    return datetime.combine(d(days_ago), datetime.min.time(), tzinfo=timezone.utc).replace(hour=hour)


def customer(cid: str = "c1", home: Optional[str] = "ES", **kw) -> Customer:
    return Customer(customer_id=cid, home_country=home, **kw)


def order(cid: str, esim: str, days_ago: int, dest: str = "JP", plan_mb: Optional[float] = 5000.0,
          plan_type: str = "finite", status: str = "confirmed", channel: str = "web") -> Order:
    return Order(customer_id=cid, order_id=f"o-{esim}", esim_id=esim, ordered_at=ts(days_ago, 9),
                 destination=dest, plan_type=plan_type, plan_mb=plan_mb, plan_days=10,
                 purchase_channel=channel, status=status)


def activation(cid: str, esim: str, days_ago: int, country: Optional[str] = "JP", hour: int = 8) -> Activation:
    return Activation(customer_id=cid, esim_id=esim, activated_at=ts(days_ago, hour),
                      first_network_country=country)


def usage(cid: str, esim: str, from_days_ago: int, to_days_ago: int, mb_per_day: float = 300.0,
          country: Optional[str] = "JP", plan_mb: Optional[float] = 5000.0) -> List[UsageDay]:
    """Daily usage from `from_days_ago` down to `to_days_ago` (inclusive),
    with mb_remaining decreasing from plan_mb."""
    rows: List[UsageDay] = []
    remaining = plan_mb
    for k in range(from_days_ago, to_days_ago - 1, -1):
        if remaining is not None:
            remaining = max(0.0, remaining - mb_per_day)
        rows.append(UsageDay(customer_id=cid, esim_id=esim, day=d(k), mb_used=mb_per_day,
                             mb_remaining=remaining, network_country=country))
    return rows


def entitlement(cid: str, esim: str, plan_end: datetime) -> Entitlement:
    return Entitlement(customer_id=cid, esim_id=esim, plan_end_at=plan_end)


# ---------------------------------------------------------------- scenarios
# Each returns (customer, orders, activations, usage, entitlements).

def scn_no_orders():
    return customer(), [], [], [], []


def scn_pre_trip():
    return customer(), [order("c1", "e1", 3)], [], [], []


def scn_departure_likely():
    c = customer(declared_travel_start=d(1), declared_destination="JP")
    return c, [order("c1", "e1", 5)], [], [], []


def scn_test_install():
    """Activated at home 5 days ago, 12 MB in Spain, never left."""
    return (customer(), [order("c1", "e1", 6)], [activation("c1", "e1", 5, "ES")],
            usage("c1", "e1", 5, 5, mb_per_day=12.0, country="ES"), [])


def scn_in_trip():
    """Activated 8 days ago in Japan, 300 MB/day since; plan ends in 5 days."""
    return (customer(), [order("c1", "e1", 12)], [activation("c1", "e1", 8, "JP")],
            usage("c1", "e1", 8, 1), [entitlement("c1", "e1", AS_OF + timedelta(days=5))])


def scn_test_then_real_trip():
    """Test install at home 22 days ago (5 MB), real usage in Japan from 8 days ago."""
    return (customer(), [order("c1", "e1", 25)], [activation("c1", "e1", 22, "ES")],
            usage("c1", "e1", 22, 22, mb_per_day=5.0, country="ES") + usage("c1", "e1", 8, 1), [])


def scn_provisional_end(extra_usage_days_ago: Optional[int] = None):
    """Usage 22..15 days ago -> 15 days since last usage -> provisional end.
    With `extra_usage_days_ago=1` a late usage day reopens the trip."""
    u = usage("c1", "e1", 22, 15)
    if extra_usage_days_ago is not None:
        u += usage("c1", "e1", extra_usage_days_ago, extra_usage_days_ago, plan_mb=2000.0)
    return customer(), [order("c1", "e1", 25)], [activation("c1", "e1", 22, "JP")], u, []


def scn_post_trip():
    """Usage 34..26 days ago: ended 12 days ago (confirmed), inside the 30-day post-trip window."""
    return (customer(), [order("c1", "e1", 40)], [activation("c1", "e1", 34, "JP")],
            usage("c1", "e1", 34, 26), [])


def scn_between_trips():
    """Usage 84..75 days ago: ended 61 days ago."""
    return (customer(), [order("c1", "e1", 90)], [activation("c1", "e1", 84, "JP")],
            usage("c1", "e1", 84, 75), [])


def scn_new_order_during_trip():
    c, o, a, u, e = scn_in_trip()
    return c, o + [order("c1", "e2", 2, dest="KR")], a, u, e


def scn_multi_esim_one_trip():
    """Japan eSIM 13..9 days ago, Korea eSIM 8..1 days ago: one continuous trip."""
    return (customer(),
            [order("c1", "e1", 16, "JP"), order("c1", "e2", 15, "KR")],
            [activation("c1", "e1", 13, "JP"), activation("c1", "e2", 8, "KR")],
            usage("c1", "e1", 13, 9, country="JP") + usage("c1", "e2", 8, 1, country="KR"), [])


def scn_refunded():
    return (customer(), [order("c1", "e1", 12, status="refunded")],
            [activation("c1", "e1", 8, "JP")], usage("c1", "e1", 8, 1), [])


def scn_plan_ended_usage_stopped():
    """Plan ended 2 days ago at 23:59, last usage that day, still within 14 days."""
    end = datetime.combine(d(2), datetime.max.time(), tzinfo=timezone.utc)
    return (customer(), [order("c1", "e1", 12)], [activation("c1", "e1", 8, "JP")],
            usage("c1", "e1", 8, 2), [entitlement("c1", "e1", end)])


def scn_long_trip():
    return (customer(), [order("c1", "e1", 50, plan_type="unlimited", plan_mb=None)],
            [activation("c1", "e1", 44, "JP")],
            usage("c1", "e1", 44, 1, plan_mb=None), [])


def scn_unknown_country(mb_per_day: float):
    return (customer(), [order("c1", "e1", 12)], [activation("c1", "e1", 8, None)],
            usage("c1", "e1", 8, 1, mb_per_day=mb_per_day, country=None), [])


def scn_two_trips():
    """June trip (confirmed) and a current September trip."""
    return (customer(),
            [order("c1", "e1", 120, "US"), order("c1", "e2", 12, "JP")],
            [activation("c1", "e1", 114, "US"), activation("c1", "e2", 8, "JP")],
            usage("c1", "e1", 114, 110, country="US") + usage("c1", "e2", 8, 1, country="JP"), [])


ALL_SCENARIOS = {
    "no_orders": scn_no_orders,
    "pre_trip": scn_pre_trip,
    "departure_likely": scn_departure_likely,
    "test_install": scn_test_install,
    "in_trip": scn_in_trip,
    "test_then_real_trip": scn_test_then_real_trip,
    "provisional_end": scn_provisional_end,
    "reopened": lambda: scn_provisional_end(1),
    "post_trip": scn_post_trip,
    "between_trips": scn_between_trips,
    "new_order_during_trip": scn_new_order_during_trip,
    "multi_esim_one_trip": scn_multi_esim_one_trip,
    "refunded": scn_refunded,
    "plan_ended_usage_stopped": scn_plan_ended_usage_stopped,
    "long_trip": scn_long_trip,
    "unknown_country_big": lambda: scn_unknown_country(120.0),
    "unknown_country_small": lambda: scn_unknown_country(20.0),
    "two_trips": scn_two_trips,
}
