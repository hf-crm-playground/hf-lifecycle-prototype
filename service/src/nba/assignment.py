"""Stable random assignment: global holdout, action-level control, decision ids.

`stable_hash` is 32-bit FNV-1a over UTF-16 code units, mapped to [0, 1). It
is bit-for-bit the same function as `stableHash` in the prototype
(src/engine/rng.js); the tests pin shared values. The service adds the
quarter's salt to the holdout key, so the holdout is re-drawn once a quarter
and never in between. Assignment depends only on its inputs, never on run
order, so re-running a day reproduces it exactly.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

_FNV_OFFSET = 0x811C9DC5
_FNV_PRIME = 0x01000193


def stable_hash(s: str) -> float:
    h = _FNV_OFFSET
    data = s.encode("utf-16-le")
    for i in range(0, len(data), 2):
        h ^= data[i] | (data[i + 1] << 8)
        h = (h * _FNV_PRIME) & 0xFFFFFFFF
    return h / 4294967296.0


def in_global_holdout(customer_id: str, share: float, salt: str) -> bool:
    """Fixed for a quarter: the salt changes only at the quarter boundary."""
    return stable_hash(f"{customer_id}|holdout|{salt}") < share


def in_action_control(customer_id: str, action_id: str, trip_id: Optional[str], share: float) -> bool:
    """Same customer, action and trip -> same arm, however often the action comes up."""
    return stable_hash(f"{customer_id}|{action_id}|{trip_id or 'none'}") < share


def decision_id(customer_id: str, decided_at: datetime) -> str:
    """One decision per customer per hourly run; re-running the hour gives the
    same id, which is also the Klaviyo event unique_id (idempotent writes)."""
    hour = decided_at.strftime("%Y%m%dT%H")
    a = int(stable_hash(f"{customer_id}|{hour}") * 4294967296)
    b = int(stable_hash(f"{hour}|{customer_id}|nba") * 4294967296)
    return f"dec_{hour}_{a:08x}{b:08x}"
