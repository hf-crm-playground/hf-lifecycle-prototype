# Holafly Trip Decision Service

The service behind the decision console. It implements policy v1 from the **Holafly NBA System Design** (23 Sep 2026), connects to Klaviyo, and runs the learning loop that moves it from fixed rules to per-customer optimisation:

- **Every hour:** reads each customer's trip state and signals, then picks one action (or "no message") at each decision point. It logs the decision with its reasons, its options and their probabilities. It then activates the decision in Klaviyo through one `NBA Decision` event and `nba_*` profile properties.
- **Every night:** attributes rewards to closed decisions and builds the uplift table from the action-level controls. It trains a model, evaluates it off-policy, promotes it only if it passes, and pauses any action that breaches a guardrail.

It is pure Python 3.10+ with no third-party dependencies. On a minimal image without a system timezone database, also run `pip install tzdata`.

```
service/
  src/trip_state/       Trip State Builder (vendored): orders, activations, usage -> trip phase + confidence
  src/nba/
    config.py             policy v1 numbers (cap 8, sub-caps, reserve, holdout, controls, windows, S(a) weights)
    actions.py            the decision table: 18 ranked actions + F1, R1, R2
    context.py            DecisionContext = snapshot + signals + contact history, frozen at decided_at
    policy.py             decide(): rules -> ranking (v1 | uplift | learner) -> control -> window
    features.py           versioned feature vector and the learner's context cell
    learner.py            Thompson sampling over (action, channel), hierarchical posteriors, logged propensities
    rewards.py            reward attribution and the uplift table from action-level controls
    evaluate.py           off-policy evaluation (IPS, SNIPS, bootstrap intervals)
    guardrails.py         unsubscribe / spam limits that pause actions automatically
    pipeline.py           hourly and nightly jobs with locks, job log and the promotion gate
    server.py             real-time trigger endpoint
    simulate.py           synthetic world to test the whole learning loop
    store.py              SQLite: decision log, ledger, events, rewards, models, state, locks
    run.py, scoring.py, assignment.py, windows.py, explain.py, countries.py, io.py, cli.py
    klaviyo/
      client.py           JSON:API client: offline | dry_run | live, rate limits, retries
      signals_sync.py     consent, timezone, predicted CLV, behavioural events -> customer signals
      outcomes.py         message events -> cap ledger
      ingest.py           Klaviyo trip events -> builder inputs
      payloads.py         NBA Decision event, profile properties, bulk import jobs
      verify.py, setup.py, registry.py
  sql/                  SQLite schema, BigQuery DDL (uplift and learner-health views), builder SQL
  deploy/README.md      schedules, container commands, secrets
  Dockerfile            one image for hourly, nightly and serve
  docs/klaviyo_setup.md generated build spec for the CRM team
  klaviyo_verification.json   what the live account has (ids, keys, counts; no customer values)
```

## Safety: writes to Klaviyo and what the learner may do

The Klaviyo account is Holafly production. The client has three modes:

| Mode | Reads (GET) | Writes (events, profile imports) |
| --- | --- | --- |
| `offline` | Disabled | Appended to the outbox JSONL |
| `dry_run` (default when a key is set) | Live | Appended to the outbox JSONL, **never sent** |
| `live` | Live | Sent. Only when `--mode live` **and** `NBA_KLAVIYO_LIVE_WRITES=yes`; only to events and profile-import endpoints |

- **No account changes.** The service cannot create, edit or delete flows, campaigns, segments, lists or templates.
- **PII never collected:**
  - Profiles are read with a sparse fieldset of consent, locale, timezone, country and predictions, so no email, phone, name or address ever comes back.
  - Events keep only whitelisted keys, so device ids and push tokens on app events, and addresses on order events, are dropped on read.
- **The learner works inside the rules.** Holdout, suppressions, the cap and Tier 0 reserve, eligibility, recency, fatigue, tier priority, fixed rules, kill switches and operator overrides all apply first. The learner only chooses which action within the top tier, and on which deliverable channel.
- **Only real sends teach.** Dry-run "dispatches" reached nobody, so attribution, training and guardrails use `dispatch_mode = live` rows by default. `--all-modes` is for testing only.

## The learning loop

1. **Signals** (`sync`, hourly). Klaviyo profile consent and predictions, plus app, intent, order, install, NPS and Always On events, become customer signals as of decision time. Warehouse-only fields (care contacts, paid share, plan type) come from `--signals`. Klaviyo events can switch a signal on; their absence never switches a warehouse value off.
2. **Decisions with propensities.** Under `--ranking learner`, each option (action × channel) is drawn by Thompson sampling, mixed with 10% uniform exploration.
   - Each decision logs its option set, the chosen option's probability, the model version, the context cell and the versioned feature vector.
   - Under the rules, the propensity is 1.
   - Seeds come from the decision id, so re-running an hour reproduces the same choice.
