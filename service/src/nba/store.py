"""SQLite store: the decision log, the held-decision queue, the cap ledger
(message events pulled from Klaviyo) and sync cursors.

SQLite keeps the service runnable anywhere with no infrastructure; the same
tables exist in BigQuery (sql/decision_log_bigquery.sql) for analysis. The
decision log is append-only except for `status`, which moves
logged | held -> dispatched | released | superseded | expired. A held row
that is released is replaced by the new row that dispatched it (released_by).
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .context import ContactHistory
from .policy import Decision

SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "sql", "decision_log_sqlite.sql")

SCHEMA = """
CREATE TABLE IF NOT EXISTS decision_log (
  decision_id            TEXT PRIMARY KEY,
  customer_id            TEXT NOT NULL,
  trip_id                TEXT,
  decided_at             TEXT NOT NULL,
  trip_phase             TEXT NOT NULL,
  destination            TEXT,
  candidates             TEXT NOT NULL,   -- JSON: every considered action with status, reason, channel, S(a)
  chosen_action          TEXT,            -- NULL = no message
  content_key            TEXT,
  channel                TEXT,
  outcome                TEXT NOT NULL,   -- send | none
  reason                 TEXT,            -- primary reason code
  reason_codes           TEXT NOT NULL,   -- JSON list
  arm                    TEXT NOT NULL,   -- treatment | holdout | action_control
  chosen_candidate       TEXT,            -- action that would have been sent (control, no channel)
  experiment_id          TEXT,
  cap_state              TEXT NOT NULL,   -- JSON: used, cap, left, per channel
  score                  TEXT,            -- JSON S(a) breakdown of the chosen action
  policy_version         TEXT NOT NULL,
  override               INTEGER NOT NULL DEFAULT 0,
  fixed_rule             INTEGER NOT NULL DEFAULT 0,
  counts_toward_cap      INTEGER NOT NULL DEFAULT 0,
  send_at                TEXT,
  window_opens           TEXT,
  status                 TEXT NOT NULL,   -- logged | held | dispatched | released | superseded | expired
  status_at              TEXT NOT NULL,
  released_by            TEXT,            -- decision_id that re-validated a held decision
  outcome_window_ends_at TEXT,
  sentence               TEXT NOT NULL,
  flags                  TEXT,            -- JSON, e.g. rating_prompt_allowed
  features               TEXT NOT NULL,   -- JSON snapshot of the context at decided_at
  dispatch_mode          TEXT             -- offline | dry_run | live, once dispatched
);
CREATE INDEX IF NOT EXISTS ix_dl_customer ON decision_log (customer_id, decided_at);
CREATE INDEX IF NOT EXISTS ix_dl_status ON decision_log (status, send_at);

CREATE TABLE IF NOT EXISTS message_events (
  event_id      TEXT PRIMARY KEY,         -- Klaviyo event id
  customer_id   TEXT,                     -- external_id when the profile has one
  profile_id    TEXT NOT NULL,            -- Klaviyo profile id
  kind          TEXT NOT NULL,            -- received | opened | clicked | skipped | bounced | unsubscribed | spam
  channel       TEXT NOT NULL,            -- email | push | whatsapp | sms
  occurred_at   TEXT NOT NULL,
  flow_id       TEXT,
  message_id    TEXT,
  message_type  TEXT,                     -- flow | campaign
  transactional INTEGER NOT NULL DEFAULT 0,
  nba           INTEGER NOT NULL DEFAULT 0  -- sent by the NBA Dispatcher flow
);
CREATE INDEX IF NOT EXISTS ix_me_customer ON message_events (customer_id, occurred_at);
CREATE INDEX IF NOT EXISTS ix_me_profile ON message_events (profile_id, occurred_at);

