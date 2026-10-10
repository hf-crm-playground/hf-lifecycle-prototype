# Running the service on a schedule

One container image (`service/Dockerfile`), three entry points. All state lives in the SQLite file passed with `--db` (mount a volume); the BigQuery DDL in `sql/` is the analysis copy.

| Job | Schedule | Command |
| --- | --- | --- |
| Hourly decisions | `5 * * * *` | `hourly --db /data/nba.db --from-klaviyo --usage /data/usage_daily.csv --signals /data/warehouse_signals.csv --ranking v1 --mode dry_run` |
| Nightly learning | `30 3 * * *` (UTC) | `nightly --db /data/nba.db --report-dir /data/reports --mode dry_run` |
| Trigger endpoint | always on | `serve --db /data/nba.db --host 0.0.0.0 --port 8080 --mode dry_run` |

- **Secrets:** pass these as environment variables, never as flags.
  - `KLAVIYO_API_KEY`: a private key with read scopes for accounts, events, flows, metrics and profiles.
  - `NBA_TRIGGER_TOKEN`: 16+ random characters. It is also set as a header on the Klaviyo webhook action.
  - `NBA_KLAVIYO_LIVE_WRITES=yes` (with `--mode live`): only when the CRM owner signs off on going live.
- **Ranking:** start with `--ranking v1` (rules). Switch to `--ranking learner` once nightly reports show treated reward rows and the promotion gate has activated a model. The learner always explores within the rules (10%), so the first weeks are also the training data.
- **Overlap:** each job takes a lock in the store, so an hourly run that overruns does not start twice. `python -m nba jobs` shows recent runs and errors.
- **Pausing an action:** guardrail pauses are written to `policy_state` and read on the next run. Resume with `guardrails --resume I1` after review.

Example crontab on a single VM:

```cron
5 * * * *  docker run --rm --env-file /etc/nba.env -v nba-data:/data holafly-nba hourly  --db /data/nba.db --from-klaviyo --usage /data/usage_daily.csv --mode dry_run >> /var/log/nba-hourly.log 2>&1
30 3 * * * docker run --rm --env-file /etc/nba.env -v nba-data:/data holafly-nba nightly --db /data/nba.db --report-dir /data/reports --mode dry_run >> /var/log/nba-nightly.log 2>&1
```

On Google Cloud the same image runs as two Cloud Run jobs triggered by Cloud Scheduler plus one Cloud Run service for `serve`. In that case move the store to a shared volume or replace SQLite with BigQuery/Postgres, because Cloud Run instances do not share a disk.
