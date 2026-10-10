# Trip State Builder (heuristic v1)

Reference implementation of the **Trip State Builder** from the *Holafly NBA
System Design* document. It turns orders, activations, daily usage and plan
end dates into one trip state per customer, with a confidence tier and the
in-trip metrics the Decision Service needs (data remaining, projected
run-out, plan end, pending activations).

No third-party dependencies: Python 3.9+ standard library only. The
warehouse SQL version of the core logic is tested for parity in SQLite and
ported to BigQuery.

## The rule set

| Situation | Rule | Where |
| --- | --- | --- |
| Trip definition | Starts at the eSIM activation (if within 3 days before first usage; otherwise at first usage), ends after **14 days without usage**; usage on day 15 starts a new trip | `builder.py`, `config.trip_gap_days` |
| Trip end | Provisional at 14 days, **confirmed at 16 days** (absorbs 24–48 h usage latency); late usage reopens the same trip | `config.trip_end_confirm_days` |
| Post-trip vs between trips | Post-trip for 30 days after the end day, then between trips | `config.post_trip_days` |
| Test install | A day with only home-country usage under 50 MB neither starts nor extends a trip; an activated but unused eSIM keeps the customer **pre-trip** with `test_install_detected` | `config.test_install_max_mb` |
| Confidence | **high**: usage outside the home country, or country unknown and a day ≥ 50 MB; **medium**: usage only at home, or unknown and light; pre-trip is **low** (medium if activated) | `config.high_confidence_day_mb` |
| Multi-eSIM | Usage is unioned per customer: a family's three eSIMs are one trip; Japan → Korea back to back is one trip with two destinations | `_cluster_days` |
| New order during a trip | Stays `in_trip` with `pending_activation=True` and `pending_esim_ids` | step 6 |
| Refund / cancellation | The eSIM and all its usage are ignored | step 1 |
| Usage latency | If the usage feed is older than 48 h, `usage_fresh=False` and run-out fields are withheld | `config.usage_stale_hours` |
| Run-out | Among eSIMs still in use (usage in the last 3 days), the one with the earliest projected run-out (linear burn over the last 3 usage days); unlimited plans excluded | `_fill_data_metrics` |
| Plan end | `plan_ends_within_48h`; `plan_ended_usage_stopped` when the plan expired and usage stopped that day (timing hint; the 14-day rule still governs the record) | `_fill_data_metrics` |
| Long trip | `long_trip=True` after 30 days in trip | `config.long_trip_days` |
| Declared dates | `departure_likely=True` when a declared travel date has passed with nothing activated | step 6 |

States: `no_trip`, `pre_trip`, `in_trip`, `trip_end_provisional`, `post_trip`,
`between_trips`. Precedence when signals conflict: an active trip beats a
pending order (the order becomes `pending_activation`); a pending order beats
post-trip and between-trips (the customer is pre-trip for the next journey).

Every snapshot carries `reasons`, a list of plain-language explanations that
becomes `trip_state_reason` on the Klaviyo profile.

## Layout

```
trip_state/
  config.py    thresholds (all policy choices in one place)
  models.py    input records (Customer, Order, Activation, UsageDay, Entitlement)
               and outputs (Trip, TripSnapshot)
  builder.py   build_trip_state() for one customer, build_many() for a batch
  klaviyo.py   to_profile_properties(), to_profile_import_body()
  cli.py       CSV in -> snapshots.csv, trips.csv, klaviyo.jsonl
sql/
  trip_sessions_sqlite.sql    executable reference (tested)
  trip_sessions_bigquery.sql  same logic, BigQuery syntax
tests/
  fixtures.py          18 named scenarios
  test_builder.py      state machine, metrics, Klaviyo mapping
  test_sql_parity.py   SQL == Python on every usage-driven scenario
  test_cli.py          end to end on examples/
examples/              CSVs generated from the scenarios, plus their outputs
```

## Run

```
python -m unittest discover -s tests -v

python -m trip_state.cli \
  --customers examples/customers.csv --orders examples/orders.csv \
  --activations examples/activations.csv --usage examples/usage_daily.csv \
  --entitlements examples/entitlements.csv \
  --as-of 2026-09-23T10:00:00Z --usage-as-of 2026-09-23T06:00:00Z \
  --snapshots-out snapshots.csv --trips-out trips.csv --klaviyo-out klaviyo.jsonl
```

In production: run `build_many()` hourly over customers with any event in
the last 30 days (plus everyone with a scheduled decision point), write the
snapshot to the warehouse (`trip_state_snapshot`), and push changed
snapshots to Klaviyo with `to_profile_import_body()` through the Create or
Update Profile endpoint. `usage_data_as_of` must be the real load time of
the carrier usage feed.

## Input columns (CSV) / fields

| Table | Required | Optional |
| --- | --- | --- |
| customers | `customer_id` | `home_country` (ISO-2), `declared_travel_start` (YYYY-MM-DD), `declared_destination` |
| orders | `customer_id`, `order_id`, `esim_id`, `ordered_at`, `destination` | `plan_type` (finite/unlimited), `plan_mb`, `plan_days`, `purchase_channel`, `status` (confirmed/refunded/cancelled) |
| activations | `customer_id`, `esim_id`, `activated_at` | `first_network_country` |
| usage_daily | `customer_id`, `esim_id`, `day`, `mb_used` | `mb_remaining`, `network_country` |
| entitlements | `customer_id`, `esim_id` | `plan_end_at` |

Missing optional fields degrade gracefully: no `home_country` disables the
test-install rule; no `network_country` makes confidence depend on daily
volume; no `mb_remaining` disables run-out projection.

## What is deliberately not here

- Timezone mapping (destination country → timezone) for send windows: a
  lookup the Decision Service applies to `trip_destination`.
- Network-country change detection for the add-destination action (I6):
  needs a per-day country feed, which is a data gap in the design document.
- Any learned component. The one place a model could replace a heuristic is
  the test-install / trip-start disambiguation (`_home_tiny` and step 3);
  the design document describes how a typed model would be evaluated in
  shadow mode against these rules before replacing them.

## Changing the rules

Edit `TripConfig` (or pass `--trip-gap-days` / `--confirm-days` to the CLI).
The tests pin the defaults from the design document; change the fixtures
when the business definition changes, not the other way round.
