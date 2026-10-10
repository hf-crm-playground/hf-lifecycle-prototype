import json
import os
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from helpers import EXAMPLES, NOW, FakeTransport, ctx, customer_id, history

from nba.config import DEFAULT_POLICY
from nba.evaluate import first_listed, greedy, ips
from nba.features import FEATURE_VERSION, cell, feature_vector
from nba.guardrails import enforce, resume
from nba.klaviyo.client import KlaviyoClient
from nba.klaviyo.signals_sync import derive_signals, event_row, profile_row, pull_profile_signals
from nba.learner import Model, Option, Stat, prior_model, train
from nba.pipeline import Inputs, hourly, nightly
from nba.policy import decide
from nba.rewards import attribute, uplift_report, uplift_table
from nba.server import handle_trigger, make_handler
from nba.simulate import simulate
from nba.store import Store

LOW = dict(data_remaining_pct=15.0)
LEARN = replace(DEFAULT_POLICY, ranking="learner")


def ev(cid, metric, when, value=None, attrs=None, eid=None):
    return {"event_id": eid or f"{cid}-{metric}-{when.isoformat()}", "customer_id": cid, "metric": metric,
            "occurred_at": when, "value": value, "attrs": attrs or {}}


class Signals(unittest.TestCase):
    def test_profile_row_reads_consent_without_pii(self):
        rec = {"id": "P1", "attributes": {"external_id": None, "locale": "es-ES", "updated": "2026-10-09T10:00:00+00:00",
               "email": "x@y.z", "location": {"timezone": "Europe/Madrid", "country": "Spain"},
               "subscriptions": {"email": {"marketing": {"can_receive_email_marketing": True}},
                                 "mobile_push": {"marketing": {"can_receive_push_marketing": False}}},
               "predictive_analytics": {"predicted_clv": "42.5", "churn_probability": 0.3}}}
        r = profile_row(rec)
        self.assertEqual((r["customer_id"], r["country"], r["can_email"], r["can_push"], r["predicted_clv"]),
                         ("P1", "ES", True, False, 42.5))
        self.assertNotIn("x@y.z", json.dumps(r))

    def test_profile_pull_uses_sparse_fields_and_cursor(self):
        t = FakeTransport([(200, {}, {"data": [{"id": "P1", "attributes": {"updated": "2026-10-09T09:00:00+00:00"}}],
                                      "links": {"next": None}})])
        store = Store()
        c = KlaviyoClient(api_key="k", transport=t, sleep=lambda s: None)
        self.assertEqual(pull_profile_signals(c, store, NOW), 1)
        url = t.calls[0]["url"]
        fields = url.split("fields[profile]=")[1].split("&")[0].split(",")
        self.assertIn("external_id", fields)
        self.assertFalse({"email", "phone_number", "first_name", "last_name", "location.address1"} & set(fields))
        self.assertEqual(store.get_cursor("profiles:updated"), "2026-10-09T09:00:00+00:00")

    def test_event_row_keeps_only_whitelisted_keys(self):
        rec = {"id": "e1", "attributes": {"datetime": "2026-10-09T08:00:00+00:00", "event_properties": {
            "order_id": "HLY1", "product_id": "esim-indonesia", "Push Token": "tok", "Device ID": "dev"}},
            "relationships": {"profile": {"data": {"type": "profile", "id": "P1"}}}}
        r = event_row("esim_installation_complete", rec, {})
        self.assertEqual(r["attrs"], {"order_id": "HLY1", "product_id": "esim-indonesia"})
        self.assertNotIn("tok", json.dumps(r, default=str))

    def test_derive_signals_point_in_time(self):
        store = Store()
        store.upsert_profile_signals([{"profile_id": "c1", "customer_id": "c1", "locale": "en-GB", "timezone": "Europe/London",
                                       "country": "GB", "can_email": True, "can_push": True, "can_whatsapp": False,
                                       "predicted_clv": 80.0}])
        store.add_customer_events([
            ev("c1", "session_start", NOW - timedelta(days=3)),
            ev("c1", "Added to Cart", NOW - timedelta(hours=5)),
            ev("c1", "Submitted NPS Score", NOW - timedelta(days=4), attrs={"nps_total_score": "10"}),
            ev("c1", "Top-up order created", NOW + timedelta(hours=1), value=9.0),  # in the future: ignored
        ])
        s = derive_signals(store, NOW, customers=["c1"])["c1"]
        self.assertTrue(s.consent_email and s.app_installed and s.push_token and s.in_app_session_14d)
        self.assertTrue(s.intent_24h)
        self.assertFalse(s.topped_up_24h)
        self.assertEqual((s.market, s.nps_score, s.days_since_nps, s.predicted_clv), ("GB", 10, 4, 80.0))

    def test_customers_without_klaviyo_data_keep_warehouse_values(self):
        from nba.context import CustomerSignals

        base = {"w1": CustomerSignals("w1", intent_24h=True, paid_share=0.4)}
        s = derive_signals(Store(), NOW, base, customers=["w1"])["w1"]
        self.assertTrue(s.intent_24h)
        self.assertEqual(s.paid_share, 0.4)


