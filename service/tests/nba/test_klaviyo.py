import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from helpers import NOW, FakeTransport, ctx

from nba.config import DEFAULT_POLICY
from nba.klaviyo.client import LIVE_ENV, KlaviyoClient, KlaviyoError, WriteBlocked
from nba.klaviyo.ingest import TripInputs, apply_event
from nba.klaviyo.outcomes import events_params, pull_message_events
from nba.klaviyo.payloads import bulk_import_bodies, nba_decision_event, nba_profile_properties
from nba.klaviyo.registry import MESSAGE_METRICS
from nba.klaviyo.setup import dispatcher_spec, setup_markdown
from nba.klaviyo.verify import verify_account
from nba.policy import decide
from nba.store import Store

LOW_DATA = dict(data_remaining_pct=15.0)


def client(transport=None, mode="dry_run", outbox=None, **kw):
    return KlaviyoClient(api_key="pk_test", mode=mode, outbox_path=outbox, transport=transport or FakeTransport([]),
                         sleep=lambda s: None, **kw)


class ClientModes(unittest.TestCase):
    def test_dry_run_writes_go_to_outbox_never_to_network(self):
        t = FakeTransport([])
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "out", "outbox.jsonl")
            c = client(t, outbox=path)
            r = c.create_event({"data": {"type": "event"}}, note="hello")
            self.assertEqual(r.status, 202)
            self.assertEqual(t.calls, [])
            with open(path) as fh:
                raw = fh.read()
            rec = json.loads(raw)
            self.assertEqual((rec["method"], rec["url"], rec["mode"]), ("POST", "https://a.klaviyo.com/api/events", "dry_run"))
            self.assertNotIn("Authorization", rec["headers"])
            self.assertNotIn("pk_test", raw)
            self.assertEqual(rec["headers"]["revision"], "2026-07-15")

    def test_dry_run_reads_are_live_with_auth_and_revision(self):
        t = FakeTransport([(200, {}, {"data": []})])
        client(t).get("/api/metrics")
        h = t.calls[0]["headers"]
        self.assertEqual(h["Authorization"], "Klaviyo-API-Key pk_test")
        self.assertEqual(h["revision"], "2026-07-15")

    def test_offline_blocks_reads(self):
        c = KlaviyoClient(mode="offline")
        with self.assertRaises(RuntimeError):
            c.get("/api/metrics")
        self.assertEqual(c.create_event({}).status, 202)

    def test_live_needs_env_and_allowlist(self):
        with mock.patch.dict(os.environ, {LIVE_ENV: ""}):
            with self.assertRaises(WriteBlocked):
                client(mode="live")
        with mock.patch.dict(os.environ, {LIVE_ENV: "yes"}):
            t = FakeTransport([(202, {}, None)])
            c = client(t, mode="live")
            c.create_event({"data": {}})
            self.assertEqual(t.calls[0]["method"], "POST")
            for path in ("/api/flows", "/api/campaigns", "/api/segments", "/api/lists/abc/relationships/profiles"):
                with self.assertRaises(WriteBlocked):
                    c.request("POST", path, body={})

    def test_429_honours_retry_after_then_succeeds(self):
        slept = []
        t = FakeTransport([(429, {"retry-after": "7"}, {"errors": []}), (200, {}, {"data": [1]})])
        c = KlaviyoClient(api_key="k", mode="dry_run", transport=t, sleep=slept.append)
        self.assertEqual(c.get("/api/events")["data"], [1])
        self.assertIn(7.0, slept)
        self.assertEqual(len(t.calls), 2)

    def test_5xx_backs_off_and_gives_up(self):
        t = FakeTransport([(503, {}, None)] * 3)
        c = client(t, max_retries=2)
        with self.assertRaises(KlaviyoError) as e:
            c.get("/api/metrics")
        self.assertEqual(e.exception.status, 503)
        self.assertEqual(len(t.calls), 3)

    def test_4xx_raises_with_detail(self):
        t = FakeTransport([(400, {}, {"errors": [{"title": "Invalid filter", "detail": "bad"}]})])
        with self.assertRaises(KlaviyoError) as e:
            client(t).get("/api/events")
        self.assertIn("Invalid filter", str(e.exception))

    def test_pagination_follows_next(self):
        nxt = "https://a.klaviyo.com/api/metrics?page%5Bcursor%5D=abc"
        t = FakeTransport([(200, {}, {"data": [{"id": "a"}], "links": {"next": nxt}}),
                           (200, {}, {"data": [{"id": "b"}], "links": {"next": None}})])
        ids = [r["id"] for r, _ in client(t).iter_records("/api/metrics")]
        self.assertEqual(ids, ["a", "b"])
        self.assertEqual(t.calls[1]["url"], nxt)

    def test_rate_limiter_waits_when_burst_spent(self):
        slept = []
        clock = [0.0]
        t = FakeTransport(route=lambda m, u: (200, {}, {"data": []}))
        c = KlaviyoClient(api_key="k", transport=t, sleep=lambda s: (slept.append(s), clock.__setitem__(0, clock[0] + s)),
                          clock=lambda: clock[0])
        for _ in range(12):  # default tier: burst 10
            c.get("/api/flows")
        self.assertTrue(slept)


