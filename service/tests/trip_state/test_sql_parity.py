"""The warehouse SQL must produce the same trips and states as the Python
builder. Runs sql/trip_sessions_sqlite.sql in SQLite against every scenario
that has usage and compares trip boundaries, confirmation, state and
confidence for the latest trip."""

from __future__ import annotations

import os
import sqlite3
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

from trip_state import build_trip_state  # noqa: E402
from trip_state.builder import STATE_IN_TRIP, STATE_END_PROVISIONAL  # noqa: E402
import fixtures as fx  # noqa: E402

SQL_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "sql", "trip_sessions_sqlite.sql")

# Scenarios where the Python state is driven by usage (the SQL covers
# exactly these; pre-trip and pending logic is Python-only by design).
PARITY_SCENARIOS = [
    "in_trip", "test_then_real_trip", "provisional_end", "reopened", "post_trip",
    "between_trips", "multi_esim_one_trip", "refunded", "plan_ended_usage_stopped",
    "long_trip", "unknown_country_big", "unknown_country_small", "two_trips",
    "test_install",
]


def load_sqlite(conn: sqlite3.Connection, scenarios) -> None:
    conn.executescript(
        """
        CREATE TABLE customers(customer_id TEXT, home_country TEXT);
        CREATE TABLE orders(customer_id TEXT, esim_id TEXT, status TEXT);
        CREATE TABLE usage_daily(customer_id TEXT, esim_id TEXT, day TEXT,
                                 mb_used REAL, network_country TEXT);
        """
    )
    for name in scenarios:
        c, orders, _, usage, _ = fx.ALL_SCENARIOS[name]()
        cid = name  # one customer per scenario
        conn.execute("INSERT INTO customers VALUES (?, ?)", (cid, c.home_country))
        for o in orders:
            conn.execute("INSERT INTO orders VALUES (?, ?, ?)", (cid, o.esim_id, o.status))
        for u in usage:
            conn.execute(
                "INSERT INTO usage_daily VALUES (?, ?, ?, ?, ?)",
                (cid, u.esim_id, u.day.isoformat(), u.mb_used, u.network_country),
            )
    conn.commit()


class SqlParityTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.conn = sqlite3.connect(":memory:")
        cls.conn.row_factory = sqlite3.Row
        load_sqlite(cls.conn, PARITY_SCENARIOS)
        with open(SQL_PATH, encoding="utf-8") as fh:
            sql = fh.read()
        cls.rows = cls.conn.execute(sql, {"as_of_day": fx.TODAY.isoformat()}).fetchall()

    def _sql_trips(self, cid):
        return [r for r in self.rows if r["customer_id"] == cid]

    def test_every_scenario_matches(self):
        for name in PARITY_SCENARIOS:
            with self.subTest(scenario=name):
                c, o, a, u, e = fx.ALL_SCENARIOS[name]()
                snap, trips = build_trip_state(c, o, a, u, e, as_of=fx.AS_OF)
                sql_trips = self._sql_trips(name)
                self.assertEqual(len(sql_trips), len(trips), "trip count")
                for py, row in zip(trips, sql_trips):
                    self.assertEqual(row["first_usage_day"], py.first_usage_day.isoformat())
                    self.assertEqual(row["last_usage_day"], py.last_usage_day.isoformat())
                    self.assertEqual(row["provisional_end_day"], py.provisional_end_day.isoformat())
                    self.assertEqual(bool(row["confirmed"]), py.confirmed)
                    self.assertEqual(row["usage_days"], py.usage_days)
                    self.assertAlmostEqual(row["mb_used_total"], py.mb_used_total, places=3)
                if trips:
                    latest = [r for r in sql_trips if r["is_latest"]][0]
                    # Python may override with pre_trip when an order is pending;
                    # these scenarios have none, so the usage-driven state must match.
                    self.assertEqual(latest["state_if_latest"], snap.trip_state)
                    if snap.trip_state in (STATE_IN_TRIP, STATE_END_PROVISIONAL):
                        self.assertEqual(latest["trip_confidence"], snap.trip_confidence)
                    self.assertEqual(latest["days_since_last_usage"], snap.days_since_last_usage)


if __name__ == "__main__":
    unittest.main()