class Features(unittest.TestCase):
    def test_vector_and_cell(self):
        c = ctx(**LOW)
        v = feature_vector(c)
        self.assertEqual(v["data_left_b"], "<20")
        self.assertEqual(cell(c), "in_trip|low|app|europe")
        d = decide(c)
        self.assertEqual((d.feature_version, d.cell), (FEATURE_VERSION, "in_trip|low|app|europe"))
        self.assertNotIn(c.customer_id, json.dumps(v))


class Learner(unittest.TestCase):
    def test_propensities_sum_to_one_with_epsilon_floor(self):
        m = prior_model(NOW, epsilon=0.1)
        opts = [Option("I1", "push", 0.2), Option("I1", "email", 0.2), Option("I1", "whatsapp", 0.2)]
        ch = m.choose(opts, "in_trip|low|app|europe", seed="dec_1")
        ps = [o["propensity"] for o in ch.table]
        self.assertAlmostEqual(sum(ps), 1.0, places=4)
        self.assertTrue(all(p >= 0.1 / 3 - 1e-9 for p in ps))
        again = m.choose(opts, "in_trip|low|app|europe", seed="dec_1")
        self.assertEqual((again.option.key, again.propensity), (ch.option.key, ch.propensity))

    def test_hierarchical_pooling(self):
        rows = [{"arm": "treated", "action": "I1", "channel": "push", "cell": "A", "reward": 9.0} for _ in range(200)]
        rows += [{"arm": "treated", "action": "I1", "channel": "push", "cell": "B", "reward": 0.0} for _ in range(3)]
        m = train(rows, NOW)
        a, _ = m.posterior("I1", "push", "A")
        b, _ = m.posterior("I1", "push", "B")
        new, _ = m.posterior("I1", "push", "never-seen")
        self.assertGreater(a, 8.0)
        self.assertGreater(b, 4.0)  # three zeros do not overturn the parent
        self.assertGreater(new, b)  # an unseen cell sits on the parent
        restored = Model.from_params(m.to_params(), NOW)
        self.assertEqual(restored.posterior("I1", "push", "A"), m.posterior("I1", "push", "A"))

    def test_learner_respects_rules_and_logs_propensity(self):
        model = prior_model(NOW)
        d = decide(ctx(signals={"success_moment": True}, **LOW), LEARN, model=model)
        self.assertEqual(d.action, "I1")  # Tier 0 still beats Tier 3: the learner only chooses within a tier
        self.assertEqual(d.policy_mode, "learner")
        self.assertEqual({o["action"] for o in d.options}, {"I1"})
        self.assertEqual({o["channel"] for o in d.options}, {"push", "email"})  # no WhatsApp consent
        self.assertGreater(d.propensity, 0)
        held = decide(ctx(history=history(sends={"total": 8}), **LOW), LEARN, model=model)
        self.assertEqual(held.reason, "CAP_REACHED")

    def test_rules_mode_logs_deterministic_propensity(self):
        d = decide(ctx(**LOW))
        self.assertEqual((d.policy_mode, d.propensity), ("rules", 1.0))
        self.assertEqual(d.options, [{"action": "I1", "channel": "push", "propensity": 1.0}])


