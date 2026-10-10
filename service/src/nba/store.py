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
  dispatch_mode          TEXT,            -- offline | dry_run | live, once dispatched
  policy_mode            TEXT,            -- rules (fixed rank) | learner (Thompson sampling)
  model_version          TEXT,            -- learner model used, NULL under rules
  propensity             REAL,            -- P(this action and channel | options, context) under the logging policy
  control_share          REAL,            -- P(action-level control) at this decision
  explored               INTEGER,         -- 1 when the choice differs from the model's greedy choice
  options                TEXT,            -- JSON: every option the policy could pick, with its propensity
  cell                   TEXT,            -- context cell the learner used
  feature_version        TEXT,
  feature_vector         TEXT             -- JSON features (versioned schema, no PII)
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

-- Consent, locale, timezone and predictions per Klaviyo profile (no email or phone).
CREATE TABLE IF NOT EXISTS profile_signals (
  profile_id        TEXT PRIMARY KEY,
  customer_id       TEXT,
  locale            TEXT,
  timezone          TEXT,
  country           TEXT,
  can_email         INTEGER,
  can_push          INTEGER,
  can_whatsapp      INTEGER,
  predicted_clv     REAL,
  churn_probability REAL,
  updated_at        TEXT
);

-- Behavioural and conversion events, whitelisted keys only.
CREATE TABLE IF NOT EXISTS customer_events (
  event_id    TEXT PRIMARY KEY,
  customer_id TEXT NOT NULL,
  profile_id  TEXT,
  metric      TEXT NOT NULL,
  occurred_at TEXT NOT NULL,
  value       REAL,
  attrs       TEXT
);
CREATE INDEX IF NOT EXISTS ix_ce_customer ON customer_events (customer_id, metric, occurred_at);

