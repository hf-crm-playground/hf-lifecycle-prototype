-- Trip Decision Service tables for BigQuery (analysis copy of the SQLite store).
-- Replace `holafly_nba` with the dataset. Partitioned by decision date and
-- clustered by customer so per-customer audits and daily reads stay cheap.

CREATE TABLE IF NOT EXISTS `holafly_nba.decision_log` (
  decision_id            STRING NOT NULL,
  customer_id            STRING NOT NULL,
  trip_id                STRING,
  decided_at             TIMESTAMP NOT NULL,
  trip_phase             STRING NOT NULL,
  destination            STRING,
  candidates             JSON NOT NULL,     -- every considered action: status, reason, channel, S(a)
  chosen_action          STRING,            -- NULL = no message
  content_key            STRING,
  channel                STRING,
  outcome                STRING NOT NULL,   -- send | none
  reason                 STRING,
  reason_codes           ARRAY<STRING>,
  arm                    STRING NOT NULL,   -- treatment | holdout | action_control
  chosen_candidate       STRING,
  experiment_id          STRING,
  cap_state              JSON NOT NULL,
  score                  JSON,
  policy_version         STRING NOT NULL,
  override               BOOL NOT NULL,
  fixed_rule             BOOL NOT NULL,
  counts_toward_cap      BOOL NOT NULL,
  send_at                TIMESTAMP,
  window_opens           STRING,
  status                 STRING NOT NULL,   -- logged | held | dispatched | released | superseded | expired
  status_at              TIMESTAMP NOT NULL,
  released_by            STRING,
  outcome_window_ends_at TIMESTAMP,
  sentence               STRING NOT NULL,
  flags                  JSON,
  features               JSON NOT NULL,     -- context frozen at decided_at (no PII)
  dispatch_mode          STRING
)
PARTITION BY DATE(decided_at)
CLUSTER BY customer_id, chosen_action;

CREATE TABLE IF NOT EXISTS `holafly_nba.message_events` (
  event_id      STRING NOT NULL,
  customer_id   STRING,
  profile_id    STRING NOT NULL,
  kind          STRING NOT NULL,   -- received | opened | clicked | skipped | bounced | unsubscribed | spam
  channel       STRING NOT NULL,
  occurred_at   TIMESTAMP NOT NULL,
  flow_id       STRING,
  message_id    STRING,
  message_type  STRING,
  transactional BOOL NOT NULL,
  nba           BOOL NOT NULL
)
PARTITION BY DATE(occurred_at)
CLUSTER BY customer_id, kind;

-- Per-action uplift read (randomised rows only: treatment sends vs action-level controls).
-- One row per customer, action and trip: the first exposure, since control
-- assignment is fixed per (customer, action, trip) and a control customer is
-- re-logged while the decision point lasts. Join outcomes on customer_id
-- within [decided_at, outcome_window_ends_at).
CREATE OR REPLACE VIEW `holafly_nba.action_randomised_rows` AS
SELECT
  decision_id, customer_id, trip_id, decided_at, outcome_window_ends_at,
  COALESCE(chosen_action, chosen_candidate) AS action_id,
  IF(arm = 'action_control', 'control', 'treated') AS arm,
  policy_version
FROM `holafly_nba.decision_log`
WHERE NOT override
  AND NOT fixed_rule
  AND ((outcome = 'send' AND status = 'dispatched') OR arm = 'action_control')  -- a released held row is replaced by the row that dispatched it
QUALIFY ROW_NUMBER() OVER (
  PARTITION BY customer_id, COALESCE(chosen_action, chosen_candidate), COALESCE(trip_id, 'none')
  ORDER BY decided_at) = 1;
