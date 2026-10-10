-- Trip Decision Service: decision log, cap ledger and sync state (SQLite).
-- Generated from nba/store.py SCHEMA; a test keeps the two identical.
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