class RewardsAndEvaluation(unittest.TestCase):
    def _send(self, store, cid, when, channel="push", **kw):
        d = decide(ctx(cid=cid, decided_at=when, **LOW, **kw), LEARN, model=prior_model(when))
        if d.outcome == "send":
            store.log(d, "dispatched", when)
        else:
            store.log(d, "logged", when)
        return d

    def test_attribution_waits_for_window_and_scores_revenue(self):
        store = Store()
        cid = customer_id()
        t = NOW - timedelta(days=10)
        d = self._send(store, cid, t)
        self.assertEqual(d.action, "I1")
        store.add_customer_events([ev(cid, "Top-up order created", t + timedelta(hours=3), value=9.0),
                                   ev(cid, "hly_order_confirmed", t + timedelta(days=5), value=20.0),
                                   ev(cid, "Top-up order created", t + timedelta(days=9), value=9.0)])  # outside horizon
        self.assertEqual(attribute(store, t + timedelta(days=2), dispatch_modes=None).get("window_open"), 1)
        self.assertEqual(attribute(store, NOW).get("not_live"), 1)  # never really sent
        stats = attribute(store, NOW, dispatch_modes=None)
        self.assertEqual(stats["treated"], 1)
        r = store.rewards()[0]
        self.assertEqual((r["converted"], r["revenue"], r["reward"], r["arm"]), (1, 29.0, 29.0, "treated"))
        self.assertIsNotNone(r["propensity"])

    def test_unsubscribe_costs_reward(self):
        store = Store()
        cid = customer_id(start=50)
        t = NOW - timedelta(days=10)
        self._send(store, cid, t)
        store.add_message_events([{"event_id": "u1", "customer_id": cid, "profile_id": "P", "kind": "unsubscribed",
                                   "channel": "email", "occurred_at": t + timedelta(hours=10)}])
        attribute(store, NOW, dispatch_modes=None)
        self.assertEqual(store.rewards()[0]["reward"], -DEFAULT_POLICY.c_unsub)

    def test_uplift_table_from_controls(self):
        store = Store()
        for i in range(400):
            arm = "control" if i % 4 == 0 else "treated"
            store.upsert_reward({"decision_id": f"d{i}", "customer_id": f"c{i}", "action": "I1", "channel": "push",
                                 "arm": arm, "cell": "x", "propensity": 1.0, "model_version": None,
                                 "decided_at": NOW.isoformat(), "converted": int((i % 10) < (5 if arm == "treated" else 2)),
                                 "revenue": 9.0, "unsubscribed": 0, "spam": 0, "reward": 9.0, "computed_at": NOW.isoformat()})
        rows = uplift_report(store, min_control_outcomes=10)
        self.assertEqual(rows[0].status, "keep")
        self.assertGreater(rows[0].uplift, 0)
        self.assertIn("I1", uplift_table(rows))

    def test_ips_recovers_known_values(self):
        # Uniform logging over two arms; arm A pays 10, arm B pays 0.
        rows = []
        for i in range(2000):
            a = "I1:push" if i % 2 == 0 else "I1:email"
            rows.append({"reward": 10.0 if a == "I1:push" else 0.0, "logged": a, "cell": "*", "propensity": 0.5,
                         "options": [{"action": "I1", "channel": "push"}, {"action": "I1", "channel": "email"}]})
        always_push = ips(rows, lambda o, c: "I1:push", "push")
        always_email = ips(rows, lambda o, c: "I1:email", "email")
        self.assertAlmostEqual(always_push.ips, 10.0, places=6)
        self.assertAlmostEqual(always_email.ips, 0.0, places=6)
        self.assertAlmostEqual(first_listed()(rows[0]["options"], "*"), "I1:push")


