-- Trip Decision Service: decision log, cap ledger, learning tables and job state (SQLite).
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
