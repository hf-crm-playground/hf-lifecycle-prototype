"""Command-line runner: CSV in, CSV/JSON out.

Example:
    python -m trip_state.cli \
        --customers examples/customers.csv \
        --orders examples/orders.csv \
        --activations examples/activations.csv \
        --usage examples/usage_daily.csv \
        --entitlements examples/entitlements.csv \
        --as-of 2026-09-23T10:00:00Z \
        --usage-as-of 2026-09-23T06:00:00Z \
        --snapshots-out snapshots.csv --trips-out trips.csv \
        --klaviyo-out klaviyo_profiles.jsonl

Column names match the dataclass fields in models.py. Timestamps are ISO
8601 (a trailing Z or an offset; naive values are read as UTC). Days are
YYYY-MM-DD. Empty cells are None.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import asdict, fields
from datetime import date, datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Type, TypeVar

from .builder import build_many
from .config import TripConfig
from .klaviyo import to_profile_import_body
from .models import Activation, Customer, Entitlement, Order, UsageDay

T = TypeVar("T")


def _parse_dt(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _parse_day(s: Optional[str]) -> Optional[date]:
    return date.fromisoformat(s.strip()) if s and s.strip() else None


def _parse_float(s: Optional[str]) -> Optional[float]:
    return float(s) if s not in (None, "") else None


def _parse_int(s: Optional[str]) -> Optional[int]:
    return int(float(s)) if s not in (None, "") else None


_PARSERS = {
    "ordered_at": _parse_dt, "activated_at": _parse_dt, "plan_end_at": _parse_dt,
    "day": _parse_day, "declared_travel_start": _parse_day,
    "mb_used": _parse_float, "mb_remaining": _parse_float, "plan_mb": _parse_float,
    "plan_days": _parse_int,
}


def read_csv(path: Optional[str], cls: Type[T]) -> List[T]:
    if not path:
        return []
    names = {f.name for f in fields(cls)}
    out: List[T] = []
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            kwargs: Dict[str, Any] = {}
            for k, v in row.items():
                if k not in names:
                    continue
                if k in _PARSERS:
                    kwargs[k] = _PARSERS[k](v)
                else:
                    kwargs[k] = v if v not in (None, "") else None
            # Required plain-string fields must not be None.
            for k in ("customer_id", "esim_id", "order_id", "destination"):
                if k in names and kwargs.get(k) is None:
                    kwargs[k] = ""
            if "status" in names and kwargs.get("status") is None:
                kwargs["status"] = "confirmed"
            if "plan_type" in names and kwargs.get("plan_type") is None:
                kwargs["plan_type"] = "finite"
            if "purchase_channel" in names and kwargs.get("purchase_channel") is None:
                kwargs["purchase_channel"] = "web"
            out.append(cls(**kwargs))
    return out


def _flatten(obj: Any) -> Dict[str, Any]:
    d = asdict(obj)
    for k, v in list(d.items()):
        if isinstance(v, (datetime, date)):
            d[k] = v.isoformat()
        elif isinstance(v, list):
            d[k] = "|".join(str(x) for x in v)
    return d


def write_csv(path: str, rows: Iterable[Dict[str, Any]]) -> None:
    rows = list(rows)
    if not rows:
        open(path, "w").close()
        return
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Holafly Trip State Builder")
    p.add_argument("--customers", required=True)
    p.add_argument("--orders", required=True)
    p.add_argument("--activations")
    p.add_argument("--usage")
    p.add_argument("--entitlements")
    p.add_argument("--as-of", help="ISO timestamp; default now (UTC)")
    p.add_argument("--usage-as-of", help="ISO timestamp the usage feed was loaded")
    p.add_argument("--snapshots-out", default="snapshots.csv")
    p.add_argument("--trips-out", default="trips.csv")
    p.add_argument("--klaviyo-out", help="JSONL of profile-import bodies")
    p.add_argument("--trip-gap-days", type=int, default=TripConfig.trip_gap_days)
    p.add_argument("--confirm-days", type=int, default=TripConfig.trip_end_confirm_days)
    args = p.parse_args(argv)

    as_of = _parse_dt(args.as_of) or datetime.now(timezone.utc)
    config = TripConfig(trip_gap_days=args.trip_gap_days,
                        trip_end_confirm_days=args.confirm_days)

    customers = read_csv(args.customers, Customer)
    results = build_many(
        customers,
        read_csv(args.orders, Order),
        read_csv(args.activations, Activation),
        read_csv(args.usage, UsageDay),
        read_csv(args.entitlements, Entitlement),
        as_of=as_of,
        usage_data_as_of=_parse_dt(args.usage_as_of),
        config=config,
    )

    snaps = [_flatten(s) for s, _ in results.values()]
    trips = [_flatten(t) for _, ts in results.values() for t in ts]
    write_csv(args.snapshots_out, snaps)
    write_csv(args.trips_out, trips)
    if args.klaviyo_out:
        with open(args.klaviyo_out, "w", encoding="utf-8") as fh:
            for cid, (s, _) in results.items():
                fh.write(json.dumps(to_profile_import_body(s, external_id=cid)) + "\n")

    counts: Dict[str, int] = {}
    for s, _ in results.values():
        counts[s.trip_state] = counts.get(s.trip_state, 0) + 1
    print(f"as_of={as_of.isoformat()} customers={len(results)} states={counts}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