class Payloads(unittest.TestCase):
    def test_nba_decision_event_shape(self):
        d = decide(ctx(**LOW_DATA))
        body = nba_decision_event(d, NOW, DEFAULT_POLICY)
        a = body["data"]["attributes"]
        self.assertEqual(a["unique_id"], d.decision_id)
        self.assertEqual(a["metric"]["data"]["attributes"]["name"], "NBA Decision")
        props = a["properties"]
        for k in ("decision_id", "action", "content_key", "channel", "trip_phase", "destination", "locale",
                  "reason_codes", "experiment_id", "arm", "expires_at"):
            self.assertIn(k, props)
        self.assertEqual((props["action"], props["content_key"], props["channel"]), ("I1", "topup_runout", "push"))
        self.assertEqual(props["expires_at"], "2026-10-10T10:00:00Z")

    def test_profile_addressed_by_klaviyo_id_without_writing_external_id(self):
        d = decide(ctx(**LOW_DATA))
        ref = nba_decision_event(d, NOW, DEFAULT_POLICY, profile_id="01ABC")["data"]["attributes"]["profile"]["data"]
        self.assertEqual(ref["id"], "01ABC")
        self.assertNotIn("external_id", ref["attributes"])

    def test_no_event_for_none_in_app_or_capture(self):
        self.assertIsNone(nba_decision_event(decide(ctx()), NOW, DEFAULT_POLICY))
        inapp = decide(ctx(signals={"success_moment": True}))
        self.assertEqual(inapp.channel, "in_app")
        self.assertIsNone(nba_decision_event(inapp, NOW, DEFAULT_POLICY))
        props = nba_profile_properties(inapp, NOW, DEFAULT_POLICY)
        self.assertEqual(props["nba_inapp_action"], "referral_intrip")
        self.assertEqual(props["nba_inapp_expires_at"], "2026-10-16T10:00:00Z")
        self.assertTrue(props["rating_prompt_allowed"])

    def test_profile_properties(self):
        d = decide(ctx(**LOW_DATA))
        p = nba_profile_properties(d, NOW, DEFAULT_POLICY)
        self.assertEqual(p["nba_holdout_group"], "treatment")
        self.assertEqual(p["nba_last_action"], "topup_runout")
        self.assertEqual((p["nba_sends_30d_total"], p["nba_sends_30d_push"]), (1, 1))
        self.assertLessEqual(len(p["nba_last_reason"]), 200)

    def test_bulk_import_chunks(self):
        rows = [(f"c{i}", {"trip_state": "in_trip"}, None) for i in range(25_001)]
        bodies = list(bulk_import_bodies(rows))
        self.assertEqual([len(b["data"]["attributes"]["profiles"]["data"]) for b in bodies], [10_000, 10_000, 5_001])
        self.assertEqual(bodies[0]["data"]["type"], "profile-bulk-import-job")


