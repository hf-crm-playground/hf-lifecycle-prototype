import json
import os
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone

from helpers import EXAMPLES, NOW, ctx, customer_id

from nba import cli
from nba.assignment import decision_id, in_global_holdout, stable_hash
from nba.config import DEFAULT_POLICY
from nba.context import ContactHistory, CustomerSignals, build_context
from nba.countries import timezone_for, to_code
from nba.io import read_signals_csv
from nba.klaviyo.client import KlaviyoClient
from nba.policy import decide
from nba.run import run_hour
from nba.store import SCHEMA, SCHEMA_PATH, Store
from nba.windows import next_send_time
from trip_state.models import TripSnapshot


class Assignment(unittest.TestCase):
    def test_hash_matches_the_prototype(self):
        # Values from src/engine/rng.js stableHash() in the console.
        self.assertEqual(stable_hash("c1|holdout"), 0.24337527388706803)
        self.assertEqual(stable_hash("cust_04_in_trip|I1|trip-1"), 0.6086016604676843)
        self.assertEqual(stable_hash("é漢字|x"), 0.35071912105195224)

    def test_holdout_share_and_quarter_salt(self):
        ids = [f"cust{i}" for i in range(20_000)]
        share = sum(in_global_holdout(c, 0.10, "2026Q4") for c in ids) / len(ids)
        self.assertAlmostEqual(share, 0.10, delta=0.01)
        same = sum(in_global_holdout(c, 0.10, "2026Q4") == in_global_holdout(c, 0.10, "2027Q1") for c in ids)
        self.assertLess(same, len(ids))  # re-drawn next quarter

    def test_decision_id_is_per_customer_hour(self):
        a = decision_id("c1", NOW)
        self.assertEqual(a, decision_id("c1", NOW + timedelta(minutes=59)))
        self.assertNotEqual(a, decision_id("c1", NOW + timedelta(hours=1)))
        self.assertNotEqual(a, decision_id("c2", NOW))


class CountriesAndWindows(unittest.TestCase):
    def test_names_slugs_and_codes(self):
        cases = {"Turkey": "TR", "esim-turkey": "TR", "Türkiye": "TR", "United States": "US", "esim-usa": "US",
                 "tr": "TR", "esim-europe": "EUROPE", "South Korea": "KR", "United Kingdom": "GB", "Atlantis": None}
        for raw, code in cases.items():
            self.assertEqual(to_code(raw), code, raw)
        self.assertEqual(timezone_for("JP"), "Asia/Tokyo")

    def test_window_crosses_midnight_and_dst(self):
        late = datetime(2026, 10, 9, 21, 0, tzinfo=timezone.utc)  # 06:00 next day in Tokyo
        st = next_send_time(late, "Asia/Tokyo", (9, 20))
        self.assertTrue(st.held)
        self.assertEqual(st.send_at, datetime(2026, 10, 10, 0, 0, tzinfo=timezone.utc))
        after_dst = datetime(2026, 10, 25, 7, 0, tzinfo=timezone.utc)  # 08:00 CET, after DST ends
        self.assertEqual(next_send_time(after_dst, "Europe/Madrid", (9, 20)).send_at,
                         datetime(2026, 10, 25, 8, 0, tzinfo=timezone.utc))


