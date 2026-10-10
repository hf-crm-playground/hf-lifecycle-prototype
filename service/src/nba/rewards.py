"""Reward attribution: decision log x customer events -> one reward row per
randomised decision once its window has closed.

Two outcomes per decision:

- `converted`: the action's own primary outcome inside its outcome window
  (top-up within 48 h for I1, install within 72 h for P1, ...). This is what
  the action-level control measures and what each action is accountable for.
- `reward`: one currency for all actions, so the learner can compare them:
  net revenue (orders + top-ups - refunds) in a common horizon after the
  send, minus the cost of an unsubscribe and of a spam complaint within 72 h.

Rows: treated = dispatched sends chosen by the policy (no operator
overrides, no fixed rules); control = action-level control rows, which
receive nothing and start their window at decided_at. Global-holdout rows
are measured separately, at customer level (intention to treat).

`uplift_table` turns closed rows into the design's uplift table (control
rate, treated - control, revenue per conversion) per action, which
`decide(..., uplift=...)` uses in ranking="uplift" mode.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional, Tuple

from .actions import ACTION_BY_ID, Value
from .config import DEFAULT_POLICY, PolicyConfig
from .policy import OUTCOME_WINDOW_HOURS
from .store import Store

REWARD_HORIZON_HOURS = 7 * 24
GUARDRAIL_HOURS = 72

ORDER_METRICS = ("hly_order_confirmed", "Top-up order created")
REFUND_METRICS = ("Refunded Order",)

PRIMARY_OUTCOME: Dict[str, Tuple[str, ...]] = {
    "I1": ("Top-up order created",),
    "I2": ORDER_METRICS, "I3": ORDER_METRICS, "I6": ORDER_METRICS,
    "P1": ("esim_installation_complete",),
    "B1": ("hly_consumption_start",),
    "I4": ("session_start",),
    "I5": ("virtual_location_toggle", "safe_browsing_toggle"),
    "P3": ("virtual_location_toggle", "safe_browsing_toggle"),
    "C1": ORDER_METRICS, "C2": ORDER_METRICS, "T2": ORDER_METRICS, "B2": ORDER_METRICS,
    "T1": ("hly_always_on_activated",),
    "T3": ("first_open",), "P2": ("first_open",),
    "I7": ("referral_link_shared", "referral_code_copied"),
    "T4": ("referral_link_shared", "referral_code_copied"),
    "F1": ("Submitted NPS Score",),
}


def _dt(s: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(s) if s else None


LATE_EVENT_GRACE = timedelta(days=7)  # rewards are recomputed until this long after the window closes


def attribute(store: Store, now: datetime, p: PolicyConfig = DEFAULT_POLICY,
              horizon_hours: int = REWARD_HORIZON_HOURS,
              dispatch_modes: Optional[Tuple[str, ...]] = ("live",)) -> Dict[str, int]:
    """`dispatch_modes`: only sends that really went out can earn a reward.
    Dry-run "dispatches" reached nobody, so by default they are excluded, and
    control rows only count from the first live send onwards (before that the
    treated customers received nothing either). Pass None to include every
    row (simulations, offline tests)."""
    done = {r["decision_id"]: datetime.fromisoformat(r["computed_at"])
            for r in store.db.execute("SELECT decision_id, computed_at FROM rewards")}
    q = ("SELECT * FROM decision_log WHERE override = 0 AND fixed_rule = 0 AND "
         "((outcome = 'send' AND status = 'dispatched') OR arm = 'action_control')")
    rows = store.db.execute(q).fetchall()
    live_since: Optional[str] = None
    if dispatch_modes is not None:
        marks = ",".join("?" for _ in dispatch_modes)
        live_since = store.db.execute(
            f"SELECT MIN(send_at) FROM decision_log WHERE dispatch_mode IN ({marks})", dispatch_modes).fetchone()[0]
    stats = defaultdict(int)
    pending = 0
    for r in rows:
        treated = r["arm"] != "action_control"
        if dispatch_modes is not None:
            if treated and r["dispatch_mode"] not in dispatch_modes:
                stats["not_live"] += 1
                continue
            if not treated and (live_since is None or r["decided_at"] < live_since):
                stats["not_live"] += 1
                continue
        action = r["chosen_action"] or r["chosen_candidate"]
        if action not in PRIMARY_OUTCOME:
            stats["no_outcome_defined"] += 1
            continue
        # Both arms start when the message went (or would have gone) out.
        start = _dt(r["send_at"]) or _dt(r["decided_at"])
        window = OUTCOME_WINDOW_HOURS.get(action, 72)
        close = start + timedelta(hours=max(window, horizon_hours, GUARDRAIL_HOURS))
        if close > now:
            stats["window_open"] += 1
            continue
        prev = done.get(r["decision_id"])
        if prev is not None and prev >= close + LATE_EVENT_GRACE:
            continue  # final
        cid = r["customer_id"]
        conv = store.events(cid, PRIMARY_OUTCOME[action], start, start + timedelta(hours=window))
        orders = store.events(cid, ORDER_METRICS, start, start + timedelta(hours=horizon_hours))
        refunds = store.events(cid, REFUND_METRICS, start, start + timedelta(hours=horizon_hours))
        revenue = sum(e["value"] or 0 for e in orders) - sum(e["value"] or 0 for e in refunds)
        g_end = start + timedelta(hours=GUARDRAIL_HOURS)
        unsub = 1 if store.message_kinds(cid, ("unsubscribed",), start, g_end) else 0
        spam = 1 if store.message_kinds(cid, ("spam",), start, g_end) else 0
        reward = revenue - p.c_unsub * unsub - 5 * p.c_unsub * spam
        store.upsert_reward({
            "decision_id": r["decision_id"], "customer_id": cid, "action": action, "channel": r["channel"],
            "arm": "treated" if treated else "control", "cell": r["cell"], "propensity": r["propensity"],
            "model_version": r["model_version"], "decided_at": r["decided_at"], "converted": int(bool(conv)),
            "revenue": round(revenue, 4), "unsubscribed": unsub, "spam": spam, "reward": round(reward, 4),
            "computed_at": now.astimezone(timezone.utc).isoformat(),
        })
        stats[("re" if prev is not None else "") + ("treated" if treated else "control")] += 1
        pending += 1
        if pending >= 500:  # short write transactions: the trigger server writes too
            store.commit()
            pending = 0
    store.commit()
    return dict(stats)


@dataclass
class UpliftRow:
    action: str
    n_treated: int
    n_control: int
    rate_treated: float
    rate_control: float
    uplift: float
    low: float
    high: float
    revenue_per_conversion: float
    control_outcomes: int
    status: str

    def as_dict(self):
        return {k: (round(v, 5) if isinstance(v, float) else v) for k, v in self.__dict__.items()}


def uplift_report(store: Store, since: Optional[datetime] = None, min_control_outcomes: int = 100) -> List[UpliftRow]:
    agg = defaultdict(lambda: {"t": [0, 0], "c": [0, 0], "rev": 0.0})
    for r in store.rewards(since):
        a = agg[r["action"]]
        k = "t" if r["arm"] == "treated" else "c"
        a[k][0] += 1
        a[k][1] += r["converted"]
        if r["arm"] == "treated" and r["converted"]:
            a["rev"] += r["revenue"]
    out = []
    for action, a in sorted(agg.items()):
        (nt, xt), (nc, xc) = a["t"], a["c"]
        pt = xt / nt if nt else 0.0
        pc = xc / nc if nc else 0.0
        se = math.sqrt(pt * (1 - pt) / max(nt, 1) + pc * (1 - pc) / max(nc, 1))
        up = pt - pc
        rpc = a["rev"] / xt if xt else 0.0
        if xc < min_control_outcomes:
            status = "collecting"
        elif up - 1.96 * se > 0:
            status = "keep"
        else:
            status = "demote"
        out.append(UpliftRow(action, nt, nc, pt, pc, up, up - 1.96 * se, up + 1.96 * se, rpc, xc, status))
    return out


def uplift_table(rows: Iterable[UpliftRow]) -> Dict[str, Value]:
    """Value models for actions whose control has enough outcomes; others
    keep their prior from the decision table (the parent fallback)."""
    table = {}
    for r in rows:
        if r.status == "collecting":
            continue
        prior = ACTION_BY_ID[r.action].value
        per_conv = r.revenue_per_conversion or (prior.per_conv if prior else 0.0)
        table[r.action] = Value(base=r.rate_control, lift=r.uplift, per_conv=per_conv)
    return table


def demotions(rows: Iterable[UpliftRow]) -> List[str]:
    return [r.action for r in rows if r.status == "demote"]
