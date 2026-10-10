"""Read-only checks against the live Klaviyo account.

Every call here is a GET. The report holds names, ids, counts and property
keys only: no profile attributes, no event values, so it can be committed.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, List

from .client import KlaviyoClient
from .registry import (
    ACCOUNT_ID,
    ALL_METRICS,
    CAPTURE_FLOWS,
    DISPATCHER_FLOW_NAME,
    KNOWN_MISSING,
    NBA_METRIC_NAME,
    OVERLAPPING_FLOWS,
    REQUIRED_METRICS,
    TRIP_METRICS,
)

PAYLOAD_SAMPLE_METRICS = (
    "activation_event", "expiration_date_eSIM_v2", "entitlement_end_date_event",
    "hly_always_on_consumption_threshold",
)
# hly_order_confirmed is deliberately not sampled: its payload carries names,
# addresses and phone numbers, and the service only needs order_number,
# items and destination_localized from it.


def _check(name: str, ok: bool, detail: str, severity: str = "error") -> Dict[str, Any]:
    return {"check": name, "ok": ok, "severity": "ok" if ok else severity, "detail": detail}


def verify_account(client: KlaviyoClient, sample_profiles: int = 100) -> Dict[str, Any]:
    checks: List[Dict[str, Any]] = []
    report: Dict[str, Any] = {"verified_at": datetime.now(timezone.utc).isoformat(), "revision": client.revision}

    # Account
    acc = (client.get("/api/accounts").get("data") or [{}])[0]
    info = (acc.get("attributes") or {}).get("contact_information") or {}
    report["account"] = {
        "id": acc.get("id"),
        "organization_name": info.get("organization_name"),
        "test_account": (acc.get("attributes") or {}).get("test_account"),
    }
    checks.append(_check("account", acc.get("id") == ACCOUNT_ID,
                         f"connected to {acc.get('id')}, expected {ACCOUNT_ID}"))

    # Metrics, by name
    found: Dict[str, str] = {}
    for rec, _ in client.iter_records("/api/metrics", {"fields[metric]": "name"}):
        found[(rec.get("attributes") or {}).get("name")] = rec["id"]
    report["metric_count"] = len(found)
    missing, moved = [], []
    for name, mid in ALL_METRICS.items():
        if name not in found:
            missing.append(name)
        elif found[name] != mid:
            moved.append(f"{name}: {mid} -> {found[name]}")
    req_missing = [m for m in missing if m in REQUIRED_METRICS]
    checks.append(_check("required metrics", not req_missing and not moved,
                         f"{len(REQUIRED_METRICS) - len(req_missing)}/{len(REQUIRED_METRICS)} present"
                         + (f"; missing {req_missing}" if req_missing else "")
                         + (f"; id changed {moved}" if moved else "")))
    if missing and not req_missing:
        checks.append(_check("optional metrics", False, f"missing {missing}", "warning"))
    checks.append(_check("NBA Decision metric", NBA_METRIC_NAME in found,
                         "exists" if NBA_METRIC_NAME in found else KNOWN_MISSING[NBA_METRIC_NAME], "info"))
    report["metrics"] = {n: found.get(n) for n in ALL_METRICS}

    # Flows that overlap NBA actions
    flows: Dict[str, Dict[str, Any]] = {}
    for rec, _ in client.iter_records("/api/flows", {"fields[flow]": "name,status,trigger_type"}):
        flows[rec["id"]] = rec.get("attributes") or {}
    overlap = []
    for fid, (name, owner) in OVERLAPPING_FLOWS.items():
        f = flows.get(fid)
        overlap.append({"id": fid, "name": name, "nba_owner": owner, "status": f.get("status") if f else "not found"})
    live_overlap = [o for o in overlap if o["status"] == "live" and o["nba_owner"] != "review"]
    checks.append(_check("overlapping live flows", not live_overlap,
                         f"{len(live_overlap)} live flows overlap NBA actions; pause or hand over in Phase 0", "warning"))
    capture = [{"id": fid, "name": n, "status": (flows.get(fid) or {}).get("status", "not found")}
               for fid, n in CAPTURE_FLOWS.items()]
    report["overlapping_flows"] = overlap
    report["capture_flows"] = capture
    dispatcher = [fid for fid, f in flows.items() if f.get("name") == DISPATCHER_FLOW_NAME]
    report["dispatcher_flow_id"] = dispatcher[0] if dispatcher else None
    checks.append(_check("NBA Dispatcher flow", bool(dispatcher),
                         dispatcher[0] if dispatcher else "not built yet (see setup spec)", "info"))

    # Profile identity: can the service address profiles?
    page = client.get("/api/profiles", {
        "fields[profile]": "external_id,locale,location.timezone",
        "page[size]": str(min(100, sample_profiles)), "sort": "-updated",
    })
    profs = page.get("data") or []
    n = len(profs)
    ext = sum(1 for p in profs if (p.get("attributes") or {}).get("external_id"))
    tz = sum(1 for p in profs if ((p.get("attributes") or {}).get("location") or {}).get("timezone"))
    loc = sum(1 for p in profs if (p.get("attributes") or {}).get("locale"))
    report["profile_sample"] = {"profiles": n, "with_external_id": ext, "with_timezone": tz, "with_locale": loc}
    checks.append(_check("profile identity", n > 0 and ext / max(n, 1) >= 0.9,
                         f"external_id on {ext}/{n} recent profiles; address profiles by Klaviyo id", "warning"))

    # Payload keys of the events the builder reads (keys only)
    keys: Dict[str, List[str]] = {}
    for name in PAYLOAD_SAMPLE_METRICS:
        mid = found.get(name) or TRIP_METRICS.get(name)
        if not mid:
            continue
        body = client.get("/api/events", {
            "filter": f'equals(metric_id,"{mid}")', "fields[event]": "event_properties",
            "sort": "-datetime", "page[size]": "1",
        })
        data = body.get("data") or []
        keys[name] = sorted(((data[0].get("attributes") or {}).get("event_properties") or {}).keys()) if data else []
    report["payload_keys"] = keys
    act = keys.get("activation_event", [])
    checks.append(_check("activation order key", "order_id" in act,
                         "activation_event carries order_id" if "order_id" in act else "no order_id on activation_event"))
    checks.append(_check("esim id", "esim_id" in act,
                         "esim_id present" if "esim_id" in act else KNOWN_MISSING["esim_id on activation_event"], "warning"))
    exp = keys.get("expiration_date_eSIM_v2", [])
    checks.append(_check("plan end date", "expiration_date_eSIM_v2" in exp,
                         "expiration_date_eSIM_v2 gives plan_end_at per order" if exp else "no expiration event found"))

    report["checks"] = checks
    report["known_gaps"] = KNOWN_MISSING
    report["ok"] = all(c["ok"] or c["severity"] != "error" for c in checks)
    return report


def format_report(r: Dict[str, Any]) -> str:
    lines = [f"Klaviyo account {r['account']['id']} ({r['account'].get('organization_name')}), "
             f"test_account={r['account'].get('test_account')}, revision {r['revision']}"]
    mark = {"ok": "PASS", "error": "FAIL", "warning": "WARN", "info": "INFO"}
    for c in r["checks"]:
        lines.append(f"  [{mark[c['severity']]}] {c['check']}: {c['detail']}")
    live = [o for o in r.get("overlapping_flows", []) if o["status"] == "live"]
    if live:
        lines.append("  Live flows overlapping NBA actions:")
        for o in live:
            lines.append(f"    {o['id']}  {o['name']}  -> {o['nba_owner']}")
    return "\n".join(lines)
