"""The hourly run: snapshots + signals + contact history -> decisions ->
decision log -> Klaviyo (events and profile properties).

A customer is decided only at a decision point (some action's trigger or a
fixed rule is due). Unchanged "no message" outcomes are not re-logged every
hour: a row is written when the outcome, reason, candidates or trip change,
or after 24 h. A decision outside its send window is logged as `held`; when
the window opens the next run re-decides from fresh data and either releases
it (same action and channel) or supersedes it.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional, Set

from trip_state.klaviyo import to_profile_properties
from trip_state.models import TripSnapshot

from .actions import ACTIONS, NOT_AT_POINT, Value
from .config import DEFAULT_POLICY, PolicyConfig
from .context import CustomerSignals, DecisionContext, build_context
from .klaviyo.client import KlaviyoClient
from .klaviyo.payloads import bulk_import_bodies, nba_decision_event, nba_profile_properties
from .policy import Decision, decide
from .store import Store

HELD_TTL = timedelta(hours=24)
RELOG_AFTER = timedelta(hours=24)


@dataclass
class RunResult:
    as_of: datetime
    evaluated: int = 0
    at_decision_point: int = 0
    logged: int = 0
    unchanged: int = 0
    duplicate_hour: int = 0
    dispatched: int = 0
    held: int = 0
    waiting_for_window: int = 0
    released: int = 0
    superseded: int = 0
    expired: int = 0
    events_written: int = 0
    profile_jobs: int = 0
    profiles_synced: int = 0
    by_outcome: Counter = field(default_factory=Counter)
    by_reason: Counter = field(default_factory=Counter)
    by_action: Counter = field(default_factory=Counter)
    by_channel: Counter = field(default_factory=Counter)
    decisions: List[Decision] = field(default_factory=list)

    def summary(self) -> Dict[str, object]:
        d = {k: v for k, v in self.__dict__.items() if k != "decisions"}
        d["as_of"] = self.as_of.isoformat()
        for k in ("by_outcome", "by_reason", "by_action", "by_channel"):
            d[k] = dict(sorted(getattr(self, k).items(), key=lambda kv: -kv[1]))
        return d


def decision_points(c: DecisionContext) -> List[str]:
    """Actions whose trigger is met (eligible or not), plus fixed rules due."""
    pts = [a.id for a in ACTIONS if a.check(c) != NOT_AT_POINT]
    if not c.h.feedback_sent_this_trip and c.phase == "trip_end_provisional":
        pts.append("F1")
    if c.phase == "post_trip" and 3 <= (c.days_after_confirm or -1) <= 10 and not c.h.review_invited_this_trip:
        pts.append("R1")
    return pts


def _signature(d: Decision) -> str:
    return json.dumps([d.trip_id, d.outcome, d.reason, d.action, d.chosen_candidate,
                       sorted((r.id, r.why) for r in d.candidates)])


def _row_signature(row) -> str:
    cands = json.loads(row["candidates"])
    return json.dumps([row["trip_id"], row["outcome"], row["reason"], row["chosen_action"], row["chosen_candidate"],
                       sorted((r["id"], r["why"]) for r in cands)])


def run_hour(
    now: datetime,
    snapshots: Dict[str, TripSnapshot],
    signals: Dict[str, CustomerSignals],
    store: Store,
    client: KlaviyoClient,
    policy: PolicyConfig = DEFAULT_POLICY,
    declared: Optional[Dict[str, date]] = None,
    uplift: Optional[Dict[str, Value]] = None,
    only: Optional[Set[str]] = None,
    sync_trip_properties: bool = True,
) -> RunResult:
    now = now.astimezone(timezone.utc)
    res = RunResult(as_of=now)
    profiles: Dict[str, Dict[str, object]] = {}
    pids: Dict[str, Optional[str]] = {}

    for cid, snap in snapshots.items():
        if only is not None and cid not in only:
            continue
        sig = replace(signals.get(cid) or CustomerSignals(customer_id=cid))  # copy: the run adjusts it
        pids[cid] = sig.klaviyo_profile_id
        res.evaluated += 1
        if sync_trip_properties:
            profiles.setdefault(cid, {}).update(to_profile_properties(snap))

        held = store.pending_held(cid)
        if held is not None:
            if now - datetime.fromisoformat(held["decided_at"]) > HELD_TTL:
                store.set_status(held["decision_id"], "expired", now)
                res.expired += 1
                held = None
            elif datetime.fromisoformat(held["send_at"]) > now:
                res.waiting_for_window += 1
                continue

        history = store.history(cid, now, snap.trip_id, policy.fatigue_days, policy.fatigue_ignores,
                                policy.ignore_window_hours)
        last_txn_push = store.last_transactional_push(cid, now)
        if last_txn_push is not None:
            sig.hours_since_push_transactional = min(
                sig.hours_since_push_transactional, (now - last_txn_push).total_seconds() / 3600)
        ctx = build_context(snap, sig, history, now, (declared or {}).get(cid))
        points = decision_points(ctx)
        if not points and held is None and not sig.nba_manual_action:
            continue
        res.at_decision_point += 1
        d = decide(ctx, policy, uplift)

        if held is not None:
            same = d.outcome == "send" and d.action == held["chosen_action"] and d.channel == held["channel"]
            if same and not d.held:
                store.set_status(held["decision_id"], "released", now, released_by=d.decision_id)
                res.released += 1
            else:
                store.set_status(held["decision_id"], "superseded", now, released_by=d.decision_id)
                res.superseded += 1
        else:
            last = store.last_decision(cid)
            if (d.outcome == "none" and last is not None and last["outcome"] == "none"
                    and now - datetime.fromisoformat(last["decided_at"]) < RELOG_AFTER
                    and _row_signature(last) == _signature(d)):
                res.unchanged += 1
                continue

        status = "logged" if d.outcome == "none" else ("held" if d.held else "dispatched")
        if not store.log(d, status, now):
            res.duplicate_hour += 1
            continue
        res.logged += 1
        res.decisions.append(d)
        res.by_outcome[d.outcome] += 1
        if d.outcome == "send":
            res.by_action[d.action] += 1
            res.by_channel[d.channel] += 1
        if d.reason:
            res.by_reason[d.reason] += 1

        if status == "dispatched":
            body = nba_decision_event(d, now, policy, pids[cid])
            if body is not None:
                client.create_event(body, note=f"{d.action} {d.channel} {d.sentence}")
                res.events_written += 1
            store.set_status(d.decision_id, "dispatched", now, dispatch_mode=client.mode)
            res.dispatched += 1
        elif status == "held":
            res.held += 1
        profiles.setdefault(cid, {}).update(nba_profile_properties(d, now, policy))

    for body in bulk_import_bodies((cid, props, pids.get(cid)) for cid, props in profiles.items() if props):
        client.bulk_import_profiles(body, note=f"{len(body['data']['attributes']['profiles']['data'])} profiles")
        res.profile_jobs += 1
    res.profiles_synced = sum(1 for p in profiles.values() if p)
    return res


def release_held(now: datetime, snapshots: Dict[str, TripSnapshot], signals: Dict[str, CustomerSignals],
                 store: Store, client: KlaviyoClient, policy: PolicyConfig = DEFAULT_POLICY, **kw) -> RunResult:
    """Re-validate only the customers whose held decision's window has opened."""
    due = {r["customer_id"] for r in store.held_due(now)}
    return run_hour(now, snapshots, signals, store, client, policy, only=due, sync_trip_properties=False, **kw)
