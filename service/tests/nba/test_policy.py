import unittest
from dataclasses import replace
from datetime import datetime, timezone

from helpers import NOW, ctx, customer_id, history

from nba.config import DEFAULT_POLICY
from nba.policy import decide

LOW_DATA = dict(data_remaining_pct=15.0)


def why(d, aid):
    return next((r.why for r in d.candidates if r.id == aid), "not considered")


class GlobalExits(unittest.TestCase):
    def test_nothing_due_is_no_eligible_action(self):
        d = decide(ctx())
        self.assertEqual((d.outcome, d.reason), ("none", "NO_ELIGIBLE_ACTION"))
        self.assertEqual(d.candidates, [])

    def test_holdout(self):
        d = decide(ctx(cid=customer_id(holdout=True), **LOW_DATA))
        self.assertEqual((d.outcome, d.reason, d.arm), ("none", "HOLDOUT", "holdout"))
        self.assertEqual(d.experiment_id, "GLOBAL_HOLDOUT")

    def test_operator_exclude_care_refund(self):
        for sig, code in (({"nba_exclude": True}, "OPERATOR_EXCLUDE"),
                          ({"care_open_72h": True}, "CARE_OPEN"),
                          ({"refunded": True}, "REFUNDED")):
            with self.subTest(code=code):
                d = decide(ctx(signals=sig, **LOW_DATA))
                self.assertEqual((d.outcome, d.reason), ("none", code))

    def test_cap_reached(self):
        d = decide(ctx(history=history(sends={"total": 8, "email": 5, "push": 3}), **LOW_DATA))
        self.assertEqual(d.reason, "CAP_REACHED")
        self.assertIn("8 sends already this month", d.sentence)

    def test_market_cap(self):
        p = replace(DEFAULT_POLICY, market_cap=(("ES", 5),))
        d = decide(ctx(history=history(sends={"total": 5, "email": 5}), **LOW_DATA), p)
        self.assertEqual(d.reason, "CAP_REACHED")


class Tier0(unittest.TestCase):
    def test_i1_low_data_by_push(self):
        d = decide(ctx(**LOW_DATA))
        self.assertEqual((d.outcome, d.action, d.channel), ("send", "I1", "push"))
        self.assertFalse(d.held)
        self.assertEqual(d.experiment_id, "E1")
        self.assertTrue(d.counts_toward_cap)

    def test_i1_on_projected_runout(self):
        d = decide(ctx(runout_before_plan_end=True, projected_runout_days=2))
        self.assertEqual(d.action, "I1")

    def test_usage_stale(self):
        d = decide(ctx(usage_fresh=False, **LOW_DATA))
        self.assertEqual(why(d, "I1"), "USAGE_STALE")
        self.assertEqual(d.reason, "NO_ELIGIBLE_ACTION")

    def test_plan_ending_hands_over_to_i2(self):
        d = decide(ctx(plan_end_hours=20, **LOW_DATA))
        self.assertEqual(why(d, "I1"), "PLAN_ENDING_I2_INSTEAD")
        self.assertEqual(d.action, "I2")

    def test_low_trip_confidence(self):
        d = decide(ctx(trip_confidence="low", **LOW_DATA))
        self.assertEqual(why(d, "I1"), "LOW_TRIP_CONFIDENCE")

    def test_i6_blocked_by_data_gap_until_v2(self):
        d = decide(ctx(network_outside_plan=True))
        self.assertEqual(why(d, "I6"), "DATA_GAP")
        d2 = decide(ctx(network_outside_plan=True), replace(DEFAULT_POLICY, v2_network_events=True))
        self.assertEqual(d2.action, "I6")