class GuardrailsAndOps(unittest.TestCase):
    def test_guardrail_pauses_and_run_obeys(self):
        store = Store()
        t0 = NOW - timedelta(days=10)
        for i in range(800):
            cid = f"g{i}"
            d = decide(ctx(cid=cid, decided_at=t0, phase="post_trip", days_after_confirm=12, trip_confidence="high"))
            if d.action != "T2":
                continue
            store.log(d, "dispatched", t0)
            if i % 40 == 0:  # 2.5% unsubscribe
                store.add_message_events([{"event_id": f"u{i}", "customer_id": cid, "profile_id": cid,
                                           "kind": "unsubscribed", "channel": "email", "occurred_at": t0 + timedelta(hours=2)}])
        self.assertEqual(enforce(store, NOW)["paused_now"], [])  # not live: no pause
        rep = enforce(store, NOW, None)
        self.assertIn("T2", rep["paused_now"])
        killed = store.killed_actions()
        self.assertIn("T2", killed)
        from nba.run import run_hour
        from trip_state.models import TripSnapshot

        snap = TripSnapshot(customer_id="fresh", as_of=NOW, trip_state="post_trip", trip_confidence="high",
                            trip_id="fresh:1", trip_destination="JP", days_since_last_usage=28)
        from nba.context import CustomerSignals

        res = run_hour(NOW, {"fresh": snap}, {"fresh": CustomerSignals("fresh", consent_email=True, market="ES")},
                       store, KlaviyoClient(mode="offline"))
        if res.decisions:
            self.assertNotEqual(res.decisions[0].action, "T2")
            row = json.loads(store.get(res.decisions[0].decision_id)["candidates"])
            self.assertIn(("T2", "KILL_SWITCH"), {(c["id"], c["why"]) for c in row})
        resume(store, "T2", NOW)
        self.assertNotIn("T2", store.killed_actions())
        again = enforce(store, NOW + timedelta(days=1), None)
        self.assertNotIn("T2", again["paused_now"])  # the old breach does not re-pause a resumed action

    def test_locks_prevent_overlap(self):
        store = Store()
        self.assertTrue(store.acquire_lock("hourly", "a", NOW, timedelta(minutes=55)))
        self.assertFalse(store.acquire_lock("hourly", "b", NOW + timedelta(minutes=10), timedelta(minutes=55)))
        self.assertTrue(store.acquire_lock("hourly", "c", NOW + timedelta(hours=1), timedelta(minutes=55)))

    def test_old_database_is_migrated(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = f"{tmp}/old.db"
            # The v0.1 schema: decision_log without the learning columns, snapshots without signals.
            from nba.store import LOG_COLUMNS, SCHEMA

            new_cols = {c for c, _ in LOG_COLUMNS if c != "dispatch_mode"}
            old = "\n".join(line for line in SCHEMA.splitlines() if line.strip().split(" ")[0] not in new_cols)
            old = old.replace("dispatch_mode          TEXT,", "dispatch_mode          TEXT")
            old = old.replace("  declared    TEXT,\n", "  declared    TEXT\n")
            old = "\n".join(line for line in old.splitlines() if not line.strip().startswith("signals "))
            db = sqlite3.connect(path)
            db.executescript(old)
            db.commit()
            cols_before = {r[1] for r in db.execute("PRAGMA table_info(decision_log)")}
            self.assertNotIn("propensity", cols_before)
            db.close()
            s = Store(path)
            cols = {r["name"] for r in s.db.execute("PRAGMA table_info(decision_log)")}
            self.assertTrue({"propensity", "options", "feature_vector"} <= cols)
            self.assertIn("signals", {r["name"] for r in s.db.execute("PRAGMA table_info(snapshots)")})
            s.close()

    def test_hourly_and_nightly_jobs_offline(self):
        store = Store()
        client = KlaviyoClient(mode="offline")
        inputs = Inputs(customers=f"{EXAMPLES}/customers.csv", orders=f"{EXAMPLES}/orders.csv",
                        activations=f"{EXAMPLES}/activations.csv", usage=f"{EXAMPLES}/usage_daily.csv",
                        entitlements=f"{EXAMPLES}/entitlements.csv", signals=f"{EXAMPLES}/signals.csv")
        as_of = datetime(2026, 9, 23, 10, 0, tzinfo=timezone.utc)
        h = hourly(as_of, store, client, inputs, LEARN)
        self.assertEqual(h["status"], "ok")
        self.assertGreater(h["run"]["learner_decisions"], 0)
        self.assertEqual(h["model_version"], "prior")
        with tempfile.TemporaryDirectory() as tmp:
            n = nightly(as_of + timedelta(days=9), store, client, report_dir=tmp, dispatch_modes=None)
            self.assertEqual(n["status"], "ok")
            self.assertTrue(os.path.exists(n["report"]))
        self.assertGreater(sum(n["rewards"].values()), 0)
        self.assertEqual([r["status"] for r in store.job_runs()], ["ok", "ok"])
        # the store now has snapshots + signals for real-time decisions
        snap, _ = store.load_snapshot("cust_04_in_trip")
        self.assertEqual(snap.trip_state, "in_trip")
        self.assertIsNotNone(store.load_signals("cust_04_in_trip"))


class RealTime(unittest.TestCase):
    def test_trigger_decides_for_one_customer(self):
        store = Store()
        client = KlaviyoClient(mode="offline")
        inputs = Inputs(customers=f"{EXAMPLES}/customers.csv", orders=f"{EXAMPLES}/orders.csv",
                        activations=f"{EXAMPLES}/activations.csv", usage=f"{EXAMPLES}/usage_daily.csv",
                        entitlements=f"{EXAMPLES}/entitlements.csv", signals=f"{EXAMPLES}/signals.csv")
        as_of = datetime(2026, 9, 23, 10, 0, tzinfo=timezone.utc)
        hourly(as_of, store, client, inputs)
        code, body = handle_trigger(store, client, {"customer_id": "nobody"}, as_of)
        self.assertEqual((code, body["decided"]), (202, False))
        code, body = handle_trigger(store, client, {"customer_id": "cust_12_refunded",
                                                    "event": {"metric": "Shady Metric"}}, as_of)
        self.assertEqual(code, 400)
        code, body = handle_trigger(store, client, {"customer_id": "cust_04_in_trip",
                                                    "event": {"metric": "session_start", "id": "rt1"}},
                                    as_of + timedelta(minutes=20))
        self.assertEqual(code, 200)
        self.assertIn(body["status"], ("decided", "no_decision_point"))

    def test_http_requires_token(self):
        from http.server import ThreadingHTTPServer

        with tempfile.TemporaryDirectory() as tmp:
            srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(f"{tmp}/nba.db", lambda: KlaviyoClient(mode="offline"),
                                                                        "s3cret-token-1234567"))
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            base = f"http://127.0.0.1:{srv.server_address[1]}"
            try:
                req = urllib.request.Request(f"{base}/v1/trigger", data=b'{"customer_id":"x"}', method="POST")
                with self.assertRaises(urllib.error.HTTPError) as e:
                    urllib.request.urlopen(req)
                self.assertEqual(e.exception.code, 401)
                req.add_header("X-NBA-Token", "s3cret-token-1234567")
                with urllib.request.urlopen(req) as r:
                    self.assertEqual(json.loads(r.read())["status"], "recorded")
                with urllib.request.urlopen(f"{base}/healthz") as r:
                    self.assertTrue(json.loads(r.read())["ok"])
            finally:
                srv.shutdown()
                srv.server_close()


