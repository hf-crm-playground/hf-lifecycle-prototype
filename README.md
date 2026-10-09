# GenComm Lifecycle — prototype

A clickable prototype of a lifecycle profit engine: for each customer it decides whether to act, how, through which channel and with what incentive, judged by the profit it adds against a random holdout.

Everything runs in the browser on **sample data** for a demo store of 8,000 customers. Nothing is sent anywhere; pushes and integrations are simulated, and settings are kept in the viewer's browser.

## Screens

| Screen | What it shows |
| --- | --- |
| Executive Summary | Profit added vs holdout, paid cost avoided, wasted discount avoided, lifecycle stages, next-euro split, recommended actions with review-and-push |
| Customer Explorer | Search and filter customers; each one's value, churn risk, recommended action, the reasons, and the profit each action would add |
| Lifecycle Optimizer | Monthly budget, holdout size, fatigue pause, paid-ad exclusions; editable action library; engine vs current targeting at every budget |
| Savings Ledger | Weekly savings (paid re-acquisition, retargeting, discounts) with 90% ranges and guardrails |
| Next-Euro Planner | Marginal return of retention vs acquisition and the best split of a fixed budget |
| Drivers | What the churn, value and uplift models rely on most |
| Integrations | Simulated connectors and the audit log of every push |

## How the model works (sample)

- `src/data.js` generates the customer base (seeded, so numbers are stable).
- `src/model.js` scores each action per customer: extra orders × order profit, minus send cost, discounts paid on all orders the offer touches, fatigue risk, plus paid-ad cost avoided. The optimizer picks the best action per customer and fits the plan to the budget by profit per euro.
- "Current targeting" is the usual rule: a 10% offer to at-risk and lapsed customers, most likely buyers first.

## Run locally

```bash
npm install
npm run dev
```

## Deploy on Vercel

Import the repository in Vercel. It detects Vite automatically: build command `npm run build`, output directory `dist`. No environment variables are needed.