class Budget(unittest.TestCase):
    def test_reserve_holds_last_two_for_tier0(self):
        h = history(sends={"total": 6, "email": 4, "push": 2})
        d = decide(ctx(history=h, signals={"success_moment": True}))
        self.assertEqual(why(d, "I7"), "BUDGET_RESERVED_FOR_TIER0")
        d2 = decide(ctx(history=h, signals={"success_moment": True}, **LOW_DATA))
        self.assertEqual(d2.action, "I1")

    def test_reserve_does_not_apply_post_trip(self):
        h = history(sends={"total": 6, "email": 6})
        d = decide(ctx(phase="post_trip", days_after_confirm=5, history=h,
                       signals={"consent_email": True}))
        self.assertNotEqual(why(d, "T2"), "BUDGET_RESERVED_FOR_TIER0")

    def test_recency_gap_by_tier(self):
        h = history(hours_since_last_send=30)
        d = decide(ctx(history=h, signals={"success_moment": True}))
        self.assertEqual(why(d, "I7"), "RECENCY_GAP")  # tier 3 needs 72 h
        d2 = decide(ctx(history=h, **LOW_DATA))
        self.assertEqual(d2.action, "I1")  # tier 0 needs 24 h

    def test_same_action_cooldown_covers_uncapped_channels(self):
        d = decide(ctx(phase="between_trips", days_since_trip_end=30, history=history(hours_since_action={"C1": 5}),
                       signals={"intent_24h": True, "paid_share": 0.45}))
        self.assertEqual(why(d, "C1"), "ACTION_SUPPRESSED")
        d2 = decide(ctx(history=history(hours_since_action={"I1": 10}), **LOW_DATA))
        self.assertEqual(why(d2, "I1"), "ACTION_SUPPRESSED")

    def test_content_fatigue(self):
        d = decide(ctx(history=history(fatigued_keys=["topup_runout"]), **LOW_DATA))
        self.assertEqual(why(d, "I1"), "CONTENT_FATIGUE")

    def test_subcap_moves_to_next_channel(self):
        d = decide(ctx(history=history(sends={"total": 4, "push": 4}), **LOW_DATA))
        self.assertEqual((d.action, d.channel), ("I1", "email"))

    def test_no_deliverable_channel(self):
        sig = {"consent_email": False, "app_installed": False, "push_token": False, "in_app_session_14d": False}
        d = decide(ctx(signals=sig, **LOW_DATA))
        self.assertEqual((d.outcome, d.reason, d.chosen_candidate), ("none", "NO_DELIVERABLE_CHANNEL", "I1"))

    def test_whatsapp_never_in_blocked_markets(self):
        sig = {"consent_email": False, "app_installed": False, "push_token": False, "consent_whatsapp": True}
        es = decide(ctx(signals=sig, **LOW_DATA))
        self.assertEqual(es.channel, "whatsapp")
        us = decide(ctx(signals={**sig, "market": "US"}, market="US", **LOW_DATA))
        self.assertEqual(us.reason, "NO_DELIVERABLE_CHANNEL")

    def test_push_needs_known_timezone_outside_trip(self):
        sig = {"hours_since_order": 130, "consent_email": False}
        d = decide(ctx(phase="pre_trip", trip_confidence="low", tz_source="market", signals=sig))
        self.assertEqual(d.reason, "NO_DELIVERABLE_CHANNEL")
        d2 = decide(ctx(phase="pre_trip", trip_confidence="low", tz_source="profile", signals=sig))
        self.assertEqual((d2.action, d2.channel), ("P1", "push"))


class RankingAndControls(unittest.TestCase):
    def test_tier_order_and_outranked(self):
        d = decide(ctx(signals={"success_moment": True}, **LOW_DATA))
        self.assertEqual(d.action, "I1")
        self.assertEqual(why(d, "I7"), "OUTRANKED")

    def test_action_control(self):
        cid = customer_id(control_for="I1")
        d = decide(ctx(cid=cid, **LOW_DATA))
        self.assertEqual((d.outcome, d.reason, d.arm, d.chosen_candidate), ("none", "ACTION_CONTROL", "action_control", "I1"))
        self.assertEqual(d.experiment_id, "E1")
        self.assertIsNotNone(d.outcome_window_ends_at)

    def test_control_is_per_trip(self):
        cid = customer_id(control_for="I1", trip_id="trip-1")
        arms = {decide(ctx(cid=cid, trip_id=f"trip-{i}", **LOW_DATA)).arm for i in range(1, 40)}
        self.assertEqual(arms, {"treatment", "action_control"})

    def test_kill_switch_and_demotion(self):
        k = decide(ctx(**LOW_DATA), replace(DEFAULT_POLICY, killed=frozenset({"I1"})))
        self.assertEqual(why(k, "I1"), "KILL_SWITCH")
        m = decide(ctx(**LOW_DATA), replace(DEFAULT_POLICY, demoted=frozenset({"I1"})))
        self.assertEqual(why(m, "I1"), "DEMOTED")

    def test_manual_action_overrides_ranking(self):
        d = decide(ctx(signals={"success_moment": True, "nba_manual_action": "I7"}, **LOW_DATA))
        self.assertEqual((d.action, d.reason, d.override), ("I7", "MANUAL_ACTION", True))
        self.assertEqual(why(d, "I1"), "OUTRANKED")

    def test_uplift_mode_sends_only_positive_scores(self):
        p = replace(DEFAULT_POLICY, ranking="uplift")
        sig = {"in_app_session_14d": False}  # I5 by push: tiny uplift minus the send's shadow price
        d = decide(ctx(days_in_trip=4, signals=sig), p)
        self.assertEqual((d.outcome, d.reason), ("none", "SCORE_NOT_POSITIVE"))
        d2 = decide(ctx(**LOW_DATA), p)
        self.assertEqual(d2.action, "I1")
        self.assertGreater(d2.score.total, 0)


