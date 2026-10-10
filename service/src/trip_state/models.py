"""Input and output records.

Inputs mirror the "minimum event schema" in the design document. Every field
that Holafly may not have yet is Optional, and the builder degrades
gracefully (lower confidence, fewer flags) when it is missing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import List, Optional

# ----------------------------------------------------------------- inputs


@dataclass
class Customer:
    customer_id: str
    home_country: Optional[str] = None  # ISO-3166 alpha-2, e.g. "ES"
    declared_travel_start: Optional[date] = None  # from calendar-lead forms
    declared_destination: Optional[str] = None


@dataclass
class Order:
    customer_id: str
    order_id: str
    esim_id: str
    ordered_at: datetime
    destination: str  # country code or region code, e.g. "JP" or "EUROPE"
    plan_type: str = "finite"  # "finite" | "unlimited"
    plan_mb: Optional[float] = None  # total data for finite plans
    plan_days: Optional[int] = None
    purchase_channel: str = "web"  # "web" | "app"
    status: str = "confirmed"  # "confirmed" | "refunded" | "cancelled"


@dataclass
class Activation:
    customer_id: str
    esim_id: str
    activated_at: datetime
    first_network_country: Optional[str] = None


@dataclass
class UsageDay:
    """One row per eSIM per calendar day (UTC) with data consumed that day."""

    customer_id: str
    esim_id: str
    day: date
    mb_used: float
    mb_remaining: Optional[float] = None  # None for unlimited or unknown
    network_country: Optional[str] = None


@dataclass
class Entitlement:
    customer_id: str
    esim_id: str
    plan_end_at: Optional[datetime] = None


# ---------------------------------------------------------------- outputs


@dataclass
class Trip:
    trip_id: str
    customer_id: str
    started_at: datetime
    start_source: str  # "activation" | "usage"
    first_usage_day: date
    last_usage_day: date
    provisional_end_day: date  # last_usage_day + trip_gap_days
    confirmed: bool  # trip_end_confirm_days elapsed without usage
    esim_ids: List[str] = field(default_factory=list)
    destinations: List[str] = field(default_factory=list)  # in order seen
    usage_days: int = 0
    mb_used_total: float = 0.0
    outside_home_country: Optional[bool] = None  # None = country unknown

    @property
    def duration_days(self) -> int:
        return (self.last_usage_day - self.first_usage_day).days + 1


@dataclass
class TripSnapshot:
    """The customer's trip state as of `as_of`. This is what the Decision
    Service reads and what is mirrored to Klaviyo profile properties."""

    customer_id: str
    as_of: datetime
    trip_state: str  # no_trip | pre_trip | in_trip | trip_end_provisional | post_trip | between_trips
    trip_confidence: str  # high | medium | low | none
    trip_id: Optional[str] = None
    trip_started_at: Optional[datetime] = None
    trip_destination: Optional[str] = None
    days_in_trip: Optional[int] = None
    days_since_last_usage: Optional[int] = None
    usage_fresh: bool = False
    data_remaining_pct: Optional[float] = None
    projected_runout_at: Optional[datetime] = None
    esim_closest_to_runout: Optional[str] = None
    plan_end_at: Optional[datetime] = None
    plan_ends_within_48h: bool = False
    plan_ended_usage_stopped: bool = False
    pending_activation: bool = False
    pending_esim_ids: List[str] = field(default_factory=list)
    test_install_detected: bool = False
    departure_likely: bool = False  # declared date reached, nothing activated
    long_trip: bool = False
    trips_completed: int = 0
    last_trip_ended_day: Optional[date] = None
    reasons: List[str] = field(default_factory=list)
