"""Scheduled jobs: hourly decisions and the nightly learning loop.

hourly  (every hour, at :05)
  1. pull Klaviyo deltas: profile signals, customer events, message events
  2. build trip state (Klaviyo trip events or warehouse files + usage feed)
  3. derive customer signals as of now
  4. decide with the active model (learner mode) or the rules, dispatch

nightly (once a day, 03:30 UTC)
  1. full incremental pulls (message events 60 days, customer events)
  2. attribute rewards to every decision whose window has closed
  3. uplift table per action from the action-level controls
  4. train a new model, evaluate it off-policy against the active model and
     the rules, and promote it only if it is not worse (promotion gate)
  5. guardrails: pause actions over the unsubscribe or spam limits
  6. write a JSON report

Each job takes a lock in the store (so overlapping schedules never run
twice) and records itself in `job_runs` with its stats or its error.
"""

from __future__ import annotations

import json
import os
import traceback
import uuid
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from trip_state.builder import build_many
from trip_state.cli import read_csv
from trip_state.models import Activation, Customer, Entitlement, Order, TripSnapshot, UsageDay

from .config import DEFAULT_POLICY, PolicyConfig
from .context import CustomerSignals
from .evaluate import first_listed, greedy, ips, logged_rows
from .guardrails import enforce
from .io import read_signals_csv
from .klaviyo.client import KlaviyoClient
from .learner import Model, prior_model, train
from .rewards import attribute, uplift_report, uplift_table
from .run import RunResult, run_hour
from .store import Store

PROMOTION_TOLERANCE = 0.02  # a new model may be at most 2% worse (SNIPS) than the active one
EVAL_SHARE = 0.2  # newest share of reward rows held out for off-policy evaluation
MIN_EVAL_ROWS = 200


@dataclass
class Inputs:
    customers: Optional[str] = None
    orders: Optional[str] = None
    activations: Optional[str] = None
    usage: Optional[str] = None
    entitlements: Optional[str] = None
    signals: Optional[str] = None
    from_klaviyo: bool = False
    usage_as_of: Optional[datetime] = None


def can_read(client: KlaviyoClient) -> bool:
    return client.mode in ("dry_run", "live")


def load_snapshots(inputs: Inputs, client: KlaviyoClient, now: datetime
                   ) -> Tuple[Dict[str, TripSnapshot], Dict[str, CustomerSignals], Dict[str, date], Dict[str, Any]]:
    base = read_signals_csv(inputs.signals)
    info: Dict[str, Any] = {}
    if inputs.from_klaviyo:
        from .klaviyo.ingest import pull_trip_inputs

        ti = pull_trip_inputs(client, now)
        customers: List[Customer] = list(ti.customers.values())
        orders, acts, ents = list(ti.orders.values()), ti.activations, ti.entitlements
        for cid, pid in ti.profile_ids.items():
            s = base.get(cid) or CustomerSignals(customer_id=cid)
            base[cid] = replace(s, klaviyo_profile_id=s.klaviyo_profile_id or pid)
        info["unknown_destinations"] = dict(ti.unknown_destinations.most_common(10))
    else:
        if not (inputs.customers and inputs.orders):
            raise ValueError("give builder CSVs (customers, orders, ...) or from_klaviyo=True")
        customers = read_csv(inputs.customers, Customer)
        orders = read_csv(inputs.orders, Order)
        acts = read_csv(inputs.activations, Activation)
        ents = read_csv(inputs.entitlements, Entitlement)
    usage = read_csv(inputs.usage, UsageDay)
    built = build_many(customers, orders, acts, usage, ents, as_of=now, usage_data_as_of=inputs.usage_as_of)
    snaps = {cid: s for cid, (s, _) in built.items()}
    declared = {c.customer_id: c.declared_travel_start for c in customers if c.declared_travel_start}
    info["customers"] = len(snaps)
    return snaps, base, declared, info


def load_active_model(store: Store, now: datetime, epsilon: float = 0.10) -> Model:
    row = store.active_model()
    if row is None:
        return prior_model(now, epsilon)
    return Model.from_params(json.loads(row["params"]), datetime.fromisoformat(row["trained_at"]))


def _job(store: Store, name: str, now: datetime, ttl: timedelta, fn) -> Dict[str, Any]:
    """`now` is the logical time the job decides as of (it can be back-dated
    for a replay); locks and job_runs use the wall clock."""
    wall = datetime.now(timezone.utc)
    run_id = f"{name}-{now.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:6]}"
    if not store.acquire_lock(name, run_id, wall, ttl):
        return {"job": name, "status": "skipped", "reason": "another run holds the lock"}
    store.start_job(run_id, name, wall)
    try:
        stats = fn()
        stats["as_of"] = now.isoformat()
        store.finish_job(run_id, "ok", datetime.now(timezone.utc), stats)
        return {"job": name, "run_id": run_id, "status": "ok", **stats}
    except Exception as e:  # recorded, then re-raised so the scheduler sees the failure
        store.finish_job(run_id, "failed", datetime.now(timezone.utc), error=traceback.format_exc()[-4000:])
        raise
    finally:
        store.release_lock(name, run_id)


