"""Command line for the Trip Decision Service.

  python -m nba verify-klaviyo [--out report.json]       read-only account checks
  python -m nba run      --customers ... --signals ...   one hourly run
  python -m nba release-held  (same inputs as run)       re-validate held decisions
  python -m nba pull-outcomes --db nba.db                message events -> cap ledger
  python -m nba explain  --db nba.db --customer ID       why a customer got what they got
  python -m nba setup-spec [--out file.md]               what to build in Klaviyo

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
from typing import Dict, List, Optional

from trip_state.builder import build_many
from trip_state.cli import read_csv
from trip_state.models import Activation, Customer, Entitlement, Order, UsageDay

from .config import DEFAULT_POLICY
from .context import CustomerSignals
from .io import read_signals_csv
from .klaviyo.client import KlaviyoClient, client_from_env
from .store import Store


def _now(s: Optional[str]) -> datetime:
    if not s:
        return datetime.now(timezone.utc)
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _client(args, need_reads: bool = False) -> KlaviyoClient:
    mode = args.mode or os.environ.get("NBA_KLAVIYO_MODE") or ("dry_run" if os.environ.get("KLAVIYO_API_KEY") else "offline")
    if need_reads and mode == "offline":
        sys.exit("This command reads from Klaviyo: set KLAVIYO_API_KEY (read scopes) and use --mode dry_run.")
    return client_from_env(mode=mode, outbox_path=getattr(args, "outbox", None))


def _add_run_inputs(p: argparse.ArgumentParser) -> None:
    p.add_argument("--customers", help="builder customers.csv")
    p.add_argument("--orders")
    p.add_argument("--activations")
    p.add_argument("--usage", help="daily usage CSV from the warehouse (not in Klaviyo)")
    p.add_argument("--entitlements")
    p.add_argument("--from-klaviyo", action="store_true",
                   help="read orders, activations and plan end dates from Klaviyo events instead of CSVs")
    p.add_argument("--signals", help="customer signals CSV (see examples/signals.csv)")
    p.add_argument("--as-of", help="ISO timestamp; default now")
    p.add_argument("--usage-as-of", help="when the usage feed was loaded")
    p.add_argument("--db", default="nba.db")
    p.add_argument("--outbox", default="outbox/klaviyo_outbox.jsonl")
    p.add_argument("--mode", choices=["offline", "dry_run", "live"])
    p.add_argument("--ranking", choices=["v1", "uplift"], default="v1")
    p.add_argument("--decisions-out", help="write this run's decisions to CSV")


def _snapshots(args, client: KlaviyoClient, now: datetime):
    signals = read_signals_csv(args.signals)
    if args.from_klaviyo:
        from .klaviyo.ingest import pull_trip_inputs

        ti = pull_trip_inputs(client, now)
        customers: List[Customer] = list(ti.customers.values())
        orders, acts, ents = list(ti.orders.values()), ti.activations, ti.entitlements
        for cid, pid in ti.profile_ids.items():
            s = signals.get(cid) or CustomerSignals(customer_id=cid)
            signals[cid] = replace(s, klaviyo_profile_id=s.klaviyo_profile_id or pid)
        for cid, at in ti.always_on_threshold.items():
            if now - at <= timedelta(hours=48):
                signals[cid] = replace(signals.get(cid) or CustomerSignals(customer_id=cid), always_on_threshold_event=True)
        if ti.unknown_destinations:
            print(f"unknown destination names: {dict(ti.unknown_destinations.most_common(10))}", file=sys.stderr)
    else:
        if not (args.customers and args.orders):
            sys.exit("Give --customers and --orders (builder CSVs), or --from-klaviyo.")
        customers = read_csv(args.customers, Customer)
        orders = read_csv(args.orders, Order)
        acts = read_csv(args.activations, Activation)
        ents = read_csv(args.entitlements, Entitlement)
    usage = read_csv(args.usage, UsageDay)
    built = build_many(customers, orders, acts, usage, ents, as_of=now,
                       usage_data_as_of=_now(args.usage_as_of) if args.usage_as_of else None)
    snaps = {cid: s for cid, (s, _) in built.items()}
    declared = {c.customer_id: c.declared_travel_start for c in customers if c.declared_travel_start}
    return snaps, signals, declared


def _write_decisions_csv(path: str, decisions) -> None:
    import csv

    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["decision_id", "customer_id", "trip_phase", "outcome", "action", "channel", "reason", "arm",
                    "held", "send_at", "sentence"])
        for d in decisions:
            w.writerow([d.decision_id, d.customer_id, d.trip_phase, d.outcome, d.action or "", d.channel or "",
                        d.reason or "", d.arm, d.held, d.send_at.isoformat() if d.send_at else "", d.sentence])


def cmd_run(args, release: bool = False) -> int:
    from .run import release_held, run_hour

    now = _now(args.as_of)
    client = _client(args, need_reads=args.from_klaviyo)
    snaps, signals, declared = _snapshots(args, client, now)
    policy = replace(DEFAULT_POLICY, ranking=args.ranking)
    store = Store(args.db)
    fn = release_held if release else run_hour
    res = fn(now, snaps, signals, store, client, policy, declared=declared)
    store.close()
    if args.decisions_out:
        _write_decisions_csv(args.decisions_out, res.decisions)
    out = res.summary()
    out["klaviyo_mode"] = client.mode
    out["outbox"] = os.path.abspath(args.outbox) if client.mode != "live" else None
    out["klaviyo_reads"] = client.requests_sent
    print(json.dumps(out, indent=2, default=str))
    return 0


def cmd_verify(args) -> int:
    from .klaviyo.verify import format_report, verify_account

    client = _client(args, need_reads=True)
    rep = verify_account(client)
    print(format_report(rep))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(rep, fh, indent=2)
    return 0 if rep["ok"] else 1


def cmd_pull(args) -> int:
    from .klaviyo.outcomes import pull_message_events

    client = _client(args, need_reads=True)
    store = Store(args.db)
    n = pull_message_events(client, store, _now(args.as_of), timedelta(days=args.lookback_days),
                            dispatcher_flow_id=args.dispatcher_flow_id, max_pages=args.max_pages)
    store.close()
    print(json.dumps({"new_events": n, "reads": client.requests_sent}, indent=2))
    return 0


def cmd_explain(args) -> int:
    from .actions import REASON_TEXT

    store = Store(args.db)
    rows = [r for r in store.decisions() if r["customer_id"] == args.customer][-args.last:]
    if not rows:
        print(f"No decisions logged for {args.customer}.")
        return 1
    for r in rows:
        print(f"{r['decided_at']}  {r['decision_id']}  [{r['status']}]  arm={r['arm']}  policy={r['policy_version']}")
        print(f"  {r['sentence']}")
        if r["reason"]:
            print(f"  reason: {r['reason']} - {REASON_TEXT.get(r['reason'], '')}")
        for c in json.loads(r["candidates"]):
            sc = f"  S(a)={c['score']['total']:+.2f}" if c.get("score") else ""
            why = f"{c['why']} - {REASON_TEXT.get(c['why'], '')}" if c.get("why") else ""
            print(f"    {c['id']:<3} {c['status']:<9} {c.get('channel') or '':<8}{sc}  {why}")
        cap = json.loads(r["cap_state"])
        print(f"  cap: {cap['used']} of {cap['cap']} used in 30 days; send_at={r['send_at'] or '-'}")
    store.close()
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

    r = sub.add_parser("run", help="one hourly decision run")
    _add_run_inputs(r)
    rh = sub.add_parser("release-held", help="re-validate held decisions whose window has opened")
    _add_run_inputs(rh)

    v = sub.add_parser("verify-klaviyo", help="read-only checks against the live account")
    v.add_argument("--out")
    v.add_argument("--mode", choices=["dry_run", "live"])

    po = sub.add_parser("pull-outcomes", help="message events -> cap ledger")
    po.add_argument("--db", default="nba.db")
    po.add_argument("--as-of")
    po.add_argument("--lookback-days", type=int, default=60)
    po.add_argument("--dispatcher-flow-id")
    po.add_argument("--max-pages", type=int)
    po.add_argument("--mode", choices=["dry_run", "live"])

    e = sub.add_parser("explain", help="show a customer's recent decisions")
    e.add_argument("--db", default="nba.db")
    e.add_argument("--customer", required=True)
    e.add_argument("--last", type=int, default=5)

    s = sub.add_parser("setup-spec", help="Klaviyo build spec (flows, segments, properties, Phase 0)")
    s.add_argument("--out")

    args = p.parse_args(argv)
    if args.cmd == "run":
        return cmd_run(args)
    if args.cmd == "release-held":
        return cmd_run(args, release=True)
    if args.cmd == "verify-klaviyo":
        return cmd_verify(args)
    if args.cmd == "pull-outcomes":
        return cmd_pull(args)
    if args.cmd == "explain":
        return cmd_explain(args)
    if args.cmd == "setup-spec":
        return cmd_setup(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