def _event(eid, pid, when, props, ext=None):
    rec = {"type": "event", "id": eid,
           "attributes": {"datetime": when, "event_properties": props},
           "relationships": {"profile": {"data": {"type": "profile", "id": pid}}}}
    inc = [{"type": "profile", "id": pid, "attributes": {"external_id": ext}}]
    return rec, inc


class Outcomes(unittest.TestCase):
    def test_params_use_sparse_profile_fields(self):
        p = events_params("LbGZwB", NOW - timedelta(days=1), NOW)
        self.assertEqual(p["fields[profile]"], "external_id")
        self.assertIn('equals(metric_id,"LbGZwB")', p["filter"])
        self.assertIn("greater-or-equal(datetime,2026-10-08T10:00:00Z)", p["filter"])

    def test_pull_into_ledger_and_history(self):
        flows = {"LbGZwB": [("e1", "P1", "2026-10-08T09:00:00+00:00", {"$flow": "TWmsiY", "$message": "m1", "Subject": "secret"}),
                            ("e2", "P1", "2026-10-08T10:00:00+00:00", {"Campaign Name": "x"})],
                 "XuL6rm": [("p1", "P1", "2026-10-09T08:00:00+00:00", {"$flow": "RDaeGW", "Message Type": "flow"}),
                            ("p2", "P1", "2026-10-09T08:30:00+00:00", {"$flow": "DISP", "Message Type": "flow"})]}

        def route(method, url):
            for mid, evs in flows.items():
                if mid in url:
                    data, inc = [], []
                    for eid, pid, when, props in evs:
                        r, i = _event(eid, pid, when, props, ext="cust-1")
                        data.append(r)
                        inc += i
                    return 200, {}, {"data": data, "included": inc, "links": {"next": None}}
            return 200, {}, {"data": [], "links": {"next": None}}

        store = Store()
        c = client(FakeTransport(route=route))
        n = pull_message_events(c, store, NOW, dispatcher_flow_id="DISP")
        self.assertEqual(n["Received Email"], 2)
        self.assertEqual(n["Received Push"], 2)
        rows = {r["event_id"]: r for r in store.db.execute("SELECT * FROM message_events")}
        self.assertEqual(rows["p1"]["transactional"], 1)
        self.assertEqual(rows["p2"]["nba"], 1)
        self.assertEqual(rows["e2"]["message_type"], "campaign")
        self.assertNotIn("secret", json.dumps([dict(r) for r in rows.values()]))
        h = store.history("cust-1", NOW, None)
        self.assertEqual(h.sends_30d["email"], 2)
        self.assertEqual(h.sends_30d["push"], 0)  # transactional and dispatcher pushes excluded
        self.assertAlmostEqual(h.hours_since_last_send, 24.0)
        self.assertEqual(store.last_transactional_push("cust-1", NOW), datetime(2026, 10, 9, 8, 0, tzinfo=timezone.utc))
        again = pull_message_events(c, store, NOW, dispatcher_flow_id="DISP")
        self.assertEqual(sum(again.values()), 0)  # incremental, idempotent
        store.close()


