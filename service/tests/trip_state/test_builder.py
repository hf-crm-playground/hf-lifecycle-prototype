"""Scenario tests for the heuristic Trip State Builder (stdlib unittest).

Run:  python -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import sys
import unittest
from datetime import timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

from trip_state import TripConfig, build_trip_state, to_profile_properties  # noqa: E402
from trip_state.builder import (  # noqa: E402
    STATE_BETWEEN, STATE_END_PROVISIONAL, STATE_IN_TRIP, STATE_NO_TRIP,
    STATE_POST_TRIP, STATE_PRE_TRIP,
)
import fixtures as fx  # noqa: E402


def run(scenario, **kw):
    c, o, a, u, e = scenario()
    return build_trip_state(c, o, a, u, e, as_of=fx.AS_OF, **kw)


class StateMachineTests(unittest.TestCase):

    def test_no_orders_is_no_trip(self):
        snap, trips = run(fx.scn_no_orders)
        self.assertEqual(snap.trip_state, STATE_NO_TRIP)
        self.assertEqual(snap.trip_confidence, "none")
        self.assertEqual(trips, [])

    def test_order_without_activation_is_pre_trip(self):
        snap, trips = run(fx.scn_pre_trip)
        self.assertEqual(snap.trip_state, STATE_PRE_TRIP)
        self.assertEqual(snap.trip_confidence, "low")
        self.assertTrue(snap.pending_activation)
        self.assertEqual(snap.pending_esim_ids, ["e1"])
        self.assertEqual(snap.trip_destination, "JP")
        self.assertFalse(snap.test_install_detected)
        self.assertFalse(snap.departure_likely)

    def test_declared_date_reached_without_activation_flags_departure(self):
        snap, _ = run(fx.scn_departure_likely)
        self.assertEqual(snap.trip_state, STATE_PRE_TRIP)
        self.assertTrue(snap.departure_likely)

    def test_test_install_at_home_stays_pre_trip(self):
        snap, trips = run(fx.scn_test_install)
        self.assertEqual(snap.trip_state, STATE_PRE_TRIP)
        self.assertTrue(snap.test_install_detected)
        self.assertEqual(snap.trip_confidence, "medium")
        self.assertEqual(trips, [], "a 12 MB day at home must not become a trip")

    def test_in_trip_high_confidence_and_metrics(self):
        snap, trips = run(fx.scn_in_trip)
        self.assertEqual(snap.trip_state, STATE_IN_TRIP)
        self.assertEqual(snap.trip_confidence, "high")
        self.assertEqual(len(trips), 1)
        t = trips[0]
        self.assertEqual(t.start_source, "activation")
        self.assertEqual(snap.trip_started_at, fx.ts(8, 8))
        self.assertEqual(snap.days_in_trip, 9)  # day 1 = activation day
        self.assertEqual(snap.days_since_last_usage, 1)
        self.assertEqual(snap.trip_destination, "JP")
        self.assertEqual(t.trip_id, f"c1:{fx.d(8).isoformat()}")
        self.assertFalse(t.confirmed)
        self.assertTrue(snap.usage_fresh)
        # 8 days x 300 MB from 5000 MB -> 2600 MB remaining = 52 %
        self.assertAlmostEqual(snap.data_remaining_pct, 52.0, places=1)
        self.assertEqual(snap.esim_closest_to_runout, "e1")
        # 2600 MB / 300 MB per day ~ 8.7 days after the last usage day
        self.assertIsNotNone(snap.projected_runout_at)
        self.assertGreater(snap.projected_runout_at, fx.AS_OF)
        self.assertEqual(snap.plan_end_at, fx.AS_OF + timedelta(days=5))
        self.assertFalse(snap.plan_ends_within_48h)
        self.assertFalse(snap.long_trip)
        self.assertFalse(snap.pending_activation)

    def test_old_test_activation_does_not_start_the_trip(self):
        snap, trips = run(fx.scn_test_then_real_trip)
        self.assertEqual(snap.trip_state, STATE_IN_TRIP)
        self.assertEqual(len(trips), 1)
        t = trips[0]
        self.assertEqual(t.first_usage_day, fx.d(8), "the 5 MB home day is ignored")
        self.assertEqual(t.start_source, "usage", "activation 14 days earlier was a test")
        self.assertEqual(snap.days_in_trip, 9)  # first usage day counts as day 1

    def test_fourteen_days_without_usage_is_provisional_end(self):
        snap, trips = run(fx.scn_provisional_end)
        self.assertEqual(snap.days_since_last_usage, 15)
        self.assertEqual(snap.trip_state, STATE_END_PROVISIONAL)
        self.assertFalse(trips[0].confirmed)
        self.assertEqual(trips[0].provisional_end_day, fx.d(15) + timedelta(days=14))

    def test_late_usage_reopens_the_same_trip(self):
        snap, trips = run(lambda: fx.scn_provisional_end(1))
        self.assertEqual(snap.trip_state, STATE_IN_TRIP)
        self.assertEqual(len(trips), 1, "a 14-day gap keeps the trip together")
        self.assertEqual(trips[0].first_usage_day, fx.d(22))

    def test_sixteen_days_confirms_end_then_post_trip(self):
        snap, trips = run(fx.scn_post_trip)
        self.assertEqual(snap.trip_state, STATE_POST_TRIP)
        self.assertTrue(trips[0].confirmed)
        self.assertEqual(snap.trips_completed, 1)
        self.assertEqual(snap.last_trip_ended_day, fx.d(26) + timedelta(days=14))
        self.assertEqual(snap.trip_confidence, "high")

    def test_thirty_days_after_end_is_between_trips(self):
        snap, _ = run(fx.scn_between_trips)
        self.assertEqual(snap.trip_state, STATE_BETWEEN)
        self.assertEqual(snap.trips_completed, 1)

    def test_new_order_during_trip_keeps_in_trip_with_pending_flag(self):
        snap, trips = run(fx.scn_new_order_during_trip)
        self.assertEqual(snap.trip_state, STATE_IN_TRIP)
        self.assertTrue(snap.pending_activation)
        self.assertEqual(snap.pending_esim_ids, ["e2"])
        self.assertEqual(snap.trip_destination, "JP")

    def test_two_esims_back_to_back_are_one_trip(self):
        snap, trips = run(fx.scn_multi_esim_one_trip)
        self.assertEqual(len(trips), 1)
        self.assertEqual(trips[0].esim_ids, ["e1", "e2"])
        self.assertEqual(trips[0].destinations, ["JP", "KR"])
        self.assertEqual(snap.trip_destination, "JP")
        self.assertEqual(snap.trip_started_at, fx.ts(13, 8))
        self.assertEqual(snap.esim_closest_to_runout, "e2")

    def test_refund_suppresses_the_esim(self):
        snap, trips = run(fx.scn_refunded)
        self.assertEqual(snap.trip_state, STATE_NO_TRIP)
        self.assertEqual(trips, [])
        self.assertFalse(snap.pending_activation)

    def test_plan_ended_and_usage_stopped(self):
        snap, _ = run(fx.scn_plan_ended_usage_stopped)
        self.assertEqual(snap.trip_state, STATE_IN_TRIP, "14-day rule still governs the record")
        self.assertTrue(snap.plan_ended_usage_stopped)
        self.assertEqual(snap.days_since_last_usage, 2)

    def test_plan_ending_within_48h(self):
        c, o, a, u, e = fx.scn_in_trip()
        e = [fx.entitlement("c1", "e1", fx.AS_OF + timedelta(hours=30))]
        snap, _ = build_trip_state(c, o, a, u, e, as_of=fx.AS_OF)
        self.assertTrue(snap.plan_ends_within_48h)

    def test_stale_usage_withholds_runout(self):
        snap, _ = run(fx.scn_in_trip, usage_data_as_of=fx.AS_OF - timedelta(hours=60))
        self.assertEqual(snap.trip_state, STATE_IN_TRIP)
        self.assertFalse(snap.usage_fresh)
        self.assertIsNone(snap.data_remaining_pct)
        self.assertIsNone(snap.projected_runout_at)
        self.assertTrue(any("usage stale" in r for r in snap.reasons))

    def test_long_trip_flag_and_unlimited_plan(self):
        snap, _ = run(fx.scn_long_trip)
        self.assertEqual(snap.trip_state, STATE_IN_TRIP)
        self.assertTrue(snap.long_trip)
        self.assertIsNone(snap.data_remaining_pct, "unlimited plans have no run-out")

    def test_unknown_country_confidence_depends_on_usage_volume(self):
        big, _ = run(lambda: fx.scn_unknown_country(120.0))
        small, _ = run(lambda: fx.scn_unknown_country(20.0))
        self.assertEqual(big.trip_confidence, "high")
        self.assertEqual(small.trip_confidence, "medium")
        self.assertEqual(small.trip_state, STATE_IN_TRIP)

    def test_two_separate_trips(self):
        snap, trips = run(fx.scn_two_trips)
        self.assertEqual(len(trips), 2)
        self.assertTrue(trips[0].confirmed)
        self.assertFalse(trips[1].confirmed)
        self.assertEqual(snap.trips_completed, 1)
        self.assertEqual(snap.trip_state, STATE_IN_TRIP)
        self.assertEqual(snap.trip_destination, "JP")

    def test_config_is_respected(self):
        cfg = TripConfig(trip_gap_days=7, trip_end_confirm_days=9)
        snap, trips = run(fx.scn_provisional_end, config=cfg)
        # 15 days since last usage > 9 -> confirmed; end day = last + 7
        self.assertEqual(snap.trip_state, STATE_POST_TRIP)
        self.assertTrue(trips[0].confirmed)
        self.assertEqual(trips[0].provisional_end_day, fx.d(15) + timedelta(days=7))


class KlaviyoMappingTests(unittest.TestCase):

    def test_properties_are_flat_and_serialisable(self):
        snap, _ = run(fx.scn_in_trip)
        props = to_profile_properties(snap)
        self.assertEqual(props["trip_state"], "in_trip")
        self.assertEqual(props["trip_confidence"], "high")
        self.assertIsInstance(props["trip_started_at"], str)
        self.assertIsInstance(props["trip_projected_runout_at"], str)
        self.assertNotIn("trip_last_ended_day", props, "None values are omitted")
        self.assertLessEqual(len(props["trip_state_reason"]), 500)

    def test_pre_trip_properties(self):
        snap, _ = run(fx.scn_pre_trip)
        props = to_profile_properties(snap)
        self.assertEqual(props["trip_state"], "pre_trip")
        self.assertTrue(props["trip_pending_activation"])
        self.assertNotIn("trip_id", props)


if __name__ == "__main__":
    unittest.main()