class ContextFromSnapshot(unittest.TestCase):
    def snap(self, **kw):
        base = dict(customer_id="c1", as_of=NOW, trip_state="in_trip", trip_confidence="high", trip_id="c1:2026-10-01",
                    trip_destination="JP", days_in_trip=9, days_since_last_usage=0, usage_fresh=True,
                    data_remaining_pct=12.0, plan_end_at=NOW + timedelta(days=3),
                    projected_runout_at=NOW + timedelta(days=1))
        base.update(kw)
        return TripSnapshot(**base)

    def test_in_trip_uses_destination_timezone(self):
        c = build_context(self.snap(), CustomerSignals("c1", home_timezone="Europe/Madrid"), ContactHistory())
        self.assertEqual((c.local_tz, c.tz_source), ("Asia/Tokyo", "destination"))
        self.assertTrue(c.runout_before_plan_end)
        self.assertAlmostEqual(c.plan_end_hours, 72)

    def test_post_trip_days_after_confirm(self):
        s = self.snap(trip_state="post_trip", days_since_last_usage=20, last_trip_ended_day=date(2026, 9, 29))
        c = build_context(s, CustomerSignals("c1", market="ES"), ContactHistory())
        self.assertEqual(c.days_after_confirm, 4)
        self.assertEqual(c.days_since_trip_end, 10)
        self.assertEqual((c.local_tz, c.tz_source), ("Europe/Madrid", "market"))
        self.assertFalse(c.tz_known)

    def test_features_hold_no_profile_id(self):
        c = build_context(self.snap(), CustomerSignals("c1", klaviyo_profile_id="01X"), ContactHistory())
        self.assertNotIn("01X", json.dumps(c.to_features()))


class LedgerHistory(unittest.TestCase):
    def test_own_sends_count_and_ignored_twice_is_fatigue(self):
        store = Store()
        cid = customer_id()
        for days_ago in (20, 10):
            t = NOW - timedelta(days=days_ago)
            d = decide(ctx(cid=cid, decided_at=t, data_remaining_pct=15.0))
            self.assertEqual(d.action, "I1")
            store.log(d, "dispatched", t)
        h = store.history(cid, NOW, "trip-1")
        self.assertEqual(h.sends_30d["total"], 2)
        self.assertEqual(h.fatigued_keys, ["topup_runout"])
        self.assertEqual(decide(ctx(cid=cid, history=h, data_remaining_pct=15.0)).candidates[0].why, "CONTENT_FATIGUE")
        # an open from the dispatcher within 72 h of one send clears it
        store.add_message_events([{"event_id": "o1", "customer_id": cid, "profile_id": "P", "kind": "opened",
                                   "channel": "push", "occurred_at": NOW - timedelta(days=10) + timedelta(hours=2),
                                   "nba": True}])
        self.assertEqual(store.history(cid, NOW, "trip-1").fatigued_keys, [])
        store.close()

    def test_schema_file_matches_code(self):
        with open(SCHEMA_PATH) as fh:
            self.assertIn(SCHEMA.strip(), fh.read())


