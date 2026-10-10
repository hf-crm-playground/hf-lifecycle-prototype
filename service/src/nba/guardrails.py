"""Guardrails that act, not just report.

Nightly, per action over the trailing window:

- email unsubscribe rate per NBA send above 0.5%, or above 1.5x the rate of
  existing lifecycle flows -> pause the action
- spam complaint rate above 0.1% -> pause the action immediately
- the cap: any customer above it is a bug, reported for reconciliation

A paused action is written to `policy_state` and every run reads it as a
kill switch, so the next hourly run stops choosing it. Re-enabling is an
operator decision (`nba guardrails --resume ACTION`), never automatic.
Minimum sample sizes keep one unlucky unsubscribe from pausing an action.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List

from .store import Store, _iso

UNSUB_MAX = 0.005
UNSUB_VS_FLOWS = 1.5
SPAM_MAX = 0.001
MIN_SENDS_UNSUB = 400
MIN_SENDS_SPAM = 1000
WINDOW_DAYS = 14
ATTRIBUTION_HOURS = 72


@dataclass
class GuardrailRow:
    action: str
    sends: int
    unsubscribes: int
    spam: int
    unsub_rate: float
    spam_rate: float
    verdict: str  # ok | pause_unsub | pause_spam | collecting

    def as_dict(self):
        return {k: (round(v, 5) if isinstance(v, float) else v) for k, v in self.__dict__.items()}


def _flows_unsub_rate(store: Store, since: datetime, until: datetime) -> float:
    rec = store.db.execute(
        "SELECT COUNT(*) FROM message_events WHERE kind = 'received' AND channel = 'email' AND nba = 0 "
        "AND transactional = 0 AND occurred_at >= ? AND occurred_at < ?", (_iso(since), _iso(until))
    ).fetchone()[0]
    uns = store.db.execute(
        "SELECT COUNT(*) FROM message_events WHERE kind = 'unsubscribed' AND channel = 'email' AND nba = 0 "
        "AND occurred_at >= ? AND occurred_at < ?", (_iso(since), _iso(until))).fetchone()[0]
    return uns / rec if rec else 0.0


def evaluate(store: Store, now: datetime, dispatch_modes=("live",)) -> Dict[str, object]:
    """`dispatch_modes`: only real sends can cause unsubscribes; a dry-run
    "send" would otherwise collect unsubscribes caused by other flows."""
    since = now - timedelta(days=WINDOW_DAYS)
    # After an operator resumes an action, only sends after the resume count,
    # so the old breach does not re-pause it the next night.
    resumed = {r["action"]: datetime.fromisoformat(r["changed_at"]) for r in store.db.execute(
        "SELECT action, changed_at FROM policy_state WHERE killed = 0 AND source = 'operator'")}
    sends = [s for s in store.db.execute(
        "SELECT decision_id, customer_id, chosen_action, send_at, dispatch_mode FROM decision_log WHERE status = 'dispatched' "
        "AND channel = 'email' AND send_at >= ? AND send_at < ?",
        (_iso(since), _iso(now - timedelta(hours=ATTRIBUTION_HOURS))),
    ).fetchall() if (dispatch_modes is None or s["dispatch_mode"] in dispatch_modes) and (s["chosen_action"] not in resumed or datetime.fromisoformat(s["send_at"]) >= resumed[s["chosen_action"]])]
    agg = defaultdict(lambda: [0, 0, 0])
    for s in sends:
        t = datetime.fromisoformat(s["send_at"])
        end = t + timedelta(hours=ATTRIBUTION_HOURS)
        a = agg[s["chosen_action"]]
        a[0] += 1
        a[1] += bool(store.message_kinds(s["customer_id"], ("unsubscribed",), t, end))
        a[2] += bool(store.message_kinds(s["customer_id"], ("spam",), t, end))
    flows = _flows_unsub_rate(store, since, now)
    rows: List[GuardrailRow] = []
    for action, (n, u, sp) in sorted(agg.items()):
        ur, sr = (u / n if n else 0.0), (sp / n if n else 0.0)
        if n >= MIN_SENDS_SPAM and sr > SPAM_MAX:
            verdict = "pause_spam"
        elif n >= MIN_SENDS_UNSUB and (ur > UNSUB_MAX or (flows > 0 and ur > UNSUB_VS_FLOWS * flows)):
            verdict = "pause_unsub"
        elif n < MIN_SENDS_UNSUB:
            verdict = "collecting"
        else:
            verdict = "ok"
        rows.append(GuardrailRow(action, n, u, sp, ur, sr, verdict))
    over_cap = store.db.execute(
        "SELECT customer_id, COUNT(*) AS n FROM decision_log WHERE status = 'dispatched' AND counts_toward_cap = 1 "
        "AND send_at >= ? GROUP BY customer_id HAVING n > 8", (_iso(now - timedelta(days=30)),)
    ).fetchall()
    return {"window_days": WINDOW_DAYS, "flows_unsub_rate": round(flows, 5), "actions": [r.as_dict() for r in rows],
            "customers_over_cap": len(over_cap)}


def enforce(store: Store, now: datetime, dispatch_modes=("live",)) -> Dict[str, object]:
    report = evaluate(store, now, dispatch_modes)
    paused = []
    already = store.killed_actions()
    for r in report["actions"]:
        if r["verdict"].startswith("pause") and r["action"] not in already:
            reason = (f"{r['verdict']}: unsub {r['unsub_rate']:.2%}, spam {r['spam_rate']:.2%} "
                      f"on {r['sends']} sends in {WINDOW_DAYS} d")
            store.set_killed(r["action"], True, reason, "guardrail", now)
            paused.append(r["action"])
    report["paused_now"] = paused
    report["paused_total"] = sorted(store.killed_actions())
    return report


def resume(store: Store, action: str, now: datetime, who: str = "operator") -> None:
    store.set_killed(action, False, f"resumed by {who}", "operator", now)
