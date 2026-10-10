import json
import os
import sys
from dataclasses import replace
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from nba.assignment import in_action_control, in_global_holdout  # noqa: E402
from nba.config import DEFAULT_POLICY  # noqa: E402
from nba.context import ContactHistory, CustomerSignals, DecisionContext  # noqa: E402

NOW = datetime(2026, 10, 9, 10, 0, tzinfo=timezone.utc)  # 10:00 UTC = 12:00 Madrid
EXAMPLES = os.path.join(os.path.dirname(__file__), "..", "..", "examples")


def customer_id(holdout=False, control_for=None, trip_id="trip-1", start=0):
    """First id 'c<n>' with the requested assignment, so tests do not depend
    on luck with the hash."""
    p = DEFAULT_POLICY
    n = start
    while True:
        cid = f"c{n}"
        h = in_global_holdout(cid, p.global_holdout, p.holdout_salt)
        if h == holdout:
            if control_for is None:
                if not any(in_action_control(cid, a, trip_id, p.action_control) for a in ALL_IDS):
                    return cid
            elif in_action_control(cid, control_for, trip_id, p.action_control):
                return cid
        n += 1


ALL_IDS = ["I1", "I2", "I3", "P1", "I6", "B1", "I4", "I5", "C1", "C2", "T1", "T2", "T3", "P2", "P3", "I7", "T4", "B2"]


def ctx(cid=None, signals=None, history=None, **kw) -> DecisionContext:
    """An in-trip customer on day 9 with fresh usage, high confidence, email
    and push reachable, and nothing due unless the test sets it."""
    cid = cid or customer_id()
    s = CustomerSignals(
        customer_id=cid, market="ES", locale="es-ES", home_timezone="Europe/Madrid",
        consent_email=True, app_installed=True, push_token=True, in_app_session_14d=True,
        app_identity="linked",
    )
    if signals:
        s = replace(s, **signals)
    h = history or ContactHistory()
    base = dict(
        customer_id=cid, decided_at=NOW, phase="in_trip", trip_confidence="high", trip_id="trip-1",
        destination="ES", market="ES", locale="es-ES", local_tz="Europe/Madrid", tz_source="destination",
        days_in_trip=9, days_since_last_usage=0, days_after_confirm=None, days_since_trip_end=None,
        data_remaining_pct=60.0, runout_before_plan_end=False, projected_runout_days=None,
        plan_end_hours=200.0, plan_ended_usage_stopped=False, usage_fresh=True, usage_last_24h=True,
        pending_activation=False, declared_departure_days=None, network_outside_plan=False,
        s=s, h=h,
    )
    base.update(kw)
    return DecisionContext(**base)


def history(**kw) -> ContactHistory:
    h = ContactHistory()
    sends = kw.pop("sends", None)
    if sends:
        h.sends_30d = {"total": 0, "email": 0, "push": 0, "whatsapp": 0, **sends}
    for k, v in kw.items():
        setattr(h, k, v)
    return h


class FakeTransport:
    """Scripted Klaviyo: a list of (status, headers, body) per call, or a
    function of (method, url) returning one."""

    def __init__(self, responses=None, route=None):
        self.responses = list(responses or [])
        self.route = route
        self.calls = []

    def __call__(self, method, url, headers, data, timeout):
        self.calls.append({"method": method, "url": url, "headers": headers,
                           "body": json.loads(data) if data else None})
        if self.route:
            status, hdrs, body = self.route(method, url)
        else:
            status, hdrs, body = self.responses.pop(0)
        raw = json.dumps(body).encode() if body is not None else b""
        return status, hdrs, raw