class EndToEnd(unittest.TestCase):
    """Builder examples -> snapshots -> decisions -> outbox -> decision log."""

    def run_cli(self, tmp, as_of, *extra):
        args = ["run", "--customers", f"{EXAMPLES}/customers.csv", "--orders", f"{EXAMPLES}/orders.csv",
                "--activations", f"{EXAMPLES}/activations.csv", "--usage", f"{EXAMPLES}/usage_daily.csv",
                "--entitlements", f"{EXAMPLES}/entitlements.csv", "--signals", f"{EXAMPLES}/signals.csv",
                "--as-of", as_of, "--db", f"{tmp}/nba.db", "--outbox", f"{tmp}/outbox.jsonl", "--mode", "offline", *extra]
        import contextlib
        import io

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(cli.main(args), 0)
        return json.loads(buf.getvalue())

    def test_hourly_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = self.run_cli(tmp, "2026-09-23T10:00:00Z")
            self.assertEqual(first["klaviyo_mode"], "offline")
            self.assertGreater(first["dispatched"], 0)
            self.assertEqual(first["profiles_synced"], 16)
            with open(f"{tmp}/outbox.jsonl") as fh:
                out = [json.loads(line) for line in fh]
            events = [o for o in out if o["url"].endswith("/api/events")]
            self.assertEqual(len(events), first["events_written"])
            self.assertTrue(all(e["body"]["data"]["attributes"]["unique_id"].startswith("dec_") for e in events))
            jobs = [o for o in out if o["url"].endswith("/api/profile-bulk-import-jobs")]
            self.assertEqual(len(jobs), 1)
            props = {p["attributes"]["external_id"]: p["attributes"]["properties"]
                     for p in jobs[0]["body"]["data"]["attributes"]["profiles"]["data"]}
            self.assertEqual(props["cust_04_in_trip"]["trip_state"], "in_trip")
            self.assertEqual(props["cust_04_in_trip"]["nba_inapp_action"], "referral_intrip")
            self.assertTrue(props["cust_04_in_trip"]["rating_prompt_allowed"])

            same_hour = self.run_cli(tmp, "2026-09-23T10:30:00Z")
            self.assertEqual(same_hour["dispatched"], 0)  # idempotent within the hour
            self.assertEqual(same_hour["duplicate_hour"] + same_hour["unchanged"], same_hour["at_decision_point"])

            later = self.run_cli(tmp, "2026-09-23T11:00:00Z")
            self.assertEqual(later["dispatched"], 0)  # recency gaps and fired fixed rules

            store = Store(f"{tmp}/nba.db")
            rows = store.decisions()
            self.assertTrue(all(len(r["sentence"]) <= 200 for r in rows))
            self.assertTrue(all(r["policy_version"] == DEFAULT_POLICY.policy_version for r in rows))
            self.assertEqual({r["dispatch_mode"] for r in rows if r["status"] == "dispatched"}, {"offline"})
            self.assertNotIn("@", json.dumps([dict(r) for r in rows]))
            store.close()

    def test_held_decision_is_revalidated_when_window_opens(self):
        snap = TripSnapshot(customer_id="c-held", as_of=NOW, trip_state="post_trip", trip_confidence="high",
                            trip_id="c-held:2026-08-20", trip_destination="JP", days_since_last_usage=30,
                            last_trip_ended_day=date(2026, 9, 25))
        cid = "c-held"
        while in_global_holdout(cid, 0.1, DEFAULT_POLICY.holdout_salt):
            cid += "x"
        snap.customer_id = cid
        sig = {cid: CustomerSignals(cid, market="ES", home_timezone="Europe/Madrid", consent_email=True)}
        store, client = Store(), KlaviyoClient(mode="offline")
        early = datetime(2026, 10, 9, 3, 0, tzinfo=timezone.utc)  # 05:00 Madrid
        r1 = run_hour(early, {cid: snap}, sig, store, client)
        self.assertEqual(r1.held, 1, r1.summary())
        self.assertEqual(r1.events_written, 0)
        r2 = run_hour(early + timedelta(hours=2, minutes=30), {cid: snap}, sig, store, client)
        self.assertEqual(r2.waiting_for_window, 1)
        r3 = run_hour(datetime(2026, 10, 9, 7, 0, tzinfo=timezone.utc), {cid: snap}, sig, store, client)
        self.assertEqual((r3.released, r3.dispatched, r3.events_written), (1, 1, 1))
        statuses = sorted(r["status"] for r in store.decisions())
        self.assertEqual(statuses, ["dispatched", "released"])
        store.close()

    def test_explain_and_setup_commands(self):
        import contextlib
        import io

        with tempfile.TemporaryDirectory() as tmp:
            self.run_cli(tmp, "2026-09-23T10:00:00Z")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                self.assertEqual(cli.main(["explain", "--db", f"{tmp}/nba.db", "--customer", "cust_04_in_trip"]), 0)
            self.assertIn("I7", buf.getvalue())
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(["setup-spec", "--out", f"{tmp}/setup.md"]), 0)
            self.assertTrue(os.path.getsize(f"{tmp}/setup.md") > 1000)

    def test_signals_csv(self):
        s = read_signals_csv(f"{EXAMPLES}/signals.csv")
        self.assertEqual(len(s), 16)
        self.assertIs(s["cust_04_in_trip"].app_installed, True)
        self.assertEqual(s["cust_01_pre_trip"].hours_since_order, 73.0)
        self.assertIsNone(s["cust_01_pre_trip"].nps_score)


if __name__ == "__main__":
    unittest.main()