class CaptureAndFixed(unittest.TestCase):
    def between(self, **sig):
        base = {"intent_24h": True, "paid_share": 0.45, "p0_buy": 0.6}
        base.update(sig)
        return ctx(phase="between_trips", trip_confidence="high", days_since_trip_end=30, signals=base)

    def test_c1_capture_only_via_native_flow(self):
        d = decide(self.between())
        self.assertEqual((d.action, d.channel, d.reason), ("C1", "native_flow", "CAPTURE_ONLY"))
        self.assertFalse(d.counts_toward_cap)
        self.assertGreater(d.score.cap, 0)

    def test_capture_budget_and_paid_share(self):
        d = decide(ctx(phase="between_trips", days_since_trip_end=30, history=history(capture_sends_30d=2),
                       signals={"intent_24h": True, "paid_share": 0.45}))
        self.assertEqual(why(d, "C1"), "CAPTURE_BUDGET_SPENT")
        d2 = decide(self.between(paid_share=0.1))
        self.assertEqual(why(d2, "C1"), "PAID_SHARE_BELOW_30")

    def test_f1_at_provisional_trip_end(self):
        d = decide(ctx(phase="trip_end_provisional", days_since_last_usage=15))
        self.assertEqual((d.action, d.reason, d.tier, d.channel), ("F1", "FIXED_RULE", "fixed", "in_app"))
        self.assertFalse(d.counts_toward_cap)  # in-app costs no send budget
        no_app = {"app_installed": False, "in_app_session_14d": False}
        d2 = decide(ctx(phase="trip_end_provisional", days_since_last_usage=15, signals=no_app))
        self.assertEqual((d2.action, d2.channel), ("F1", "email"))
        self.assertTrue(d2.counts_toward_cap)

    def test_f1_once_per_trip(self):
        d = decide(ctx(phase="trip_end_provisional", history=history(feedback_sent_this_trip=True)))
        self.assertNotEqual(d.action, "F1")

    def test_r1_review_after_confirmed_end(self):
        d = decide(ctx(phase="post_trip", days_after_confirm=3))
        self.assertEqual(d.action, "R1")
        d2 = decide(ctx(phase="post_trip", days_after_confirm=3, history=history(review_invited_this_trip=True)))
        self.assertNotEqual(d2.action, "R1")

    def test_r2_flag_costs_no_budget(self):
        d = decide(ctx(signals={"success_moment": True}))
        self.assertTrue(d.flags.get("rating_prompt_allowed"))


class WindowsAndText(unittest.TestCase):
    def test_email_outside_window_is_held_until_local_open(self):
        early = datetime(2026, 10, 9, 3, 0, tzinfo=timezone.utc)  # 05:00 in Madrid
        d = decide(ctx(phase="post_trip", days_after_confirm=12, decided_at=early, local_tz="Europe/Madrid",
                       tz_source="profile", signals={"order_last_30d": False}))
        self.assertEqual((d.action, d.channel), ("T2", "email"))
        self.assertTrue(d.held)
        self.assertEqual(d.send_at, datetime(2026, 10, 9, 7, 0, tzinfo=timezone.utc))  # 09:00 CEST
        self.assertIn("09:00", d.window_opens)

    def test_tier0_window_is_wider(self):
        t = datetime(2026, 10, 9, 5, 30, tzinfo=timezone.utc)  # 07:30 Madrid
        self.assertTrue(decide(ctx(decided_at=t, **LOW_DATA)).held)
        t2 = datetime(2026, 10, 9, 6, 30, tzinfo=timezone.utc)  # 08:30 Madrid: Tier 0 opens at 08
        self.assertFalse(decide(ctx(decided_at=t2, **LOW_DATA)).held)

    def test_in_app_has_no_window(self):
        t = datetime(2026, 10, 9, 1, 0, tzinfo=timezone.utc)
        d = decide(ctx(decided_at=t, signals={"success_moment": True}))
        self.assertEqual(d.channel, "in_app")
        self.assertFalse(d.held)

    def test_sentence_is_short_and_explains(self):
        long = ctx(signals={"success_moment": True}, history=history(sends={"total": 6, "push": 4, "email": 2},
                   hours_since_last_send=30, fatigued_keys=["topup_runout"]), **LOW_DATA)
        for c in (ctx(), ctx(**LOW_DATA), long, ctx(cid=customer_id(holdout=True))):
            s = decide(c).sentence
            self.assertLessEqual(len(s), 200)
            self.assertIn("Budget", s)

    def test_deterministic(self):
        a, b = decide(ctx(**LOW_DATA)), decide(ctx(**LOW_DATA))
        self.assertEqual((a.decision_id, a.action, a.sentence), (b.decision_id, b.action, b.sentence))


if __name__ == "__main__":
    unittest.main()
