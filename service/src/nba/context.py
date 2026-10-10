"""Everything the policy reads about one customer at one decision point.

A DecisionContext is assembled from three sources and then frozen; the policy
never reads anything else, which is what keeps decisions reproducible
("features frozen at decided_at"):

1. the Trip State Builder snapshot (trip phase, confidence, days, data left);
2. customer signals (consent, app, Always On, care, intent), loaded from
   Klaviyo profiles and the warehouse; see `CustomerSignals`;
3. contact history from the cap ledger and the decision log (sends in 30
   days per channel, last send, fatigue, what was already prompted this trip).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from trip_state.config import DEFAULT_CONFIG as TRIP_DEFAULTS
from trip_state.models import TripSnapshot

from .countries import home_timezone, timezone_for


@dataclass
class CustomerSignals:
    """Per-customer inputs that are not trip state. Defaults are the safe
    choice: unknown consent means no channel, unknown app identity blocks
    app-install prompts."""

    customer_id: str
    klaviyo_profile_id: Optional[str] = None
    market: Optional[str] = None  # home market, ISO-2
    locale: Optional[str] = None
    home_timezone: Optional[str] = None  # IANA, from the Klaviyo profile

    consent_email: bool = False
    consent_whatsapp: bool = False
    email_complaint_90d: bool = False  # hard bounce or spam complaint
    app_installed: bool = False
    push_token: bool = False
    in_app_session_14d: bool = False
    app_identity: str = "unknown"  # "linked" | "unknown"
    first_purchase_channel: str = "web"

    plan_type: str = "finite"  # of the eSIM in use
    plans_subscriber: bool = False
    paid_plan_active: bool = False
    newer_plan_for_destination: bool = False

    always_on_status: str = "none"  # none | eligible | active
    always_on_claimed_this_month: bool = False
    always_on_threshold_event: bool = False  # hly_always_on_consumption_threshold, current trip

    care_open_72h: bool = False
    care_this_trip: bool = False
    refunded: bool = False

    hours_since_order: Optional[float] = None
    order_last_24h: bool = False
    order_last_30d: bool = False
    order_last_60d: bool = False
    topped_up_24h: bool = False
    low_data_alert_12h: bool = False
    hours_since_push_transactional: float = 1e9

    install_started: bool = False
    install_completed: bool = False
    hours_since_install_start: Optional[float] = None
    market_p60_lead_hours: float = 120.0  # purchase-to-activation, 60th percentile

    in_trip_app_session: bool = False
    feature_used: bool = False
    just_installed: bool = False
    success_moment: bool = False

    intent_24h: bool = False  # browse or cart event without order
    declared_within_7d: bool = False
    repeat_predicted: bool = False
    paid_share: float = 0.0  # segment share of orders last-clicked from paid
    p0_buy: float = 0.0  # proxy purchase probability (capture)
    coupon_user: bool = False

    nps_score: Optional[int] = None
    days_since_nps: Optional[int] = None
    anniversary_window: bool = False

    # Operator overrides (profile properties nba_exclude / nba_manual_action).
    nba_exclude: bool = False
    nba_manual_action: Optional[str] = None


@dataclass
class ContactHistory:
    """Derived from the cap ledger and the decision log."""

    sends_30d: Dict[str, int] = field(default_factory=lambda: {"total": 0, "email": 0, "push": 0, "whatsapp": 0})
    hours_since_last_send: float = 1e9
    capture_sends_30d: int = 0
    fatigued_keys: List[str] = field(default_factory=list)
    always_on_claim_reminders_this_month: int = 0
    always_on_prompt_30d: bool = False
    referral_last_prompt_days: float = 1e9
    referral_prompts_12m: int = 0
    i7_this_trip: bool = False
    t1_last_3d: bool = False
    p2_ignored_this_trip: bool = False
    feedback_sent_this_trip: bool = False
    review_invited_this_trip: bool = False
    # Hours since each action was last dispatched (any channel).
    hours_since_action: Dict[str, float] = field(default_factory=dict)


@dataclass
class DecisionContext:
    customer_id: str
    decided_at: datetime
    phase: str
    trip_confidence: str
    trip_id: Optional[str]
    destination: Optional[str]
    market: Optional[str]
    locale: Optional[str]
    local_tz: Optional[str]
    tz_source: str  # destination | profile | market | unknown

    days_in_trip: Optional[int]
    days_since_last_usage: Optional[int]
    days_after_confirm: Optional[int]
    days_since_trip_end: Optional[int]
    data_remaining_pct: Optional[float]
    runout_before_plan_end: bool
    projected_runout_days: Optional[float]
    plan_end_hours: Optional[float]
    plan_ended_usage_stopped: bool
    usage_fresh: bool
    usage_last_24h: bool
    pending_activation: bool
    declared_departure_days: Optional[int]
    network_outside_plan: bool

    s: CustomerSignals
    h: ContactHistory

    @property
    def tz_known(self) -> bool:
        return self.local_tz is not None and self.tz_source != "market"

    def to_features(self) -> Dict[str, Any]:
        """Flat, JSON-safe copy for the decision log (`features` column)."""
        d = asdict(self)
        d["decided_at"] = self.decided_at.isoformat()
        d.pop("s")
        d.pop("h")
        d["signals"] = {k: v for k, v in asdict(self.s).items() if k not in ("klaviyo_profile_id",)}
        d["history"] = asdict(self.h)
        return d


def _hours(a: datetime, b: datetime) -> float:
    return (a - b).total_seconds() / 3600.0


def build_context(
    snap: TripSnapshot,
    signals: CustomerSignals,
    history: ContactHistory,
    now: Optional[datetime] = None,
    declared_travel_start: Optional[date] = None,
    confirm_days: int = TRIP_DEFAULTS.trip_end_confirm_days,
) -> DecisionContext:
    now = now or snap.as_of
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    today = now.date()
    phase = snap.trip_state

    days_after_confirm = None
    if phase == "post_trip" and snap.days_since_last_usage is not None:
        days_after_confirm = snap.days_since_last_usage - confirm_days
    days_since_trip_end = None
    if snap.last_trip_ended_day is not None:
        days_since_trip_end = (today - snap.last_trip_ended_day).days

    plan_end_hours = _hours(snap.plan_end_at, now) if snap.plan_end_at else None
    runout_days = None
    if snap.projected_runout_at is not None:
        runout_days = max(0.0, _hours(snap.projected_runout_at, now) / 24.0)
    runout_before_end = bool(
        snap.projected_runout_at and snap.plan_end_at and snap.projected_runout_at < snap.plan_end_at
    )

    # Local time: the destination's while in-trip, the profile's otherwise,
    # the home market's as a last resort (good enough for email, not for push).
    local_tz, tz_source = None, "unknown"
    if phase in ("in_trip", "trip_end_provisional") and timezone_for(snap.trip_destination):
        local_tz, tz_source = timezone_for(snap.trip_destination), "destination"
    elif signals.home_timezone:
        local_tz, tz_source = signals.home_timezone, "profile"
    elif home_timezone(signals.market):
        local_tz, tz_source = home_timezone(signals.market), "market"

    declared_days = None
    if declared_travel_start is not None:
        declared_days = (declared_travel_start - today).days

    return DecisionContext(
        customer_id=snap.customer_id,
        decided_at=now,
        phase=phase,
        trip_confidence=snap.trip_confidence,
        trip_id=snap.trip_id,
        destination=snap.trip_destination,
        market=signals.market,
        locale=signals.locale,
        local_tz=local_tz,
        tz_source=tz_source,
        days_in_trip=snap.days_in_trip,
        days_since_last_usage=snap.days_since_last_usage,
        days_after_confirm=days_after_confirm,
        days_since_trip_end=days_since_trip_end,
        data_remaining_pct=snap.data_remaining_pct,
        runout_before_plan_end=runout_before_end,
        projected_runout_days=runout_days,
        plan_end_hours=plan_end_hours,
        plan_ended_usage_stopped=snap.plan_ended_usage_stopped,
        usage_fresh=snap.usage_fresh,
        usage_last_24h=snap.days_since_last_usage is not None and snap.days_since_last_usage <= 1,
        pending_activation=snap.pending_activation,
        declared_departure_days=declared_days,
        network_outside_plan=False,  # v2: needs a network-country event (gap 16)
        s=signals,
        h=history,
    )