def hourly(now: datetime, store: Store, client: KlaviyoClient, inputs: Inputs,
           policy: PolicyConfig = DEFAULT_POLICY) -> Dict[str, Any]:
    def body() -> Dict[str, Any]:
        stats: Dict[str, Any] = {}
        if can_read(client):
            from .klaviyo.outcomes import pull_message_events
            from .klaviyo.signals_sync import pull_customer_events, pull_profile_signals

            stats["profiles_synced"] = pull_profile_signals(client, store, now)
            stats["customer_events"] = sum(pull_customer_events(client, store, now, lookback=timedelta(days=3)).values())
            stats["message_events"] = sum(pull_message_events(client, store, now, lookback=timedelta(days=3)).values())
        snaps, base, declared, info = load_snapshots(inputs, client, now)
        stats.update(info)
        from .klaviyo.signals_sync import derive_signals

        signals = derive_signals(store, now, base, customers=snaps.keys())
        model = load_active_model(store, now) if policy.ranking == "learner" else None
        res: RunResult = run_hour(now, snaps, signals, store, client, policy, declared=declared, model=model)
        stats["run"] = res.summary()
        stats["model_version"] = model.version if model else None
        return stats

    return _job(store, "hourly", now, timedelta(hours=3), body)


def nightly(now: datetime, store: Store, client: KlaviyoClient, policy: PolicyConfig = DEFAULT_POLICY,
            report_dir: Optional[str] = None, train_days: int = 180,
            dispatch_modes: Optional[Tuple[str, ...]] = ("live",)) -> Dict[str, Any]:
    """`dispatch_modes=None` learns from every logged send, including dry-run
    and offline ones (simulation and tests only)."""
    def body() -> Dict[str, Any]:
        stats: Dict[str, Any] = {}
        if can_read(client):
            from .klaviyo.outcomes import pull_message_events
            from .klaviyo.signals_sync import pull_customer_events

            stats["message_events"] = sum(pull_message_events(client, store, now).values())
            stats["customer_events"] = sum(pull_customer_events(client, store, now).values())
        stats["rewards"] = attribute(store, now, policy, dispatch_modes=dispatch_modes)
        up = uplift_report(store)
        stats["uplift"] = [r.as_dict() for r in up]
        stats["uplift_table_actions"] = sorted(uplift_table(up))
        stats["demotion_candidates"] = [r.action for r in up if r.status == "demote"]

        # Train on everything before the split, evaluate off-policy on the
        # learner's randomised rows after it (out of sample), then refit on
        # all rows if the candidate passes the gate.
        since = now - timedelta(days=train_days)
        rows = [dict(r) for r in store.rewards(since)]
        times = sorted(r["decided_at"] for r in rows)
        split = times[int(len(times) * (1 - EVAL_SHARE))] if len(times) >= 20 else None
        train_rows = [r for r in rows if split is None or r["decided_at"] < split]
        candidate = train(train_rows, now)
        active = load_active_model(store, now)
        held_out = logged_rows(store, since=split) if split else []
        evals = {
            "split_at": split,
            "eval_rows": len(held_out),
            "rules": ips(held_out, first_listed(), "rules").as_dict(),
            "active": ips(held_out, greedy(active), active.version).as_dict(),
            "candidate": ips(held_out, greedy(candidate), candidate.version).as_dict(),
        }
        stats["ope"] = evals
        if not rows:
            promote, why = False, "no reward rows yet"
        elif len(held_out) < MIN_EVAL_ROWS:
            promote = active.version == "prior"
            why = (f"only {len(held_out)} held-out learner rows: "
                   + ("first model promoted to start learning" if promote else "keeping the active model"))
        else:
            base = evals["active"]["snips"]
            promote = evals["candidate"]["snips"] >= base - abs(base) * PROMOTION_TOLERANCE
            why = f"held-out SNIPS {evals['candidate']['snips']:.3f} vs active {base:.3f}"
        final = train(rows, now) if promote else candidate
        store.save_model(final.version, now, final.n_rows, final.to_params(), evals, activate=promote)
        stats["model"] = {"version": final.version, "rows": final.n_rows, "promoted": promote, "why": why}
        stats["guardrails"] = enforce(store, now, dispatch_modes)
        if report_dir:
            os.makedirs(report_dir, exist_ok=True)
            path = os.path.join(report_dir, f"nightly_{now.strftime('%Y%m%d')}.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(stats, fh, indent=2, default=str)
            stats["report"] = path
        return stats

    return _job(store, "nightly", now, timedelta(hours=3), body)