3. **Rewards** (`attribute`, nightly). For each randomised decision whose windows have closed, the service records:
   - **Converted:** the action's own outcome inside its window (for example, a top-up within 48 h).
   - **Reward:** net revenue in 7 days, minus €30 per unsubscribe and €150 per spam complaint within 72 h.

   Control rows start their window at the time the message would have gone out, so treated and control windows line up. Rows are recomputed for 7 days after their window closes, to pick up late events.
4. **Uplift table** (`uplift`). Treated vs control per action, with 95% intervals. An action stays in collection mode until its control has 100 outcomes, then it is marked keep or demote. The table feeds `--ranking uplift`.
5. **Training and the promotion gate** (`nightly`).
   - A new model is fit on everything except the newest 20% of rows.
   - It is compared off-policy, on the held-out learner rows, against the active model and the rules (SNIPS).
   - It is promoted, then refit on all rows, only if it is no more than 2% worse than the active model.
   - Every model is stored with its evaluation, and `models --activate VERSION` rolls back.
6. **Guardrails** (`nightly`). Over 14 days of live email sends, an action is paused if its unsubscribe rate exceeds 0.5% (or 1.5× existing flows) or its spam rate exceeds 0.1%.
   - The next hourly run treats the pause as a kill switch.
   - Only an operator can resume it, and sends before the resume do not count again.

`python -m nba simulate` runs the whole loop in a synthetic world where the rules' push-first preference is wrong outside Europe. These results come from that synthetic world, not real customers: they show the loop works.

- The rules pick the best channel 34% of the time.
- The learner reaches about 92% by week 3; the remaining 8% is deliberate exploration.
- Regret per decision falls from 0.115 to about 0.012.
- Off-policy evaluation estimates €3.7 per decision for the rules against €4.5 for the learner. The true values are €3.45 and €4.50.

## Run it

```bash
cd service
export PYTHONPATH=src

# Offline, on the sample customers
python -m nba run --customers examples/customers.csv --orders examples/orders.csv \
  --activations examples/activations.csv --usage examples/usage_daily.csv \
  --entitlements examples/entitlements.csv --signals examples/signals.csv \
  --as-of 2026-09-23T10:00:00Z --mode offline --ranking learner
python -m nba explain --customer cust_04_in_trip
python -m nba simulate --days 42

# Against the live account (reads only; writes go to the outbox)
export KLAVIYO_API_KEY=pk_...        # read scopes: accounts, events, flows, metrics, profiles
python -m nba verify-klaviyo
python -m nba sync --lookback-days 60
python -m nba hourly --from-klaviyo --usage usage_daily.csv --signals warehouse_signals.csv --mode dry_run
python -m nba nightly --mode dry_run
python -m nba jobs
```

Schedules, the container image and secrets are covered in [deploy/README.md](deploy/README.md). The real-time endpoint (`serve`) lets a Klaviyo flow webhook trigger a decision for one customer between hourly runs. It needs `NBA_TRIGGER_TOKEN`.

## Tests

```bash
python -m unittest discover -s tests/trip_state     # 24 builder tests
python -m unittest discover -s tests/nba            # 96 service tests
```

The service tests cover:

- the policy: every reason code, windows and DST, and hash parity with the console;
- the Klaviyo client: retries, rate limits and the dry-run and live guards;
- payloads, ingest and verification;
- the learning loop: point-in-time signals, learner propensities and pooling, reward attribution, and that dry-run sends don't count as real sends;
- the action-control fix: one control exposure per cooldown, not one a day;
- measurement: the uplift table, off-policy evaluation on known values, and guardrail pause and resume;
- operations: locks, schema migration, the hourly and nightly jobs, and trigger auth and validation;
- an end-to-end simulation in which the learner must beat the rules.

## Mapping from Klaviyo data (verified 10 Oct 2026)

- **eSIM key.** eSIMs are keyed by `order_id`, because `activation_event` has no `esim_id`.
- **Order-key formats differ by event.** The expiration event uses a UUID, while the install event uses an `HLY…` order number. The join key across order events still needs confirming.
- **Plan end date.** `expiration_date_eSIM_v2` provides `plan_end_at`.
- **NPS.** `Submitted NPS Score` carries `nps_total_score`.
- **Profile addressing.** Profiles are addressed by Klaviyo profile id, because `external_id` is sparse.
- **Care signals.** There are no Intercom metrics in Klaviyo, so `CARE_OPEN` needs an Intercom or warehouse feed.
- **Outcome to confirm.** B1's outcome uses `hly_consumption_start`, which still needs confirming as the Always On claim event.

See `klaviyo_verification.json` and `docs/klaviyo_setup.md`.
