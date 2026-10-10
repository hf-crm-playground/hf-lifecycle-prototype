"""Real-time trigger endpoint, for moments an hourly run is too slow for
(cart abandonment, low-data alerts, install started and stalled).

    POST /v1/trigger      {"customer_id": "...", "event": {"metric": "Added to Cart",
                           "id": "...", "time": "2026-10-10T12:00:00Z"}}
    GET  /healthz         last job runs and the active model

A Klaviyo flow can call it with a webhook action on the trigger metric. The
endpoint records the event (whitelisted metrics only, no properties), then
decides for that one customer from their last hourly snapshot and current
signals, using the same policy, store, Klaviyo client and model as the
hourly job. All the usual rules apply: cap, recency, holdout, control,
windows. Requests must carry `X-NBA-Token` equal to NBA_TRIGGER_TOKEN.

Standard library only (ThreadingHTTPServer). Each request opens its own
SQLite connection. Run behind a TLS-terminating proxy.
"""

from __future__ import annotations

import hmac
import json
import os
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, Optional, Tuple

from .config import DEFAULT_POLICY, PolicyConfig
from .klaviyo.client import KlaviyoClient
from .klaviyo.signals_sync import EVENT_WHITELIST, derive_signals
from .pipeline import load_active_model
from .run import run_hour
from .store import Store

MAX_BODY = 16 * 1024


def handle_trigger(store: Store, client: KlaviyoClient, payload: Dict[str, Any], now: datetime,
                   policy: PolicyConfig = DEFAULT_POLICY) -> Tuple[int, Dict[str, Any]]:
    if not isinstance(payload, dict):
        return 400, {"error": "body must be a JSON object"}
    cid = payload.get("customer_id") or payload.get("profile_id")
    if not cid or not isinstance(cid, str) or len(cid) > 128:
        return 400, {"error": "customer_id or profile_id required"}
    ev = payload.get("event") or {}
    if not isinstance(ev, dict):
        return 400, {"error": "event must be an object"}
    if ev:
        metric = ev.get("metric")
        if not isinstance(metric, str) or metric not in EVENT_WHITELIST:
            return 400, {"error": "metric not accepted"}
        try:
            at = datetime.fromisoformat(str(ev.get("time") or now.isoformat()).replace("Z", "+00:00"))
        except ValueError:
            return 400, {"error": "bad event time"}
        if at.tzinfo is None:
            return 400, {"error": "event time needs a timezone"}
        if at > now + timedelta(minutes=5):
            return 400, {"error": "event time is in the future"}
        store.add_customer_events([{
            "event_id": str(ev.get("id") or f"rt-{cid}-{metric}-{at.isoformat()}"), "customer_id": cid,
            "profile_id": payload.get("profile_id"), "metric": metric, "occurred_at": at, "value": None, "attrs": {},
        }])
    snap, declared = store.load_snapshot(cid)
    if snap is None:
        return 202, {"status": "recorded", "decided": False, "why": "no trip snapshot yet; next hourly run decides"}
    base = store.load_signals(cid)
    signals = derive_signals(store, now, base={cid: base} if base else None, customers=[cid])
    model = load_active_model(store, now) if policy.ranking == "learner" else None
    res = run_hour(now, {cid: snap}, signals, store, client, policy, declared={cid: declared} if declared else None,
                   only={cid}, sync_trip_properties=False, model=model, save_snapshots=False, id_source="rt")
    d = res.decisions[0] if res.decisions else None
    return 200, {
        "status": "decided" if d else "no_decision_point",
        "decision_id": d.decision_id if d else None,
        "outcome": d.outcome if d else None,
        "action": d.action if d else None,
        "channel": d.channel if d else None,
        "reason": d.reason if d else None,
        "held": d.held if d else None,
    }


def make_handler(db_path: str, client_factory: Callable[[], KlaviyoClient], token: str,
                 policy: PolicyConfig = DEFAULT_POLICY, clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
    class Handler(BaseHTTPRequestHandler):
        server_version = "nba-trigger/0.1"

        def _send(self, code: int, body: Dict[str, Any]) -> None:
            raw = json.dumps(body, default=str).encode()
            self.send_response(code)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, fmt, *args):  # no request lines with ids in logs
            pass

        def do_GET(self):
            if self.path != "/healthz":
                return self._send(404, {"error": "not found"})
            store = Store(db_path)
            try:
                jobs = [{"job": r["job"], "status": r["status"], "started_at": r["started_at"]} for r in store.job_runs(limit=5)]
                m = store.active_model()
                return self._send(200, {"ok": True, "jobs": jobs, "model": m["version"] if m else "prior"})
            finally:
                store.close()

        def do_POST(self):
            if self.path != "/v1/trigger":
                return self._send(404, {"error": "not found"})
            given = self.headers.get("X-NBA-Token", "").encode("utf-8", "surrogateescape")
            if not hmac.compare_digest(given, token.encode("utf-8")):
                return self._send(401, {"error": "unauthorised"})
            n = int(self.headers.get("content-length") or 0)
            if n <= 0 or n > MAX_BODY:
                return self._send(413 if n > MAX_BODY else 400, {"error": "body size"})
            try:
                payload = json.loads(self.rfile.read(n))
            except ValueError:
                return self._send(400, {"error": "invalid JSON"})
            store = Store(db_path)
            try:
                code, body = handle_trigger(store, client_factory(), payload, clock(), policy)
            finally:
                store.close()
            return self._send(code, body)

    return Handler


def serve(db_path: str, client_factory: Callable[[], KlaviyoClient], host: str = "127.0.0.1", port: int = 8080,
          policy: PolicyConfig = DEFAULT_POLICY, token: Optional[str] = None) -> ThreadingHTTPServer:
    token = token or os.environ.get("NBA_TRIGGER_TOKEN")
    if not token or len(token) < 16:
        raise SystemExit("Set NBA_TRIGGER_TOKEN (16+ characters) before serving.")
    return ThreadingHTTPServer((host, port), make_handler(db_path, client_factory, token, policy))