class ReviewFixes(unittest.TestCase):
    def test_control_customer_logged_once_per_exposure(self):
        from nba.run import run_hour
        from nba.context import CustomerSignals
        from trip_state.models import TripSnapshot

        cid = customer_id(control_for="I1", trip_id="c:trip")
        store, client = Store(), KlaviyoClient(mode="offline")
        sig = {cid: CustomerSignals(cid, market="ES", consent_email=True, app_installed=True, push_token=True)}
        for day in range(5):
            now = NOW + timedelta(days=day)
            snap = TripSnapshot(customer_id=cid, as_of=now, trip_state="in_trip", trip_confidence="high", trip_id="c:trip",
                                trip_destination="ES", days_in_trip=6 + day, days_since_last_usage=0, usage_fresh=True,
                                data_remaining_pct=10.0, plan_end_at=now + timedelta(days=5))
            run_hour(now, {cid: snap}, sig, store, client)
        controls = [r for r in store.decisions() if r["arm"] == "action_control"]
        self.assertEqual(len(controls), 3)  # days 0, 2, 4: once per 48 h cooldown, like a treated send; not daily
        self.assertIsNotNone(controls[0]["send_at"])

    def test_trigger_id_differs_from_hourly(self):
        from nba.assignment import decision_id

        self.assertNotEqual(decision_id("c1", NOW), decision_id("c1", NOW, "rt"))
        self.assertNotEqual(decision_id("c1", NOW, "rt"), decision_id("c1", NOW + timedelta(minutes=1), "rt"))

    def test_trigger_rejects_bad_payloads(self):
        store, client = Store(), KlaviyoClient(mode="offline")
        for bad in ([1, 2], {"customer_id": "x", "event": "str"},
                    {"customer_id": "x", "event": {"metric": "session_start", "time": "2026-10-09T10:00:00"}},
                    {"customer_id": "x", "event": {"metric": "session_start", "time": "2027-01-01T00:00:00Z"}}):
            self.assertEqual(handle_trigger(store, client, bad, NOW)[0], 400, bad)

    def test_signals_never_switched_off_by_missing_events(self):
        from nba.context import CustomerSignals

        store = Store()
        store.add_customer_events([ev("w2", "Viewed Product", NOW - timedelta(days=200))])
        base = {"w2": CustomerSignals("w2", intent_24h=True, topped_up_24h=True, success_moment=True, nps_score=9,
                                      always_on_threshold_event=True, in_app_session_14d=True)}
        s = derive_signals(store, NOW, base, customers=["w2"])["w2"]
        self.assertEqual((s.intent_24h, s.topped_up_24h, s.success_moment, s.nps_score, s.always_on_threshold_event,
                          s.in_app_session_14d), (True, True, True, 9, True, True))


class Simulation(unittest.TestCase):
    def test_learner_beats_rules_in_synthetic_world(self):
        rules = simulate(days=21, per_day=120, mode="rules")
        learn = simulate(days=21, per_day=120, mode="learner")
        self.assertLess(learn["weeks"][-1]["regret_per_decision"], rules["weeks"][-1]["regret_per_decision"] / 3)
        self.assertEqual(learn["final_greedy"], {"europe": "I1:push", "latam": "I1:whatsapp", "us": "I1:email"})
        ope = {r["policy"].split("-")[0]: r for r in learn["ope"]}
        self.assertGreater(ope["ts"]["snips"], ope["rules"]["snips"])


if __name__ == "__main__":
    unittest.main()
