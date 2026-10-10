# Holafly Trip Decision Service

The service behind the decision console. It implements policy v1 from the **Holafly NBA System Design** (23 Sep 2026) and connects to Klaviyo. Every hour it reads each customer's trip state, picks one action (or "no message") at each decision point, logs the decision with its reasons, and activates it in Klaviyo through one `NBA Decision` event plus `nba_*` profile properties.

It is pure Python 3.10+ with no third-party dependencies. On a minimal image without a system timezone database, also run `pip install tzdata`.

```
service/
  src/trip_state/     Trip State Builder (vendored): orders, activations, usage -> trip phase + confidence
  src/nba/            Decision Service
    config.py           policy v1 numbers: cap 8, sub-caps, Tier 0 reserve, holdout, controls, windows, S(a) weights
    actions.py          the decision table: 18 ranked actions + F1, R1, R2, each with its trigger and own rules
    context.py          DecisionContext = snapshot + customer signals + contact history, frozen at decided_at
    policy.py           decide(): the pseudocode, every exit tagged with a reason code
    scoring.py          S(a) and Cap(a)
    assignment.py       stable-hash holdout (salted per quarter), action-level control, decision ids
    windows.py          local send windows; held decisions
    explain.py          nba_last_reason, under 200 characters
    store.py            SQLite: decision log, held queue, cap ledger, sync cursors
    run.py              the hourly run and held-decision re-validation
    countries.py        Klaviyo country names / SKU slugs -> ISO codes and timezones
    klaviyo/
      client.py         JSON:API client: offline | dry_run | live, rate limits, retries
      payloads.py       NBA Decision event, profile properties, bulk import jobs
      outcomes.py       message events -> cap ledger (no PII read)
      ingest.py         Klaviyo trip events -> builder inputs
      verify.py         read-only checks against the account
      setup.py          the Klaviyo build spec (dispatcher flow, segments, Phase 0 flow list)
      registry.py       verified metric and flow ids
  sql/                decision log DDL (SQLite, BigQuery with the uplift view) + builder SQL
  examples/           builder inputs and signals.csv for 16 sample customers
  docs/klaviyo_setup.md       generated build spec for the CRM team
  klaviyo_verification.json   what the live account has (ids, keys, counts; no customer values)
```

## Safety: how writes to Klaviyo are handled

The Klaviyo account is Holafly production. The client has three modes:

| Mode | Reads (GET) | Writes (events, profile imports) |
| --- | --- | --- |
| `offline` | Disabled | Appended to the outbox JSONL |
| `dry_run` (default when a key is set) | Live | Appended to the outbox JSONL, **never sent** |
| `live` | Live | Sent. Only when `--mode live` **and** `NBA_KLAVIYO_LIVE_WRITES=yes`; only to `/api/events`, `/api/event-bulk-create-jobs`, `/api/profile-import`, `/api/profile-bulk-import-jobs` |

- The service cannot create, edit or delete flows, campaigns, segments, lists or templates in any mode.
- The outbox records each request exactly as it would be sent, without the API key.
- Message events are read with `fields[profile]=external_id`, so no email address or phone number reaches the service.
- The order event, which carries addresses, is read for three fields only.

## Run it

```bash
cd service
export PYTHONPATH=src

# One hourly run on the sample customers, fully offline
python -m nba run \
  --customers examples/customers.csv --orders examples/orders.csv \
  --activations examples/activations.csv --usage examples/usage_daily.csv \
  --entitlements examples/entitlements.csv --signals examples/signals.csv \
  --as-of 2026-09-23T10:00:00Z --mode offline --decisions-out decisions.csv

python -m nba explain --customer cust_04_in_trip       # why this customer got what they got
python -m nba setup-spec --out docs/klaviyo_setup.md    # what to build in Klaviyo
```

Against the live account (read-only):

```bash
export KLAVIYO_API_KEY=pk_...        # private key, read scopes: accounts, events, flows, metrics, profiles
python -m nba verify-klaviyo --out verification.json
python -m nba pull-outcomes --lookback-days 60      # Received/Opened/Clicked/... -> cap ledger
python -m nba run --from-klaviyo --usage usage_daily.csv --signals signals.csv --mode dry_run
```

`--from-klaviyo` reads orders, activations and plan end dates from Klaviyo events. Daily usage is not in Klaviyo (gap 2) and still comes from the warehouse CSV.

Schedule: `pull-outcomes` nightly (and for the last few hours before each run), `run` hourly. `release-held` re-validates held decisions whenever their send window opens; the hourly `run` does the same.

## Tests

```bash
python -m unittest discover -s tests/trip_state     # 24 builder tests
python -m unittest discover -s tests/nba            # 71 service tests
```

The service tests cover:

- every reason code, the cap and sub-caps, the Tier 0 reserve, recency, fatigue and capture budgets;
- fixed rules, manual overrides and uplift ranking;
- send windows across midnight and daylight-saving changes;
- hash parity with the console's `stableHash`;
- retries on 429 (honouring Retry-After) and on 5xx, the rate limiter, and the dry-run and live guards;
- payload shapes and bulk chunking, ledger pulls, ingest, and verification;
- an end-to-end run from the builder examples, through hourly re-runs and a held decision, to the outbox and the decision log.

## How a decision is made

The checks run in this order:

1. operator exclude
2. global holdout (10%, salted per quarter)
3. open care conversation
4. refund
5. 30-day cap

Then, for each action in the table:

1. its trigger and its own rules
2. kill switch
3. data gap
4. demotion
5. trip confidence
6. Tier 0 reserve
7. same-action cooldown, which lasts as long as the action's outcome window
8. recency gap (24, 48 or 72 hours by tier)
9. content fatigue (ignored twice in 60 days)
10. capture sub-budget
11. channel and sub-caps
12. S(a)

After that come the manual action, the fixed rules (F1, R1), ranking (tier, then rank, or tier, then S(a) > 0), the action-level control (10% per customer, action and trip) and the send window.

Each decision-log row records:

- every action considered and why it was or was not chosen;
- the cap state, the arm, the policy version and the features frozen at `decided_at`;
- the outcome window used for measurement.

## Mapping from Klaviyo data (verified 10 Oct 2026)

- **eSIM key.** eSIMs are keyed by `order_id`, because `activation_event` has no `esim_id`.
- **Plan end date.** `expiration_date_eSIM_v2` provides `plan_end_at` for each order.
- **Country names.** English names and `esim-<country>` slugs are mapped to ISO codes. Names that can't be mapped are counted and reported, never guessed.
- **Profile addressing.** Profiles are addressed by Klaviyo profile id. Only 8 of 50 recently updated profiles had an `external_id`, and in two formats.
- **Care signals.** There are no Intercom metrics in Klaviyo, so `CARE_OPEN` needs an Intercom or warehouse feed.
- **Open question.** It is not yet confirmed whether the order event's `order_number` matches the `order_id` used by the activation and expiration events. If they differ, pre-trip orders without an activation won't join.

See `klaviyo_verification.json` for the full list of findings and `docs/klaviyo_setup.md` for what the CRM team builds:

- the NBA Dispatcher flow;
- the holdout, in-app and capture segments;
- the Phase 0 list of flows to pause or gate.
