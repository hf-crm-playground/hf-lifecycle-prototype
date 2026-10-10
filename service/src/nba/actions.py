"""The NBA decision table (design, section "Decision table") as data.

Each action has a decision point (trigger), eligibility and action-specific
suppressions, encoded in `check()`, which returns None when the action is a
candidate or the reason code that rules it out. Global suppressions, cap,
reserve, confidence, recency, fatigue, ranking and channel selection live in
`policy.py`, so the order of checks is the same for every action.

`value` is the sample value model used by S(a) until the action-level
controls report: base = outcome rate without the message, lift = absolute
uplift, per_conv = net revenue per incremental outcome (EUR).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, Optional, Tuple

from .context import DecisionContext

CONF_RANK = {"none": 0, "low": 1, "medium": 2, "high": 3}

# Probability the message wins the last click, by channel (design placeholders).
P_WINS = {"in_app": 0.8, "push": 0.7, "whatsapp": 0.7, "email": 0.4, "native_flow": 0.7}

NOT_AT_POINT = "NOT_AT_DECISION_POINT"


@dataclass(frozen=True)
class Value:
    base: float
    lift: float
    per_conv: float


@dataclass(frozen=True)
class Action:
    id: str
    tier: int
    rank: int
    phase: str
    key: str  # content_key sent to Klaviyo; the dispatcher branches on it
    name: str
    channels: Tuple[str, ...]
    min_conf: str
    check: Callable[[DecisionContext], Optional[str]] = field(compare=False, repr=False)
    value: Optional[Value] = None
    under_test: bool = True  # gets an action-level control
    capture: bool = False  # S(a) includes Cap(a)
    capture_action: bool = False  # C1/C2: counts against the capture sub-budget
    perk_cost: float = 0.0
    needs_data: Optional[int] = None  # data-gap number that blocks it in v1
    fixed: bool = False
    experiment: Optional[str] = None
    replaces: Tuple[str, ...] = ()  # Klaviyo flows this action takes over (Phase 0 pause list)


def _within(v: Optional[float], lo: float, hi: float) -> bool:
    return v is not None and lo <= v <= hi


# ----------------------------------------------------------------- checks


def _i1(c: DecisionContext) -> Optional[str]:
    if c.phase != "in_trip" or c.s.plan_type != "finite":
        return NOT_AT_POINT
    low = c.data_remaining_pct is not None and c.data_remaining_pct <= 20
    if not (low or c.runout_before_plan_end):
        return NOT_AT_POINT
    if not c.usage_fresh:
        return "USAGE_STALE"
    if c.s.plans_subscriber:
        return "PLANS_SUBSCRIBER"
    if c.plan_end_hours is not None and c.plan_end_hours <= 24:
        return "PLAN_ENDING_I2_INSTEAD"
    if c.s.topped_up_24h:
        return "ACTION_SUPPRESSED"
    if c.s.low_data_alert_12h:
        return "TRANSACTIONAL_ALERT_SENT"
    return None


def _i2(c):
    if c.phase != "in_trip":
        return NOT_AT_POINT
    if not (c.plan_end_hours is not None and 0 < c.plan_end_hours <= 48 and c.usage_last_24h):
        return NOT_AT_POINT
    if c.s.newer_plan_for_destination or c.s.order_last_24h:
        return "ACTION_SUPPRESSED"
    return None


def _i3(c):
    if c.phase != "in_trip" or not c.s.always_on_threshold_event:
        return NOT_AT_POINT
    if c.s.paid_plan_active or c.s.order_last_24h:
        return "ACTION_SUPPRESSED"
    return None


def _p1(c):
    if c.phase != "pre_trip":
        return NOT_AT_POINT
    if c.s.install_completed:
        return "ALREADY_INSTALLED"
    past_p60 = c.s.hours_since_order is not None and c.s.hours_since_order >= c.s.market_p60_lead_hours
    stuck = c.s.install_started and (c.s.hours_since_install_start or 0) >= 24
    if not (past_p60 or stuck):
        return NOT_AT_POINT
    if c.s.hours_since_order is not None and c.s.hours_since_order < 24:
        return "QR_EMAIL_LAST_24H"
    return None


def _i6(c):
    if c.phase != "in_trip" or not c.network_outside_plan:
        return NOT_AT_POINT
    if c.s.order_last_24h:
        return "ACTION_SUPPRESSED"
    return None


def _b1(c):
    if c.phase not in ("between_trips", "post_trip"):
        return NOT_AT_POINT
    if c.s.always_on_status != "active" or c.decided_at.day < 3:
        return NOT_AT_POINT
    if c.s.always_on_claimed_this_month:
        return "ALREADY_CLAIMED"
    if c.h.always_on_claim_reminders_this_month >= 2:
        return "ACTION_SUPPRESSED"
    return None


def _i4(c):
    if c.phase != "in_trip" or c.days_in_trip != 2:
        return NOT_AT_POINT
    if not c.s.app_installed or c.s.in_trip_app_session:
        return NOT_AT_POINT
    return None


def _i5(c):
    if c.phase != "in_trip" or not _within(c.days_in_trip, 3, 7) or not c.s.app_installed:
        return NOT_AT_POINT
    if c.s.feature_used:
        return "FEATURE_USED"
    if c.s.care_this_trip:
        return "CARE_THIS_TRIP"
    return None


def _c1(c):
    if c.phase not in ("between_trips", "pre_trip", "no_trip") or not c.s.intent_24h:
        return NOT_AT_POINT
    if c.s.paid_share < 0.30:
        return "PAID_SHARE_BELOW_30"
    if c.s.order_last_24h:
        return "ACTION_SUPPRESSED"
    return None


def _c2(c):
    if c.phase not in ("between_trips", "post_trip"):
        return NOT_AT_POINT
    if not (c.s.declared_within_7d or c.s.repeat_predicted):
        return NOT_AT_POINT
    if c.s.paid_share < 0.30:
        return "PAID_SHARE_BELOW_30"
    if c.s.order_last_30d:
        return "ACTION_SUPPRESSED"
    return None


def _t1(c):
    if c.phase != "post_trip" or (c.days_after_confirm or 0) < 3:
        return NOT_AT_POINT
    if c.s.always_on_status != "eligible":
        return "NOT_ELIGIBLE"
    if c.h.always_on_prompt_30d:
        return "ACTION_SUPPRESSED"
    return None


def _t2(c):
    if c.phase != "post_trip" or (c.days_after_confirm or 0) < 5:
        return NOT_AT_POINT
    if c.s.order_last_30d:
        return "NOT_ELIGIBLE"
    if c.h.t1_last_3d:
        return "ACTION_SUPPRESSED"
    return None


def _t3(c):
    if c.phase != "post_trip":
        return NOT_AT_POINT
    if c.s.first_purchase_channel != "web" or c.s.app_installed:
        return "NOT_ELIGIBLE"
    if c.s.app_identity == "unknown":
        return "APP_IDENTITY_UNKNOWN"
    if c.h.p2_ignored_this_trip or c.h.t1_last_3d:
        return "ACTION_SUPPRESSED"
    return None


def _p2(c):
    if c.phase != "pre_trip" or c.s.hours_since_order is None or c.s.hours_since_order < 24:
        return NOT_AT_POINT
    if c.s.first_purchase_channel != "web" or c.s.app_installed:
        return "NOT_ELIGIBLE"
    if c.s.app_identity == "unknown":
        return "APP_IDENTITY_UNKNOWN"
    return None


def _p3(c):
    if c.phase != "pre_trip" or not c.s.app_installed:
        return NOT_AT_POINT
    if not (_within(c.declared_departure_days, 0, 2) or c.s.just_installed):
        return NOT_AT_POINT
    if c.s.feature_used:
        return "FEATURE_USED"
    return None


def _referral_limits(c) -> Optional[str]:
    if c.h.referral_last_prompt_days < 60:
        return "REFERRAL_PROMPT_60D"
    if c.h.referral_prompts_12m >= 3:
        return "REFERRAL_LIMIT_12M"
    return None


def _i7(c):
    if c.phase != "in_trip" or (c.days_in_trip or 0) < 5 or not c.s.success_moment:
        return NOT_AT_POINT
    if c.s.care_this_trip:
        return "CARE_THIS_TRIP"
    return _referral_limits(c)


def _t4(c):
    if c.phase != "post_trip":
        return NOT_AT_POINT
    promoter = c.s.nps_score is not None and c.s.nps_score >= 9 and (c.s.days_since_nps or 0) >= 3
    default = (c.days_after_confirm or 0) >= 5 and not c.s.care_this_trip
    if not (promoter or default):
        return NOT_AT_POINT
    if c.h.i7_this_trip:
        return "ACTION_SUPPRESSED"
    return _referral_limits(c)


def _b2(c):
    if c.phase != "between_trips":
        return NOT_AT_POINT
    if not (_within(c.days_since_trip_end, 60, 120) or c.s.anniversary_window):
        return NOT_AT_POINT
    if c.s.order_last_60d:
        return "NOT_ELIGIBLE"
    if c.h.hours_since_last_send < 14 * 24:
        return "ACTION_SUPPRESSED"
    return None


def _never(c):  # fixed rules are scheduled by the policy, not by check()
    return NOT_AT_POINT


# ------------------------------------------------------------------ table

ACTIONS: Tuple[Action, ...] = (
    # Tier 0: keep the customer connected
    Action("I1", 0, 1, "in_trip", "topup_runout", "Top up before data runs out", ("push", "email", "whatsapp"), "medium", _i1, Value(0.45, 0.09, 9), capture=True, experiment="E1"),
    Action("I2", 0, 2, "in_trip", "plan_extend", "Extend or buy a plan for the same destination", ("push", "email"), "medium", _i2, Value(0.35, 0.08, 14), capture=True),
    Action("I3", 0, 3, "in_trip", "always_on_ending", "Always On data ending: buy a plan", ("push", "email"), "medium", _i3, Value(0.40, 0.07, 8), replaces=("RX3ADw",)),
    Action("P1", 0, 4, "pre_trip", "activate_help", "Activation help, device-specific", ("email", "push"), "low", _p1, Value(0.55, 0.08, 12), experiment="E2"),
    Action("I6", 0, 5, "in_trip", "add_destination", "Add the new destination", ("push",), "medium", _i6, Value(0.20, 0.10, 12), needs_data=16),
    # Tier 1: useful app habit in the moment
    Action("B1", 1, 1, "between_trips", "always_on_claim", "Claim this month's 1 GB", ("push", "in_app", "email"), "none", _b1, Value(0.38, 0.15, 3), experiment="E6"),
    Action("I4", 1, 2, "in_trip", "app_balance_intrip", "Check your balance in the app", ("push", "email"), "high", _i4, Value(0.30, 0.12, 1.4)),
    Action("I5", 1, 3, "in_trip", "feature_intro_intrip", "Introduce VPN, virtual location or safe browsing", ("in_app", "push"), "high", _i5, Value(0.08, 0.006, 6)),
    Action("C1", 1, 4, "between_trips", "capture_intent", "Finish the purchase in the app (intent capture)", ("native_flow",), "none", _c1, Value(0.80, 0.01, 20), capture=True, capture_action=True, experiment="E11",
           replaces=("SMEfpK", "Y3MGDJ", "X8RSQq", "YqWmDE", "RxgHLm", "Xpnhkr", "TvFpJJ", "YkTUXw", "TUTQFa", "QeUyvL")),
    Action("C2", 1, 5, "between_trips", "capture_window", "Your eSIM for the destination, ready in the app", ("push", "email"), "none", _c2, Value(0.50, 0.02, 20), capture=True, capture_action=True, experiment="E11"),
    # Tier 2: growth
    Action("T1", 2, 1, "post_trip", "always_on_enrol", "Claim 1 GB a month with Always On", ("email", "push"), "high", _t1, Value(0.05, 0.05, 30)),
    Action("T2", 2, 2, "post_trip", "next_destination", "Your eSIM works in 190+ destinations", ("email",), "high", _t2, Value(0.10, 0.025, 25), capture=True,
           replaces=("UTREx6", "SEhSMh", "T2SZRT")),
    Action("T3", 2, 3, "post_trip", "app_install_posttrip", "Keep your eSIM and perks in the app", ("email",), "high", _t3, Value(0.04, 0.04, 7), experiment="E4"),
    Action("P2", 2, 4, "pre_trip", "app_install_pretrip", "Install the app to manage the eSIM", ("email",), "low", _p2, Value(0.06, 0.05, 6), experiment="E4"),
    Action("P3", 2, 5, "pre_trip", "feature_primer", "Prime one companion feature (safe browsing)", ("push", "in_app"), "low", _p3, Value(0.12, 0.004, 5)),
    # Tier 3: advocacy (X1 = I7 in-trip / T4 post-trip)
    Action("I7", 3, 1, "in_trip", "referral_intrip", "Refer a friend (in-trip)", ("in_app", "email"), "high", _i7, Value(0.02, 0.04, 9), experiment="E9"),
    Action("T4", 3, 2, "post_trip", "referral_posttrip", "Refer a friend (post-trip)", ("email",), "high", _t4, Value(0.02, 0.035, 9), experiment="E10"),
    Action("B2", 3, 3, "between_trips", "seasonal_intent", "Seasonal next-trip prompt", ("email",), "none", _b2, Value(0.06, 0.02, 25), capture=True,
           replaces=("ReC6vj", "XrinQr", "YtYkP2")),
)

FIXED: Tuple[Action, ...] = (
    Action("F1", 99, 1, "trip_end_provisional", "trip_feedback", "Trip feedback, 0-10 score", ("in_app", "email"), "none", _never, under_test=False, fixed=True),
    Action("R1", 99, 2, "post_trip", "review_invite", "Public review invitation", ("email",), "none", _never, under_test=False, fixed=True),
    Action("R2", 99, 3, "in_trip", "rating_prompt", "App-store rating prompt (native)", ("in_app",), "none", _never, under_test=False, fixed=True),
)

ACTION_BY_ID: Dict[str, Action] = {a.id: a for a in ACTIONS + FIXED}

SHORT_NAME = {
    "I1": "top-up prompt", "I2": "plan extension", "I3": "Always On ending", "P1": "activation help",
    "I6": "add destination", "B1": "Always On claim", "I4": "app balance", "I5": "feature intro",
    "C1": "intent capture", "C2": "purchase-window capture", "T1": "Always On enrol",
    "T2": "next destination", "T3": "app install", "P2": "app install", "P3": "feature primer",
    "I7": "referral", "T4": "referral", "B2": "seasonal prompt", "F1": "trip feedback",
    "R1": "review invitation", "R2": "rating prompt",
}

REASON_TEXT = {
    "HOLDOUT": "Customer is in the measurement holdout; no NBA marketing until the quarter ends",
    "CARE_OPEN": "Open Intercom conversation in the last 72 h",
    "REFUNDED": "Refunded or cancelled order for the eSIM concerned",
    "EMAIL_COMPLAINT": "Hard bounce or spam complaint on email in the last 90 days",
    "CAP_REACHED": "Marketing sends already at the cap in the last 30 days",
    "BUDGET_RESERVED_FOR_TIER0": "Two sends held back for connectivity actions while pre-trip or in-trip",
    "RECENCY_GAP": "Last marketing send less than 24/48/72 h ago",
    "LOW_TRIP_CONFIDENCE": "Trip confidence below the action's minimum",
    "CONTENT_FATIGUE": "Same message ignored twice in 60 days",
    "NO_DELIVERABLE_CHANNEL": "No consented, deliverable channel for this action",
    "ACTION_CONTROL": "Randomly assigned to this action's control group",
    "OUTRANKED": "Eligible, but a higher-ranked action was chosen",
    "USAGE_STALE": "Usage data older than 48 h; run-out prompt withheld",
    "NO_ELIGIBLE_ACTION": "Nothing useful to say at this point of the trip",
    "CAPTURE_ONLY": "Chosen for capture value: likely to buy anyway, and the click would otherwise go to paid",
    "KILL_SWITCH": "Action paused by an operator kill switch",
    "DEMOTED": "No revenue uplift in its control after 60 days; demoted to no message",
    "CAPTURE_BUDGET_SPENT": "Capture sub-budget already used",
    "NOT_AT_DECISION_POINT": "Not at this action's decision point",
    "NOT_ELIGIBLE": "Does not meet the eligibility rule",
    "ACTION_SUPPRESSED": "Action-specific suppression applies",
    "OPERATOR_EXCLUDE": "Removed from NBA marketing by an operator (nba_exclude)",
    "MANUAL_ACTION": "Forced by an operator (nba_manual_action); excluded from uplift training",
    "FIXED_RULE": "Fixed-rule send scheduled before ranking",
    "SCORE_NOT_POSITIVE": "Uplift ranking: S(a) is not above zero, so no message wins",
    "DATA_GAP": "Needs data that does not exist yet",
    "ALREADY_HELD": "A decision for this customer is already waiting for its send window",
}
