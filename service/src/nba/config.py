"""Policy v1 settings from the Holafly NBA System Design (23 Sep 2026).

Every number is a policy choice, not a fact about customers. Change them in
one place; the tests pin the defaults. `policy_version` is written to every
decision-log row so a result can always be traced to the settings that made it.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Dict, FrozenSet, Optional, Tuple

POLICY_VERSION = "v1.0"


@dataclass(frozen=True)
class PolicyConfig:
    policy_version: str = POLICY_VERSION

    # Contact pressure: marketing sends per rolling 30 days, all sources.
    cap_30d: int = 8
    subcap: Tuple[Tuple[str, int], ...] = (("email", 6), ("push", 4), ("whatsapp", 2))
    # Sends held back for Tier 0 while the customer is pre-trip or in-trip.
    reserve_for_tier0: int = 2
    # Capture actions (C1, C2) may use at most this many of the 8.
    capture_budget: int = 2

    # Measurement. The holdout salt changes once a quarter, never mid-quarter.
    global_holdout: float = 0.10
    holdout_salt: str = "2026Q4"
    action_control: float = 0.10

    # Minimum hours since the last marketing send, by tier.
    min_gap_hours: Tuple[Tuple[int, int], ...] = ((0, 24), (1, 48), (2, 48), (3, 72))
    # A content key ignored this many times in this many days is fatigued.
    fatigue_ignores: int = 2
    fatigue_days: int = 60
    # An NBA send counts as "ignored" when nothing opens or clicks it within this window.
    ignore_window_hours: int = 72

    # Local send windows [start, end) in hours.
    window_email: Tuple[int, int] = (9, 20)
    window_push: Tuple[int, int] = (10, 19)
    window_whatsapp: Tuple[int, int] = (10, 19)
    window_tier0: Tuple[int, int] = (8, 23)
    # Push transactional messages (low-data alerts and similar) block marketing push for this long.
    push_after_transactional_hours: int = 6

    # Ranking: "v1" = tier, then fixed rank; "uplift" = tier, then S(a) > 0 (after 60 days).
    ranking: str = "v1"

    # S(a) parameters (EUR). v1: w_app = w_util = 0, c_spam = 5 x c_unsub.
    lambda_send: float = 0.20
    c_unsub: float = 30.0
    c_paid: float = 4.0
    w_app: float = 0.0
    w_util: float = 0.0

    # Operations.
    killed: FrozenSet[str] = frozenset()
    demoted: FrozenSet[str] = frozenset()
    market_cap: Tuple[Tuple[str, int], ...] = ()
    whatsapp_enabled: bool = True
    whatsapp_blocked_markets: FrozenSet[str] = frozenset({"US"})
    v2_network_events: bool = False
    min_paid_share_for_capture: float = 0.30

    # Klaviyo-side lifetimes.
    nba_event_ttl_hours: int = 24
    inapp_ttl_days: int = 7

    def subcap_for(self, channel: str) -> Optional[int]:
        return dict(self.subcap).get(channel)

    def gap_hours(self, tier: int) -> int:
        return dict(self.min_gap_hours)[tier]

    def cap_for(self, market: Optional[str]) -> int:
        return dict(self.market_cap).get(market or "", self.cap_30d)

    def window_for(self, channel: str, tier: Optional[int]) -> Optional[Tuple[int, int]]:
        if channel in ("in_app", "native_flow"):
            return None
        if tier == 0:
            return self.window_tier0
        return {
            "email": self.window_email,
            "push": self.window_push,
            "whatsapp": self.window_whatsapp,
        }.get(channel, self.window_email)

    def with_(self, **kw) -> "PolicyConfig":
        return replace(self, **kw)


DEFAULT_POLICY = PolicyConfig()
