"""Command line for the Trip Decision Service.

Decisions
  run            one hourly decision run (rules, uplift or learner ranking)
  release-held   re-validate held decisions whose window has opened
  explain        why a customer got what they got

Klaviyo (reads are live in dry_run; writes go to the outbox)
  verify-klaviyo read-only account checks
  sync           profile signals, customer events and message events (incremental)
  pull-outcomes  message events -> cap ledger only

Learning
  attribute      rewards for every decision whose window has closed
  uplift         the uplift table from the action-level controls
  train          fit a model from rewards (optionally activate it)
  evaluate       off-policy comparison: rules vs active model vs candidates
  models         list models, or activate one
  guardrails     evaluate and enforce unsubscribe/spam pauses; resume an action

Operations
  hourly         sync + build + decide, under a lock, recorded in job_runs
  nightly        sync + attribute + uplift + train + gate + guardrails + report
  serve          real-time trigger endpoint
  jobs           recent job runs
  setup-spec     what to build in Klaviyo
  simulate       synthetic world: show the learning loop beating the rules

Klaviyo mode comes from --mode or NBA_KLAVIYO_MODE: offline (no network),
dry_run (live reads, writes to the outbox; the default when a key is set) or
live (also needs NBA_KLAVIYO_LIVE_WRITES=yes).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from .config import DEFAULT_POLICY
from .klaviyo.client import KlaviyoClient, client_from_env
from .store import Store


def _now(s: Optional[str]) -> datetime:
    if not s:
        return datetime.now(timezone.utc)
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _client(args, need_reads: bool = False) -> KlaviyoClient:
    mode = getattr(args, "mode", None) or os.environ.get("NBA_KLAVIYO_MODE") or (
        "dry_run" if os.environ.get("KLAVIYO_API_KEY") else "offline")
    if need_reads and mode == "offline":
        sys.exit("This command reads from Klaviyo: set KLAVIYO_API_KEY (read scopes) and use --mode dry_run.")
    return client_from_env(mode=mode, outbox_path=getattr(args, "outbox", None))


def _inputs(args):
    from .pipeline import Inputs

    return Inputs(customers=args.customers, orders=args.orders, activations=args.activations, usage=args.usage,
                  entitlements=args.entitlements, signals=args.signals, from_klaviyo=args.from_klaviyo,
                  usage_as_of=_now(args.usage_as_of) if args.usage_as_of else None)


def _policy(args):
    return replace(DEFAULT_POLICY, ranking=getattr(args, "ranking", "v1") or "v1")


def _add_common(p: argparse.ArgumentParser, outbox: bool = True) -> None:
    p.add_argument("--db", default="nba.db")
    p.add_argument("--as-of", help="ISO timestamp; default now")
    p.add_argument("--mode", choices=["offline", "dry_run", "live"])
    if outbox:
        p.add_argument("--outbox", default="outbox/klaviyo_outbox.jsonl")


def _add_run_inputs(p: argparse.ArgumentParser) -> None:
    p.add_argument("--customers", help="builder customers.csv")
    p.add_argument("--orders")
    p.add_argument("--activations")
    p.add_argument("--usage", help="daily usage CSV from the warehouse (not in Klaviyo)")
    p.add_argument("--entitlements")
    p.add_argument("--from-klaviyo", action="store_true",
                   help="read orders, activations and plan end dates from Klaviyo events instead of CSVs")
    p.add_argument("--signals", help="warehouse signals CSV (care, paid share, plan type, ...)")
    p.add_argument("--usage-as-of", help="when the usage feed was loaded")
    p.add_argument("--ranking", choices=["v1", "uplift", "learner"], default="v1")
    _add_common(p)


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, default=str))


# ------------------------------------------------------------- decisions


def _write_decisions_csv(path: str, decisions) -> None:
    import csv

    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["decision_id", "customer_id", "trip_phase", "outcome", "action", "channel", "reason", "arm",
                    "held", "send_at", "policy_mode", "propensity", "explored", "sentence"])
        for d in decisions:
            w.writerow([d.decision_id, d.customer_id, d.trip_phase, d.outcome, d.action or "", d.channel or "",
                        d.reason or "", d.arm, d.held, d.send_at.isoformat() if d.send_at else "", d.policy_mode,
                        "" if d.propensity is None else round(d.propensity, 4), d.explored, d.sentence])


def cmd_run(args, release: bool = False) -> int:
    from .klaviyo.signals_sync import derive_signals
    from .pipeline import load_active_model, load_snapshots
    from .run import release_held, run_hour

    now = _now(args.as_of)
    client = _client(args, need_reads=args.from_klaviyo)
    store = Store(args.db)
    snaps, base, declared, info = load_snapshots(_inputs(args), client, now)
    signals = derive_signals(store, now, base, customers=snaps.keys())
    policy = _policy(args)
    model = load_active_model(store, now) if policy.ranking == "learner" else None
    fn = release_held if release else run_hour
    res = fn(now, snaps, signals, store, client, policy, declared=declared, model=model)
    store.close()
    if args.decisions_out:
        _write_decisions_csv(args.decisions_out, res.decisions)
    out = res.summary()
    out.update(info)
    out["klaviyo_mode"] = client.mode
    out["outbox"] = os.path.abspath(args.outbox) if client.mode != "live" else None
    out["klaviyo_reads"] = client.requests_sent
    out["model_version"] = model.version if model else None
    _print(out)
    return 0


def cmd_explain(args) -> int:
    from .actions import REASON_TEXT

    store = Store(args.db)
    rows = [r for r in store.decisions() if r["customer_id"] == args.customer][-args.last:]
    if not rows:
        print(f"No decisions logged for {args.customer}.")
        return 1
    for r in rows:
        print(f"{r['decided_at']}  {r['decision_id']}  [{r['status']}]  arm={r['arm']}  policy={r['policy_version']}"
              f"  mode={r['policy_mode'] or 'rules'}")
        print(f"  {r['sentence']}")
        if r["reason"]:
            print(f"  reason: {r['reason']} - {REASON_TEXT.get(r['reason'], '')}")
        for c in json.loads(r["candidates"]):
            sc = f"  S(a)={c['score']['total']:+.2f}" if c.get("score") else ""
            why = f"{c['why']} - {REASON_TEXT.get(c['why'], '')}" if c.get("why") else ""
            print(f"    {c['id']:<3} {c['status']:<9} {c.get('channel') or '':<8}{sc}  {why}")
        if r["options"] and r["policy_mode"] == "learner":
            print(f"  learner {r['model_version']} cell={r['cell']} propensity={r['propensity']:.3f}"
                  f"{' (explored)' if r['explored'] else ''}")
            for o in json.loads(r["options"]):
                print(f"    {o['action']}:{o['channel']:<8} mean {o['mean']:+.2f} sd {o['sd']:.2f}  p={o['propensity']:.3f}")
        cap = json.loads(r["cap_state"])
        print(f"  cap: {cap['used']} of {cap['cap']} used in 30 days; send_at={r['send_at'] or '-'}")
    store.close()
    return 0


# --------------------------------------------------------------- klaviyo


def cmd_verify(args) -> int:
    from .klaviyo.verify import format_report, verify_account

    rep = verify_account(_client(args, need_reads=True))
    print(format_report(rep))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(rep, fh, indent=2)
    return 0 if rep["ok"] else 1


def cmd_sync(args) -> int:
    from .klaviyo.outcomes import pull_message_events
    from .klaviyo.signals_sync import pull_customer_events, pull_profile_signals

    client = _client(args, need_reads=True)
    store, now = Store(args.db), _now(args.as_of)
    lb = timedelta(days=args.lookback_days)
    out = {
        "profiles": pull_profile_signals(client, store, now, lb, args.max_pages),
        "customer_events": pull_customer_events(client, store, now, lookback=lb, max_pages=args.max_pages),
        "message_events": pull_message_events(client, store, now, lb, dispatcher_flow_id=args.dispatcher_flow_id,
                                              max_pages=args.max_pages),
        "reads": client.requests_sent,
    }
    store.close()
    _print(out)
    return 0


def cmd_pull(args) -> int:
    from .klaviyo.outcomes import pull_message_events

    client = _client(args, need_reads=True)
    store = Store(args.db)
    n = pull_message_events(client, store, _now(args.as_of), timedelta(days=args.lookback_days),
                            dispatcher_flow_id=args.dispatcher_flow_id, max_pages=args.max_pages)
    store.close()
    _print({"new_events": n, "reads": client.requests_sent})
    return 0


# -------------------------------------------------------------- learning


def cmd_attribute(args) -> int:
    from .rewards import attribute

    store = Store(args.db)
    _print(attribute(store, _now(args.as_of), dispatch_modes=None if args.all_modes else ("live",)))
    store.close()
    return 0


def cmd_uplift(args) -> int:
    from .rewards import uplift_report

    store = Store(args.db)
    rows = uplift_report(store, min_control_outcomes=args.min_control_outcomes)
    store.close()
    print(f"{'action':<6} {'treated':>8} {'control':>8} {'rate T':>8} {'rate C':>8} {'uplift':>8} {'95% CI':>18} {'EUR/conv':>9}  status")
    for r in rows:
        print(f"{r.action:<6} {r.n_treated:>8} {r.n_control:>8} {r.rate_treated:>8.2%} {r.rate_control:>8.2%} "
              f"{r.uplift:>+8.2%} [{r.low:+.2%}, {r.high:+.2%}] {r.revenue_per_conversion:>9.2f}  {r.status}")
    return 0


def cmd_train(args) -> int:
    from .learner import train

    store, now = Store(args.db), _now(args.as_of)
    rows = [dict(r) for r in store.rewards(now - timedelta(days=args.days))]
    m = train(rows, now, epsilon=args.epsilon)
    store.save_model(m.version, now, m.n_rows, m.to_params(), activate=args.activate)
    store.close()
    _print({"version": m.version, "rows": m.n_rows, "arms": len(m.stats), "active": args.activate})
    return 0


def cmd_evaluate(args) -> int:
    from .evaluate import first_listed, greedy, ips, logged_rows
    from .learner import Model

    store = Store(args.db)
    rows = logged_rows(store)
    out = [ips(rows, first_listed(), "rules").as_dict()]
    for r in store.db.execute("SELECT * FROM models ORDER BY trained_at DESC LIMIT ?", (args.last,)):
        m = Model.from_params(json.loads(r["params"]), datetime.fromisoformat(r["trained_at"]))
        res = ips(rows, greedy(m), m.version).as_dict()
        res["active"] = bool(r["active"])
        out.append(res)
    store.close()
    _print(out)
    return 0


def cmd_models(args) -> int:
    store = Store(args.db)
    if args.activate:
        store.activate_model(args.activate)
    rows = [{"version": r["version"], "trained_at": r["trained_at"], "rows": r["n_rows"], "active": bool(r["active"])}
            for r in store.db.execute("SELECT * FROM models ORDER BY trained_at DESC")]
    store.close()
    _print(rows)
    return 0


def cmd_guardrails(args) -> int:
    from .guardrails import enforce, evaluate, resume

    store, now = Store(args.db), _now(args.as_of)
    if args.resume:
        resume(store, args.resume, now)
    modes = None if args.all_modes else ("live",)
    out = enforce(store, now, modes) if args.enforce else evaluate(store, now, modes)
    store.close()
    _print(out)
    return 0


# ------------------------------------------------------------ operations


def cmd_hourly(args) -> int:
    from .pipeline import hourly

    client = _client(args, need_reads=args.from_klaviyo)
    store = Store(args.db)
    out = hourly(_now(args.as_of), store, client, _inputs(args), _policy(args))
    store.close()
    _print(out)
    return 0 if out.get("status") in ("ok", "skipped") else 1


def cmd_nightly(args) -> int:
    from .pipeline import nightly

    client = _client(args)
    store = Store(args.db)
    out = nightly(_now(args.as_of), store, client, report_dir=args.report_dir,
                  dispatch_modes=None if args.all_modes else ("live",))
    store.close()
    _print(out)
    return 0


def cmd_serve(args) -> int:
    from .server import serve

    policy = _policy(args)
    srv = serve(args.db, lambda: _client(args), host=args.host, port=args.port, policy=policy)
    print(f"nba trigger endpoint on http://{args.host}:{args.port} (POST /v1/trigger, GET /healthz)", file=sys.stderr)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


def cmd_jobs(args) -> int:
    store = Store(args.db)
    rows = [{"run_id": r["run_id"], "job": r["job"], "status": r["status"], "started_at": r["started_at"],
             "finished_at": r["finished_at"], "error": (r["error"] or "")[-300:] or None}
            for r in store.job_runs(limit=args.last)]
    store.close()
    _print(rows)
    return 0


def cmd_simulate(args) -> int:
    from .simulate import simulate

    out = [simulate(days=args.days, per_day=args.per_day, mode=m, seed=args.seed) for m in ("rules", "learner")]
    for r in out:
        r.pop("uplift", None)
    _print(out)
    return 0


def cmd_setup(args) -> int:
    from .klaviyo.setup import setup_markdown

    md = setup_markdown()
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(md)
        print(f"wrote {args.out}")
    else:
        print(md)
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="nba", description="Holafly Trip Decision Service")
    sub = p.add_subparsers(dest="cmd", required=True)

    for name, helptext in (("run", "one hourly decision run"),
                           ("release-held", "re-validate held decisions whose window has opened")):
        sp = sub.add_parser(name, help=helptext)
        _add_run_inputs(sp)
        sp.add_argument("--decisions-out", help="write this run's decisions to CSV")

    e = sub.add_parser("explain", help="show a customer's recent decisions")
    e.add_argument("--db", default="nba.db")
    e.add_argument("--customer", required=True)
    e.add_argument("--last", type=int, default=5)

    v = sub.add_parser("verify-klaviyo", help="read-only checks against the live account")
    v.add_argument("--out")
    v.add_argument("--mode", choices=["dry_run", "live"])

    for name in ("sync", "pull-outcomes"):
        sp = sub.add_parser(name)
        _add_common(sp, outbox=False)
        sp.add_argument("--lookback-days", type=int, default=60)
        sp.add_argument("--dispatcher-flow-id")
        sp.add_argument("--max-pages", type=int)

    a = sub.add_parser("attribute", help="rewards for closed decision windows")
    _add_common(a, outbox=False)
    a.add_argument("--all-modes", action="store_true", help="include dry-run/offline sends (testing only)")
    u = sub.add_parser("uplift", help="uplift table from action-level controls")
    u.add_argument("--db", default="nba.db")
    u.add_argument("--min-control-outcomes", type=int, default=100)
    t = sub.add_parser("train", help="fit a model from rewards")
    _add_common(t, outbox=False)
    t.add_argument("--days", type=int, default=180)
    t.add_argument("--epsilon", type=float, default=0.10)
    t.add_argument("--activate", action="store_true")
    ev = sub.add_parser("evaluate", help="off-policy comparison of rules and recent models")
    ev.add_argument("--db", default="nba.db")
    ev.add_argument("--last", type=int, default=3)
    m = sub.add_parser("models", help="list models; --activate VERSION")
    m.add_argument("--db", default="nba.db")
    m.add_argument("--activate")
    g = sub.add_parser("guardrails", help="unsubscribe and spam guardrails")
    _add_common(g, outbox=False)
    g.add_argument("--enforce", action="store_true", help="pause actions over the limits")
    g.add_argument("--resume", help="operator: re-enable a paused action")
    g.add_argument("--all-modes", action="store_true", help="include dry-run/offline sends (testing only)")

    h = sub.add_parser("hourly", help="sync + build + decide, locked and logged")
    _add_run_inputs(h)
    n = sub.add_parser("nightly", help="attribute, uplift, train, gate, guardrails, report")
    _add_common(n)
    n.add_argument("--report-dir", default="reports")
    n.add_argument("--all-modes", action="store_true", help="learn from dry-run/offline sends too (testing only)")
    sv = sub.add_parser("serve", help="real-time trigger endpoint")
    _add_common(sv)
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8080)
    sv.add_argument("--ranking", choices=["v1", "uplift", "learner"], default="v1")
    j = sub.add_parser("jobs", help="recent job runs")
    j.add_argument("--db", default="nba.db")
    j.add_argument("--last", type=int, default=10)
    sim = sub.add_parser("simulate", help="synthetic end-to-end learning loop: rules vs learner")
    sim.add_argument("--days", type=int, default=42)
    sim.add_argument("--per-day", type=int, default=200)
    sim.add_argument("--seed", type=int, default=11)
    s = sub.add_parser("setup-spec", help="Klaviyo build spec")
    s.add_argument("--out")

    args = p.parse_args(argv)
    handlers = {
        "run": cmd_run, "release-held": lambda a_: cmd_run(a_, release=True), "explain": cmd_explain,
        "verify-klaviyo": cmd_verify, "sync": cmd_sync, "pull-outcomes": cmd_pull, "attribute": cmd_attribute,
        "uplift": cmd_uplift, "train": cmd_train, "evaluate": cmd_evaluate, "models": cmd_models,
        "guardrails": cmd_guardrails, "hourly": cmd_hourly, "nightly": cmd_nightly, "serve": cmd_serve,
        "jobs": cmd_jobs, "setup-spec": cmd_setup, "simulate": cmd_simulate,
    }
    return handlers[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
