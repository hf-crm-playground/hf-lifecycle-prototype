"""End-to-end: the CLI reads the example CSVs and writes snapshots, trips
and Klaviyo profile-import bodies."""

from __future__ import annotations

import csv
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from trip_state.cli import main  # noqa: E402

EX = os.path.join(os.path.dirname(__file__), "..", "..", "examples")


class CliTests(unittest.TestCase):

    def test_examples_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            snaps = os.path.join(tmp, "snapshots.csv")
            trips = os.path.join(tmp, "trips.csv")
            kl = os.path.join(tmp, "klaviyo.jsonl")
            rc = main([
                "--customers", os.path.join(EX, "customers.csv"),
                "--orders", os.path.join(EX, "orders.csv"),
                "--activations", os.path.join(EX, "activations.csv"),
                "--usage", os.path.join(EX, "usage_daily.csv"),
                "--entitlements", os.path.join(EX, "entitlements.csv"),
                "--as-of", "2026-09-23T10:00:00Z",
                "--usage-as-of", "2026-09-23T06:00:00Z",
                "--snapshots-out", snaps, "--trips-out", trips, "--klaviyo-out", kl,
            ])
            self.assertEqual(rc, 0)
            rows = list(csv.DictReader(open(snaps, encoding="utf-8")))
            self.assertEqual(len(rows), 16)
            by_id = {r["customer_id"]: r for r in rows}
            self.assertEqual(by_id["cust_03_test_install"]["trip_state"], "pre_trip")
            self.assertEqual(by_id["cust_03_test_install"]["test_install_detected"], "True")
            self.assertEqual(by_id["cust_04_in_trip"]["trip_state"], "in_trip")
            self.assertEqual(by_id["cust_08_post_trip"]["trip_state"], "post_trip")
            self.assertEqual(by_id["cust_12_refunded"]["trip_state"], "no_trip")
            trip_rows = list(csv.DictReader(open(trips, encoding="utf-8")))
            self.assertGreaterEqual(len(trip_rows), 12)
            bodies = [json.loads(l) for l in open(kl, encoding="utf-8")]
            self.assertEqual(len(bodies), 16)
            self.assertEqual(bodies[0]["data"]["type"], "profile")
            self.assertIn("trip_state", bodies[0]["data"]["attributes"]["properties"])


if __name__ == "__main__":
    unittest.main()