class Ingest(unittest.TestCase):
    def test_trip_events_to_builder_inputs(self):
        ti = TripInputs()
        rec, inc = _event("x1", "P9", "2026-10-01T09:00:00+00:00",
                          {"order_number": "1001", "items": json.dumps([{"sku": "esim-turkey"}]),
                           "email": "a@b.c", "shipping_address": "street"})
        apply_event(ti, "hly_order_confirmed", rec, {("profile", "P9"): inc[0]})
        rec, inc = _event("x2", "P9", "2026-10-03T08:00:00+00:00",
                          {"order_id": "1001", "activation_country": "Turkey", "purchased_destination": "Turkey",
                           "number_of_days_purchased": "10"})
        apply_event(ti, "activation_event", rec, {("profile", "P9"): inc[0]})
        rec, inc = _event("x3", "P9", "2026-10-03T20:00:00+00:00",
                          {"order_id": "1001", "expiration_date_eSIM_v2": "2026-10-13 09:17:16+00:00", "iccid_eSIM": "8935"})
        apply_event(ti, "expiration_date_eSIM_v2", rec, {("profile", "P9"): inc[0]})
        o = ti.orders["1001"]
        self.assertEqual((o.customer_id, o.destination, o.esim_id), ("P9", "TR", "1001"))
        self.assertEqual(ti.activations[0].first_network_country, "TR")
        self.assertEqual(ti.entitlements[0].plan_end_at, datetime(2026, 10, 13, 9, 17, 16, tzinfo=timezone.utc))
        self.assertNotIn("a@b.c", repr(ti))
        self.assertNotIn("8935", repr(ti))

    def test_unknown_destination_is_counted_not_guessed(self):
        ti = TripInputs()
        rec, inc = _event("x", "P1", "2026-10-03T08:00:00+00:00",
                          {"order_id": "9", "activation_country": "Atlantis", "purchased_destination": "Atlantis"})
        apply_event(ti, "activation_event", rec, {("profile", "P1"): inc[0]})
        self.assertEqual(ti.orders["9"].destination, "")
        self.assertEqual(ti.unknown_destinations["Atlantis"], 2)


class Verify(unittest.TestCase):
    def test_verify_reports_checks_without_values(self):
        from nba.klaviyo.registry import ALL_METRICS

        metrics = [{"type": "metric", "id": mid, "attributes": {"name": n}} for n, mid in ALL_METRICS.items()]

        def route(method, url):
            self.assertEqual(method, "GET")
            if "/api/accounts" in url:
                return 200, {}, {"data": [{"id": "H5sgvh", "attributes": {"test_account": False,
                                 "contact_information": {"organization_name": "HOLAFLY LIMITED"}}}]}
            if "/api/metrics" in url:
                return 200, {}, {"data": metrics, "links": {"next": None}}
            if "/api/flows" in url:
                return 200, {}, {"data": [{"id": "TWmsiY", "attributes": {"name": "x", "status": "live"}}], "links": {}}
            if "/api/profiles" in url:
                return 200, {}, {"data": [{"attributes": {"external_id": None, "locale": "es"}}] * 10}
            if "/api/events" in url:
                return 200, {}, {"data": [{"attributes": {"event_properties": {"order_id": "SECRET", "expiration_date_eSIM_v2": "x"}}}]}
            return 404, {}, None

        rep = verify_account(client(FakeTransport(route=route)))
        self.assertTrue(rep["ok"])
        checks = {c["check"]: c for c in rep["checks"]}
        self.assertTrue(checks["account"]["ok"])
        self.assertTrue(checks["required metrics"]["ok"])
        self.assertFalse(checks["overlapping live flows"]["ok"])
        self.assertFalse(checks["profile identity"]["ok"])
        self.assertNotIn("SECRET", json.dumps(rep))


class Setup(unittest.TestCase):
    def test_dispatcher_has_a_branch_per_messaging_channel(self):
        spec = dispatcher_spec()
        conds = {b["condition"] for b in spec["branches"]}
        self.assertIn("action = I1 AND channel = whatsapp", conds)
        self.assertIn("action = R1 AND channel = email", conds)
        self.assertFalse(any("in_app" in c or "native_flow" in c for c in conds))
        self.assertIn("NBA - Global holdout", setup_markdown())


if __name__ == "__main__":
    unittest.main()
