"""S(a), the per-action objective from the design.

S(a) = dR + Cap(a) + w_app*dA + w_util*U - lambda*1[a != none]
       - c_unsub*dP_unsub - c_spam*dP_spam

Cap(a) = P0(buy) * P(paid last click) * P(a wins) * c_paid - d(a)

v1: w_app = w_util = 0 and c_spam = 5 * c_unsub. "No message" scores 0, so
in uplift-table mode an action is sent only when S(a) > 0. Values come from
the action's sample model until the action-level controls produce an uplift
table (pass it as `uplift`, keyed by action id).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Dict, Optional

from .actions import P_WINS, Action, Value
from .config import PolicyConfig
from .context import DecisionContext

# Placeholder per-send risk by channel until guardrail data replaces it.
UNSUB_RISK = {"email": 0.0008, "push": 0.0015, "whatsapp": 0.002}
SPAM_RISK = {"email": 0.0001}


@dataclass(frozen=True)
class Score:
    d_r: float
    cap: float
    lam: float
    unsub: float
    spam: float
    total: float

    def as_dict(self) -> Dict[str, float]:
        return {k: round(v, 4) for k, v in asdict(self).items()}


def score(
    c: DecisionContext,
    a: Action,
    channel: Optional[str],
    p: PolicyConfig,
    uplift: Optional[Dict[str, Value]] = None,
) -> Optional[Score]:
    v = (uplift or {}).get(a.id, a.value)
    if v is None:
        return None
    d_r = v.lift * v.per_conv
    cap = 0.0
    if a.capture and channel:
        p0 = c.s.p0_buy if a.capture_action else v.base
        paid = min(0.95, c.s.paid_share * (1.2 if c.s.coupon_user else 1.0))
        cap = p0 * paid * P_WINS.get(channel, 0.4) * p.c_paid - a.perk_cost
    counts = channel is not None and channel != "in_app"
    lam = p.lambda_send if counts else 0.0
    unsub = p.c_unsub * UNSUB_RISK.get(channel or "", 0.0)
    spam = 5 * p.c_unsub * SPAM_RISK.get(channel or "", 0.0)
    return Score(d_r, cap, lam, unsub, spam, d_r + cap - lam - unsub - spam)
