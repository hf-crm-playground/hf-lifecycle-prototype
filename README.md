# Holafly Trip NBA — decision console prototype

A clickable prototype of the **Holafly NBA System Design** (23 Sep 2026): a trip-state decision layer whose one objective is net contribution per customer — incremental 12-month net revenue plus the paid-acquisition cost avoided when a message wins a click a paid channel would otherwise take. The trip is the unit of decisioning and "no message" is always a candidate.

Everything runs in the browser on **6,000 sample travellers**. Nothing is sent to Klaviyo; settings, overrides and gap statuses are kept in the viewer's browser.

## Screens

| Screen | From the design |
| --- | --- |
| Overview | Primary metric vs the global holdout, this run's decision points by tier and by "no message" reason, trip-phase mix, guardrails |
| Traveller explorer | Trip State Builder snapshot (`trip_*` properties), every candidate with its reason code, S(a) breakdown, `nba_last_reason`, the `NBA Decision` event and `nba_*` profile properties, operator overrides (`nba_exclude`, `nba_manual_action`) |
| Decision policy | The decision table (18 ranked actions + F1, R1, R2), tiers, cap 8 with sub-caps and the Tier 0 reserve, holdout and action controls, capture sub-budget, kill switches, market caps, v1 fixed order vs uplift-table ranking (E8) |
| Last-click capture | Cap(a) calculator, v1 purchase signals, C1 via `nba_capture_allowed`, how the paid saving is measured |
| Measurement | Experimental layers, interim reads, per-action uplift with 95% intervals, guardrails, power calculator, the app-LTV question (E4) |
| Rollout and gaps | Phases 0–3 with exit gates, experiments E1–E11, the 24 data gaps as a tracker, assumptions A1–A6 |
| Data and Klaviyo | Architecture, trip state machine, `trip_state_builder` thresholds, the 23-event schema, the activation contract, Klaviyo native vs external |

## Code

- `src/engine/actions.js` — the decision table as data (triggers, eligibility, suppressions, channels, tiers, sample value models) and reason codes.
- `src/engine/policy.js` — `decide()`, a port of the policy v1 pseudocode: holdout → global suppressions → cap and reserve → per-action eligibility, confidence, recency, fatigue, capture budget → ranking → channel and sub-caps → action-level control → send window. Also `score()` for S(a), `nbaEvent()` and `nbaProfileProps()`.
- `src/engine/travellers.js` — sample travellers with builder snapshots; `tripProperties()` mirrors `trip_state_builder/klaviyo.py`.
- `src/engine/results.js`, `src/engine/content.js` — sample measurement results and the reference tables from the design.

All values marked as sample are placeholders until the Phase 1 action-level controls report.

## Decision Service (`service/`)

The Python service behind the console: the Trip State Builder, policy v1 as code, the decision log, and the Klaviyo connection (dry-run writes, live read checks). See [service/README.md](service/README.md).

## Run and deploy

```bash
npm install
npm run dev
```

Vercel detects Vite: build `npm run build`, output `dist`. Pushes to `main` redeploy.