CREATE TABLE IF NOT EXISTS sync_state (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS profile_map (
  customer_id TEXT PRIMARY KEY,
  profile_id  TEXT NOT NULL
);
"""

ACTIVE_STATUSES = ("dispatched",)
REFERRAL_ACTIONS = ("I7", "T4")


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.astimezone(timezone.utc).isoformat() if dt else None


def _dt(s: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(s) if s else None


class Store:
    def __init__(self, path: str = ":memory:"):
        self.path = path
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    # ----------------------------------------------------- decision log

    def get(self, decision_id: str) -> Optional[sqlite3.Row]:
        return self.db.execute("SELECT * FROM decision_log WHERE decision_id = ?", (decision_id,)).fetchone()

    def log(self, d: Decision, status: str, now: datetime) -> bool:
        """Insert once. Returns False if this decision id was already logged
        (the hour was re-run), so the caller does not dispatch twice."""
        score = d.score.as_dict() if d.score else None
        cur = self.db.execute(
            """INSERT OR IGNORE INTO decision_log VALUES
               (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                d.decision_id, d.customer_id, d.trip_id, _iso(d.decided_at), d.trip_phase, d.destination,
                json.dumps([r.as_dict() for r in d.candidates]), d.action, d.content_key, d.channel,
                d.outcome, d.reason, json.dumps(d.reason_codes), d.arm, d.chosen_candidate, d.experiment_id,
                json.dumps(d.cap_state), json.dumps(score) if score else None, d.policy_version,
                int(d.override), int(d.fixed), int(d.counts_toward_cap), _iso(d.send_at), d.window_opens,
                status, _iso(now), None, _iso(d.outcome_window_ends_at), d.sentence,
                json.dumps(d.flags) if d.flags else None, json.dumps(d.features, default=str), None,
            ),
        )
        self.db.commit()
        return cur.rowcount == 1

    def set_status(self, decision_id: str, status: str, now: datetime, released_by: Optional[str] = None,
                   dispatch_mode: Optional[str] = None) -> None:
        self.db.execute(
            "UPDATE decision_log SET status = ?, status_at = ?, released_by = COALESCE(?, released_by), "
            "dispatch_mode = COALESCE(?, dispatch_mode) WHERE decision_id = ?",
            (status, _iso(now), released_by, dispatch_mode, decision_id),
        )
        self.db.commit()

    def pending_held(self, customer_id: str) -> Optional[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM decision_log WHERE customer_id = ? AND status = 'held' ORDER BY decided_at DESC LIMIT 1",
            (customer_id,),
        ).fetchone()

    def held_due(self, now: datetime) -> List[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM decision_log WHERE status = 'held' AND send_at <= ? ORDER BY send_at", (_iso(now),)
        ).fetchall()

    def last_decision(self, customer_id: str) -> Optional[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM decision_log WHERE customer_id = ? ORDER BY decided_at DESC LIMIT 1", (customer_id,)
        ).fetchone()

    def decisions(self, since: Optional[datetime] = None) -> List[sqlite3.Row]:
        if since:
            return self.db.execute(
                "SELECT * FROM decision_log WHERE decided_at >= ? ORDER BY decided_at", (_iso(since),)
            ).fetchall()
        return self.db.execute("SELECT * FROM decision_log ORDER BY decided_at").fetchall()

    # ------------------------------------------------------- cap ledger

    def add_message_events(self, rows: Iterable[Dict[str, Any]]) -> int:
        n = 0
        for r in rows:
            cur = self.db.execute(
                "INSERT OR IGNORE INTO message_events VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    r["event_id"], r.get("customer_id"), r["profile_id"], r["kind"], r["channel"],
                    _iso(r["occurred_at"]), r.get("flow_id"), r.get("message_id"), r.get("message_type"),
                    int(bool(r.get("transactional"))), int(bool(r.get("nba"))),
                ),
            )
            n += cur.rowcount
        self.db.commit()
        return n

    def map_profile(self, customer_id: str, profile_id: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO profile_map VALUES (?, ?)", (customer_id, profile_id))
        self.db.commit()

    def get_cursor(self, key: str) -> Optional[str]:
        row = self.db.execute("SELECT value FROM sync_state WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_cursor(self, key: str, value: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO sync_state VALUES (?, ?)", (key, value))
        self.db.commit()

    def _profile_ids(self, customer_id: str) -> Tuple[str, ...]:
        row = self.db.execute("SELECT profile_id FROM profile_map WHERE customer_id = ?", (customer_id,)).fetchone()
        return (row["profile_id"],) if row else ()

    def _klaviyo_sends(self, customer_id: str, since: datetime, until: datetime) -> List[sqlite3.Row]:
        """Marketing sends from flows and campaigns other than the NBA
        Dispatcher (its sends are counted from the decision log, where they
        exist the moment they are dispatched)."""
        pids = self._profile_ids(customer_id)
        q = (
            "SELECT channel, occurred_at FROM message_events WHERE kind = 'received' AND transactional = 0 AND nba = 0 "
            "AND occurred_at >= ? AND occurred_at < ? AND (customer_id = ?"
            + (" OR profile_id = ?" if pids else "") + ")"
        )
        return self.db.execute(q, (_iso(since), _iso(until), customer_id, *pids)).fetchall()

    def _engaged_after(self, customer_id: str, start: datetime, hours: int) -> bool:
        pids = self._profile_ids(customer_id)
        q = (
            "SELECT 1 FROM message_events WHERE kind IN ('opened', 'clicked') AND nba = 1 "
            "AND occurred_at >= ? AND occurred_at < ? AND (customer_id = ?"
            + (" OR profile_id = ?" if pids else "") + ") LIMIT 1"
        )
        end = start + timedelta(hours=hours)
        return self.db.execute(q, (_iso(start), _iso(end), customer_id, *pids)).fetchone() is not None

    def last_transactional_push(self, customer_id: str, until: datetime) -> Optional[datetime]:
        pids = self._profile_ids(customer_id)
        q = (
            "SELECT MAX(occurred_at) AS t FROM message_events WHERE kind = 'received' AND channel = 'push' "
            "AND transactional = 1 AND occurred_at < ? AND (customer_id = ?"
            + (" OR profile_id = ?" if pids else "") + ")"
        )
        row = self.db.execute(q, (_iso(until), customer_id, *pids)).fetchone()
        return _dt(row["t"]) if row and row["t"] else None

    def history(self, customer_id: str, now: datetime, trip_id: Optional[str],
                fatigue_days: int = 60, fatigue_ignores: int = 2, ignore_window_hours: int = 72) -> ContactHistory:
        h = ContactHistory()
        since30 = now - timedelta(days=30)
        last_send: Optional[datetime] = None

        for r in self._klaviyo_sends(customer_id, since30, now):
            ch = r["channel"] if r["channel"] in ("email", "push", "whatsapp") else "email"
            h.sends_30d[ch] = h.sends_30d.get(ch, 0) + 1
            h.sends_30d["total"] += 1
            t = _dt(r["occurred_at"])
            last_send = max(last_send, t) if last_send else t

        own = self.db.execute(
            "SELECT chosen_action, content_key, channel, send_at, trip_id, counts_toward_cap FROM decision_log "
            "WHERE customer_id = ? AND status = 'dispatched' AND send_at >= ? AND send_at <= ?",
            (customer_id, _iso(now - timedelta(days=366)), _iso(now)),
        ).fetchall()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        ignored: Dict[str, int] = {}
        for r in own:
            t = _dt(r["send_at"])
            a = r["chosen_action"]
            age = now - t
            hrs = age.total_seconds() / 3600
            h.hours_since_action[a] = min(h.hours_since_action.get(a, hrs), hrs)
            if r["counts_toward_cap"] and t >= since30:
                h.sends_30d[r["channel"]] = h.sends_30d.get(r["channel"], 0) + 1
                h.sends_30d["total"] += 1
                last_send = max(last_send, t) if last_send else t
            if a in ("C1", "C2") and t >= since30:
                h.capture_sends_30d += 1
            if a == "B1" and t >= month_start:
                h.always_on_claim_reminders_this_month += 1
            if a == "T1":
                h.always_on_prompt_30d = h.always_on_prompt_30d or t >= since30
                h.t1_last_3d = h.t1_last_3d or age <= timedelta(days=3)
            if a in REFERRAL_ACTIONS:
                h.referral_last_prompt_days = min(h.referral_last_prompt_days, age.total_seconds() / 86400)
                h.referral_prompts_12m += 1
            if a == "I7" and trip_id and r["trip_id"] == trip_id:
                h.i7_this_trip = True
            if a == "F1" and trip_id and r["trip_id"] == trip_id:
                h.feedback_sent_this_trip = True
            if a == "R1" and trip_id and r["trip_id"] == trip_id:
                h.review_invited_this_trip = True
            # Content fatigue: an NBA send nobody opened or clicked within the window.
            if (r["content_key"] and age <= timedelta(days=fatigue_days)
                    and age >= timedelta(hours=ignore_window_hours)
                    and not self._engaged_after(customer_id, t, ignore_window_hours)):
                ignored[r["content_key"]] = ignored.get(r["content_key"], 0) + 1
                if a == "P2":
                    h.p2_ignored_this_trip = True
        h.fatigued_keys = sorted(k for k, n in ignored.items() if n >= fatigue_ignores)
        if last_send:
            h.hours_since_last_send = (now - last_send).total_seconds() / 3600
        return h
