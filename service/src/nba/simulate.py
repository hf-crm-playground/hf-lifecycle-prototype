"""A synthetic world to exercise the whole learning loop end to end.

Each simulated day, new in-trip customers with low data reach the I1
(top-up) decision point. The true top-up probability depends on channel and
region, and the rules' fixed preference (push first) is wrong outside Europe:

    europe: push .50  email .35  whatsapp .40
    latam:  push .30  email .30  whatsapp .55
    us:     push .35  email .45  (no WhatsApp)

The loop is the production one: decide (rules or learner) -> log ->
conversions arrive as customer events -> nightly attribute -> train ->
promotion gate. It reports, per week, the share of decisions that picked the
best channel and the regret against the best channel. This is a test of the
machinery, not evidence about real customers.
"""

from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Dict, List

from .config import DEFAULT_POLICY
from .context import ContactHistory, CustomerSignals, DecisionContext
from .learner import Model, prior_model, train
from .policy import decide
from .rewards import attribute
from .store import Store

TRUTH = {
    "europe": {"push": 0.50, "email": 0.35, "whatsapp": 0.40},
    "latam": {"push": 0.30, "email": 0.30, "whatsapp": 0.55},
    "us": {"push": 0.35, "email": 0.45},
}
MARKETS = {"europe": ("ES", "Europe/Madrid"), "latam": ("MX", "America/Mexico_City"), "us": ("US", "America/New_York")}
TOPUP_VALUE = 9.0
# Top-up rate with no message at all: the baseline the action-level control measures.
CONTROL_RATE = {"europe": 0.22, "latam": 0.20, "us": 0.24}


def _ctx(cid: str, region: str, now: datetime) -> DecisionContext:
    market, tz = MARKETS[region]
    s = CustomerSignals(customer_id=cid, market=market, locale="es-ES", home_timezone=tz, consent_email=True,
                        consent_whatsapp=region != "us", app_installed=True, push_token=True,
                        app_identity="linked")
    return DecisionContext(
        customer_id=cid, decided_at=now, phase="in_trip", trip_confidence="high", trip_id=f"{cid}:t",
        destination="ES", market=market, locale="es-ES", local_tz="Europe/Madrid", tz_source="destination",
        days_in_trip=6, days_since_last_usage=0, days_after_confirm=None, days_since_trip_end=None,
        data_remaining_pct=12.0, runout_before_plan_end=True, projected_runout_days=1.0, plan_end_hours=120.0,
        plan_ended_usage_stopped=False, usage_fresh=True, usage_last_24h=True, pending_activation=False,
        declared_departure_days=None, network_outside_plan=False, s=s, h=ContactHistory())


def simulate(days: int = 56, per_day: int = 300, mode: str = "learner", seed: int = 11,
             start: datetime = datetime(2026, 11, 2, 11, 0, tzinfo=timezone.utc)) -> Dict[str, object]:
    rng = random.Random(seed)
    store = Store()
    policy = replace(DEFAULT_POLICY, ranking="learner" if mode == "learner" else "v1", global_holdout=0.0)
    model: Model = prior_model(start)
    weekly = defaultdict(lambda: {"n": 0, "best": 0, "regret": 0.0})
    models: List[Dict[str, object]] = []
    for day in range(days):
        now = start + timedelta(days=day)
        for i in range(per_day):
            region = rng.choice(list(TRUTH))
            cid = f"sim{day:03d}{i:04d}"
            d = decide(_ctx(cid, region, now), policy, model=model)
            if d.outcome != "send":
                if d.arm == "action_control":
                    store.log(d, "logged", now)  # the control baseline for the uplift table
                    if rng.random() < CONTROL_RATE[region]:
                        store.add_customer_events([{"event_id": f"ev-{cid}", "customer_id": cid,
                                                    "metric": "Top-up order created",
                                                    "occurred_at": now + timedelta(hours=5), "value": TOPUP_VALUE}])
                continue
            store.log(d, "dispatched", now)
            truth = TRUTH[region]
            p = truth[d.channel]
            w = weekly[day // 7]
            w["n"] += 1
            w["best"] += int(p == max(truth.values()))
            w["regret"] += max(truth.values()) - p
            if rng.random() < p:
                store.add_customer_events([{"event_id": f"ev-{cid}", "customer_id": cid, "metric": "Top-up order created",
                                            "occurred_at": now + timedelta(hours=5), "value": TOPUP_VALUE}])
        # Nightly: attribute closed windows, retrain, promote (always, in simulation).
        night = now + timedelta(hours=16)
        attribute(store, night, policy, dispatch_modes=None)
        if mode == "learner":
            rows = [dict(r) for r in store.rewards()]
            if rows:
                model = train(rows, night)
                models.append({"day": day, "version": model.version, "rows": model.n_rows})
    out_weeks = []
    for wk, w in sorted(weekly.items()):
        out_weeks.append({"week": wk + 1, "decisions": w["n"], "best_channel_share": round(w["best"] / w["n"], 3),
                          "regret_per_decision": round(w["regret"] / w["n"], 4)})
    from .evaluate import first_listed, greedy, ips, logged_rows
    from .rewards import uplift_report

    logged = logged_rows(store)
    ope = [ips(logged, first_listed(), "rules").as_dict()]
    if mode == "learner":
        ope.append(ips(logged, greedy(model), model.version).as_dict())
    uplift = [r.as_dict() for r in uplift_report(store, min_control_outcomes=50)]
    final = {region: model.greedy_key(
        [{"action": "I1", "channel": ch, "lambda": 0.2} for ch in TRUTH[region]],
        f"in_trip|low|app|{region}") for region in TRUTH} if mode == "learner" else None
    store.close()
    return {"mode": mode, "weeks": out_weeks, "final_greedy": final, "models_trained": len(models),
            "ope": ope, "uplift": uplift}