-- One reward row per randomised decision once its window has closed.
CREATE TABLE IF NOT EXISTS rewards (
  decision_id   TEXT PRIMARY KEY,
  customer_id   TEXT NOT NULL,
  action        TEXT NOT NULL,
  channel       TEXT,
  arm           TEXT NOT NULL,     -- treated | control
  cell          TEXT,
  propensity    REAL,
  model_version TEXT,
  decided_at    TEXT NOT NULL,
  converted     INTEGER NOT NULL,  -- the action's primary outcome in its window
  revenue       REAL NOT NULL,     -- net revenue in the common reward horizon
  unsubscribed  INTEGER NOT NULL,
  spam          INTEGER NOT NULL,
  reward        REAL NOT NULL,     -- revenue - c_unsub*unsub - c_spam*spam
  computed_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS models (
  version     TEXT PRIMARY KEY,
  trained_at  TEXT NOT NULL,
  n_rows      INTEGER NOT NULL,
  params      TEXT NOT NULL,      -- JSON posteriors per arm and cell
  metrics     TEXT,               -- JSON offline evaluation at training time
  active      INTEGER NOT NULL DEFAULT 0
);

-- Operational state the policy reads at run time (automatic kill switches).
CREATE TABLE IF NOT EXISTS policy_state (
  action     TEXT PRIMARY KEY,
  killed     INTEGER NOT NULL,
  reason     TEXT,
  source     TEXT NOT NULL,       -- guardrail | operator
  changed_at TEXT NOT NULL
);

-- Last trip snapshot per customer, for real-time decisions between hourly runs.
CREATE TABLE IF NOT EXISTS snapshots (
  customer_id TEXT PRIMARY KEY,
  as_of       TEXT NOT NULL,
  snapshot    TEXT NOT NULL,
  declared    TEXT,
  signals     TEXT                -- the customer's signals at the last hourly run (no PII)
);

CREATE TABLE IF NOT EXISTS job_runs (
  run_id      TEXT PRIMARY KEY,
  job         TEXT NOT NULL,
  started_at  TEXT NOT NULL,
  finished_at TEXT,
  status      TEXT NOT NULL,      -- running | ok | failed | skipped
  stats       TEXT,
  error       TEXT
);

CREATE TABLE IF NOT EXISTS locks (
  name        TEXT PRIMARY KEY,
  owner       TEXT NOT NULL,
  acquired_at TEXT NOT NULL,
  expires_at  TEXT NOT NULL
);
"""

ACTIVE_STATUSES = ("dispatched",)
LOG_COLUMNS = (
    ("dispatch_mode", "TEXT"), ("policy_mode", "TEXT"), ("model_version", "TEXT"), ("propensity", "REAL"),
    ("control_share", "REAL"), ("explored", "INTEGER"), ("options", "TEXT"), ("cell", "TEXT"),
    ("feature_version", "TEXT"), ("feature_vector", "TEXT"),
)
REFERRAL_ACTIONS = ("I7", "T4")


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.astimezone(timezone.utc).isoformat() if dt else None


def _dt(s: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(s) if s else None


class Store:
    def __init__(self, path: str = ":memory:"):
        self.path = path
        self.db = sqlite3.connect(path, timeout=30)
        self.db.row_factory = sqlite3.Row
        if path != ":memory:":
            self.db.execute("PRAGMA journal_mode=WAL")  # the trigger server reads while jobs write
        self.db.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        """Add decision_log columns introduced after a database was created."""
        for table, cols in (("decision_log", LOG_COLUMNS), ("snapshots", (("signals", "TEXT"),))):
            have = {r["name"] for r in self.db.execute(f"PRAGMA table_info({table})")}
            for col, typ in cols:
                if col not in have:
                    self.db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def commit(self) -> None:
        self.db.commit()

    # ----------------------------------------------------- decision log

    def get(self, decision_id: str) -> Optional[sqlite3.Row]:
        return self.db.execute("SELECT * FROM decision_log WHERE decision_id = ?", (decision_id,)).fetchone()

    def log(self, d: Decision, status: str, now: datetime) -> bool:
        """Insert once. Returns False if this decision id was already logged
        (the hour was re-run), so the caller does not dispatch twice."""
        score = d.score.as_dict() if d.score else None
        row = {
            "decision_id": d.decision_id, "customer_id": d.customer_id, "trip_id": d.trip_id,
            "decided_at": _iso(d.decided_at), "trip_phase": d.trip_phase, "destination": d.destination,
            "candidates": json.dumps([r.as_dict() for r in d.candidates]), "chosen_action": d.action,
            "content_key": d.content_key, "channel": d.channel, "outcome": d.outcome, "reason": d.reason,
            "reason_codes": json.dumps(d.reason_codes), "arm": d.arm, "chosen_candidate": d.chosen_candidate,
            "experiment_id": d.experiment_id, "cap_state": json.dumps(d.cap_state),
            "score": json.dumps(score) if score else None, "policy_version": d.policy_version,
            "override": int(d.override), "fixed_rule": int(d.fixed), "counts_toward_cap": int(d.counts_toward_cap),
            "send_at": _iso(d.send_at), "window_opens": d.window_opens, "status": status, "status_at": _iso(now),
            "released_by": None, "outcome_window_ends_at": _iso(d.outcome_window_ends_at), "sentence": d.sentence,
            "flags": json.dumps(d.flags) if d.flags else None, "features": json.dumps(d.features, default=str),
            "dispatch_mode": None, "policy_mode": d.policy_mode, "model_version": d.model_version,
            "propensity": d.propensity, "control_share": d.control_share, "explored": int(d.explored),
            "options": json.dumps(d.options) if d.options else None, "cell": d.cell,
            "feature_version": d.feature_version,
            "feature_vector": json.dumps(d.feature_vector) if d.feature_vector else None,
        }
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        cur = self.db.execute(f"INSERT OR IGNORE INTO decision_log ({cols}) VALUES ({marks})", tuple(row.values()))
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
        # Action-level control exposures start the same cooldown as a send, so a
        # control customer is one control observation per exposure, not one a day.
        for r in self.db.execute(
            "SELECT chosen_candidate, decided_at FROM decision_log WHERE customer_id = ? AND arm = 'action_control' "
            "AND decided_at >= ? AND decided_at <= ?", (customer_id, _iso(now - timedelta(days=60)), _iso(now))):
            hrs = (now - _dt(r["decided_at"])).total_seconds() / 3600
            a = r["chosen_candidate"]
            h.hours_since_action[a] = min(h.hours_since_action.get(a, hrs), hrs)
        h.fatigued_keys = sorted(k for k, n in ignored.items() if n >= fatigue_ignores)
        if last_send:
            h.hours_since_last_send = (now - last_send).total_seconds() / 3600
        return h

    # ------------------------------------------------- signals and events

    def upsert_profile_signals(self, rows: Iterable[Dict[str, Any]]) -> int:
        n = 0
        for r in rows:
            self.db.execute(
                "INSERT OR REPLACE INTO profile_signals VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (r["profile_id"], r.get("customer_id"), r.get("locale"), r.get("timezone"), r.get("country"),
                 _b(r.get("can_email")), _b(r.get("can_push")), _b(r.get("can_whatsapp")),
                 r.get("predicted_clv"), r.get("churn_probability"), r.get("updated_at")),
            )
            if r.get("customer_id") and r["customer_id"] != r["profile_id"]:
                self.db.execute("INSERT OR REPLACE INTO profile_map VALUES (?, ?)", (r["customer_id"], r["profile_id"]))
            n += 1
        self.db.commit()
        return n

    def profile_signals(self) -> Dict[str, sqlite3.Row]:
        """By customer id (external id when known, else profile id)."""
        return {(r["customer_id"] or r["profile_id"]): r for r in self.db.execute("SELECT * FROM profile_signals")}

    def add_customer_events(self, rows: Iterable[Dict[str, Any]]) -> int:
        n = 0
        for r in rows:
            cur = self.db.execute(
                "INSERT OR IGNORE INTO customer_events VALUES (?,?,?,?,?,?,?)",
                (r["event_id"], r["customer_id"], r.get("profile_id"), r["metric"], _iso(r["occurred_at"]),
                 r.get("value"), json.dumps(r.get("attrs") or {}, default=str)),
            )
            n += cur.rowcount
        self.db.commit()
        return n

    def events(self, customer_id: Optional[str] = None, metrics: Optional[Iterable[str]] = None,
               since: Optional[datetime] = None, until: Optional[datetime] = None) -> List[sqlite3.Row]:
        q, args = "SELECT * FROM customer_events WHERE 1=1", []
        if customer_id is not None:
            q += " AND customer_id = ?"
            args.append(customer_id)
        if metrics is not None:
            ms = list(metrics)
            q += f" AND metric IN ({','.join('?' for _ in ms)})"
            args += ms
        if since is not None:
            q += " AND occurred_at >= ?"
            args.append(_iso(since))
        if until is not None:
            q += " AND occurred_at < ?"
            args.append(_iso(until))
        return self.db.execute(q + " ORDER BY occurred_at", args).fetchall()

    def message_kinds(self, customer_id: str, kinds: Iterable[str], since: datetime, until: datetime) -> List[sqlite3.Row]:
        pids = self._profile_ids(customer_id)
        ks = list(kinds)
        q = (f"SELECT * FROM message_events WHERE kind IN ({','.join('?' for _ in ks)}) "
             "AND occurred_at >= ? AND occurred_at < ? AND (customer_id = ?" + (" OR profile_id = ?" if pids else "") + ")")
        return self.db.execute(q, (*ks, _iso(since), _iso(until), customer_id, *pids)).fetchall()

    # -------------------------------------------------------- snapshots

    def save_snapshots(self, snaps: Dict[str, Any], declared: Optional[Dict[str, Any]] = None,
                       signals: Optional[Dict[str, Any]] = None) -> None:
        from dataclasses import asdict

        for cid, snap in snaps.items():
            dec = (declared or {}).get(cid)
            sig = (signals or {}).get(cid)
            self.db.execute("INSERT OR REPLACE INTO snapshots VALUES (?,?,?,?,?)",
                            (cid, _iso(snap.as_of), json.dumps(asdict(snap), default=str),
                             dec.isoformat() if dec else None, json.dumps(asdict(sig)) if sig else None))
        self.db.commit()

    def load_signals(self, customer_id: str):
        from .context import CustomerSignals

        row = self.db.execute("SELECT signals FROM snapshots WHERE customer_id = ?", (customer_id,)).fetchone()
        return CustomerSignals(**json.loads(row["signals"])) if row and row["signals"] else None

    def load_snapshot(self, customer_id: str):
        from datetime import date as _date

        from trip_state.models import TripSnapshot

        row = self.db.execute("SELECT * FROM snapshots WHERE customer_id = ?", (customer_id,)).fetchone()
        if not row:
            return None, None
        d = json.loads(row["snapshot"])
        for k in ("as_of", "trip_started_at", "projected_runout_at", "plan_end_at"):
            if d.get(k):
                d[k] = datetime.fromisoformat(d[k])
        if d.get("last_trip_ended_day"):
            d["last_trip_ended_day"] = _date.fromisoformat(d["last_trip_ended_day"])
        declared = _date.fromisoformat(row["declared"]) if row["declared"] else None
        return TripSnapshot(**d), declared

    # ----------------------------------------------- rewards and models

    def upsert_reward(self, r: Dict[str, Any]) -> None:
        cols = ", ".join(r)
        self.db.execute(f"INSERT OR REPLACE INTO rewards ({cols}) VALUES ({','.join('?' for _ in r)})", tuple(r.values()))

    def rewards(self, since: Optional[datetime] = None) -> List[sqlite3.Row]:
        if since:
            return self.db.execute("SELECT * FROM rewards WHERE decided_at >= ? ORDER BY decided_at", (_iso(since),)).fetchall()
        return self.db.execute("SELECT * FROM rewards ORDER BY decided_at").fetchall()

    def save_model(self, version: str, trained_at: datetime, n_rows: int, params: Dict[str, Any],
                   metrics: Optional[Dict[str, Any]] = None, activate: bool = True) -> None:
        if activate:
            self.db.execute("UPDATE models SET active = 0")
        self.db.execute("INSERT OR REPLACE INTO models VALUES (?,?,?,?,?,?)",
                        (version, _iso(trained_at), n_rows, json.dumps(params), json.dumps(metrics or {}), int(activate)))
        self.db.commit()

    def active_model(self) -> Optional[sqlite3.Row]:
        return self.db.execute("SELECT * FROM models WHERE active = 1 ORDER BY trained_at DESC LIMIT 1").fetchone()

    def activate_model(self, version: str) -> None:
        self.db.execute("UPDATE models SET active = CASE WHEN version = ? THEN 1 ELSE 0 END", (version,))
        self.db.commit()

    # --------------------------------------------------- operational state

    def set_killed(self, action: str, killed: bool, reason: str, source: str, now: datetime) -> None:
        self.db.execute("INSERT OR REPLACE INTO policy_state VALUES (?,?,?,?,?)",
                        (action, int(killed), reason, source, _iso(now)))
        self.db.commit()

    def killed_actions(self) -> Dict[str, sqlite3.Row]:
        return {r["action"]: r for r in self.db.execute("SELECT * FROM policy_state WHERE killed = 1")}

    def start_job(self, run_id: str, job: str, now: datetime) -> None:
        self.db.execute("INSERT INTO job_runs VALUES (?,?,?,?,?,?,?)", (run_id, job, _iso(now), None, "running", None, None))
        self.db.commit()

    def finish_job(self, run_id: str, status: str, now: datetime, stats: Optional[Dict[str, Any]] = None,
                   error: Optional[str] = None) -> None:
        self.db.execute("UPDATE job_runs SET finished_at = ?, status = ?, stats = ?, error = ? WHERE run_id = ?",
                        (_iso(now), status, json.dumps(stats, default=str) if stats else None, error, run_id))
        self.db.commit()

    def job_runs(self, job: Optional[str] = None, limit: int = 20) -> List[sqlite3.Row]:
        if job:
            return self.db.execute("SELECT * FROM job_runs WHERE job = ? ORDER BY started_at DESC LIMIT ?", (job, limit)).fetchall()
        return self.db.execute("SELECT * FROM job_runs ORDER BY started_at DESC LIMIT ?", (limit,)).fetchall()

    def acquire_lock(self, name: str, owner: str, now: datetime, ttl: timedelta) -> bool:
        """At most one hourly or nightly job at a time; an expired lock is taken over."""
        self.db.execute("DELETE FROM locks WHERE name = ? AND expires_at < ?", (name, _iso(now)))
        cur = self.db.execute("INSERT OR IGNORE INTO locks VALUES (?,?,?,?)", (name, owner, _iso(now), _iso(now + ttl)))
        self.db.commit()
        return cur.rowcount == 1

    def release_lock(self, name: str, owner: str) -> None:
        self.db.execute("DELETE FROM locks WHERE name = ? AND owner = ?", (name, owner))
        self.db.commit()


def _b(v: Any) -> Optional[int]:
    return None if v is None else int(bool(v))
