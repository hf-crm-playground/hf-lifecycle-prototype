// Sample measurement results, shaped exactly as the measurement plan reports them.
// Every number here is illustrative until Phase 1 produces real control outcomes.
import { ACTIONS } from './actions.js';
import { DEMOTED } from './policy.js';
import { stableHash } from './rng.js';

const z = 1.96;

// Per-action uplift from the 10% action-level controls: treated vs control rate on the action's own outcome.
export function upliftTable(sentByAction, days = 60) {
  return ACTIONS.map((a) => {
    const perDay = DEMOTED.has(a.id) ? 40 : Math.max(2, (sentByAction[a.id] || 0) * 1.6);
    const nT = Math.round(perDay * days * 0.9);
    const nC = Math.round(perDay * days * 0.1);
    const noise = (stableHash(`${a.id}|uplift`) - 0.5) * 0.5;
    const pC = a.value.base;
    const pT = Math.min(0.98, pC + a.value.lift * (1 + noise));
    const se = Math.sqrt((pT * (1 - pT)) / Math.max(nT, 1) + (pC * (1 - pC)) / Math.max(nC, 1));
    const up = pT - pC;
    const controlOutcomes = Math.round(nC * pC);
    let status;
    if (a.needsData) status = 'Not running';
    else if (a.captureAction) status = 'Read on paid share';
    else if (DEMOTED.has(a.id)) status = 'Demoted';
    else if (controlOutcomes < 100) status = 'Collecting';
    else if (up - z * se > 0) status = 'Keep';
    else status = 'Demoted';
    return {
      a,
      nT,
      nC,
      pT,
      pC,
      uplift: up,
      low: up - z * se,
      high: up + z * se,
      controlOutcomes,
      revenuePerSend: up * a.value.perConv,
      status,
    };
  });
}

// Primary metric: incremental net revenue per eligible customer, treatment − global holdout, at fixed reads.
export const INTERIM_READS = [
  { day: 30, value: 0.71, low: -0.12, high: 1.54, capture: 0.18 },
  { day: 60, value: 1.42, low: 0.38, high: 2.46, capture: 0.31 },
  { day: 90, value: 1.95, low: 0.88, high: 3.02, capture: 0.38 },
];

// Cumulative incremental net revenue per eligible customer (treatment − holdout), by week,
// passing through the interim reads at days 30, 60 and 90.
export function cumulativeSeries(weeks = 13) {
  const pts = [[0, 0], ...INTERIM_READS.map((r) => [r.day, r.value])];
  const at = (day) => {
    for (let i = 1; i < pts.length; i++) {
      if (day <= pts[i][0]) {
        const [d0, v0] = pts[i - 1];
        const [d1, v1] = pts[i];
        return v0 + ((day - d0) / (d1 - d0)) * (v1 - v0);
      }
    }
    return pts[pts.length - 1][1];
  };
  const out = [];
  for (let w = 1; w <= weeks; w++) {
    const day = Math.min(90, w * 7);
    const wobble = w % 4 === 0 || w === weeks ? 0 : (stableHash(`wk${w}`) - 0.5) * 0.12;
    out.push({ week: w, day, incr: at(day) + wobble });
  }
  return out;
}

export const GUARDRAILS = [
  { name: 'Email unsubscribe rate per NBA send', threshold: 'Above 0.5%, or above 1.5× existing lifecycle flows', current: '0.31% (flows: 0.27%)', status: 'ok', action: 'Pause the action and review' },
  { name: 'Spam complaint rate', threshold: 'Above 0.1% on any action', current: '0.03% (highest: B2 0.05%)', status: 'ok', action: 'Pause the action immediately' },
  { name: 'Push opt-out and WhatsApp per-user blocks (Meta 131049)', threshold: 'Rising week over week for two weeks', current: 'Push opt-out up 1 week of 2', status: 'watch', action: 'Lower the channel sub-cap' },
  { name: 'Care contacts per 100 trips (Intercom)', threshold: 'Treatment higher than holdout by more than 10% relative', current: '+4% (6.2 vs 6.0)', status: 'ok', action: 'Review activation and run-out content' },
  { name: 'Sends per customer per month', threshold: 'Any customer above the cap', current: '0 customers above 8', status: 'ok', action: 'Bug: reconcile the counter against Received and Skipped Send events' },
];

export const LAYERS = [
  { layer: 'Global holdout', assignment: '10% at first NBA eligibility, stable hash, stratified by market and first-purchase channel, fixed for a quarter, excluded from NBA sends and from campaigns via a Klaviyo segment', purpose: 'Total value of the system versus no NBA marketing' },
  { layer: 'Action-level control', assignment: '10% of customers who would receive a given action at a given decision point receive nothing for that decision', purpose: 'Uplift per action and per cell; training data for v2' },
  { layer: 'Cap arms (Phase 2)', assignment: 'Treatment customers randomised to a monthly cap of 6, 8 or 10', purpose: 'Validates or revises the 8-per-month guardrail on revenue, unsubscribes and complaints' },
  { layer: 'Creative A/B', assignment: 'Klaviyo random-sample split inside a dispatcher branch', purpose: 'Copy, timing within window, channel order' },
  { layer: 'App encouragement', assignment: 'First-time web buyers randomised to receive or not receive P2 and T3', purpose: 'Causal effect of app adoption on 12-month revenue (encouragement design, LATE)' },
  { layer: 'Capture test (E11, Phase 2)', assignment: 'Capture-eligible customers randomised to a capture message or none', purpose: 'Avoided paid-acquisition cost per eligible customer, with purchase rate and revenue as the check that nothing is cannibalised' },
];

// n ≈ 2·(z₀.₉₇₅ + z₀.₈₀)²·σ² / δ²
export function powerPerArm({ sdMultiple = 2.5, relLift = 0.05, alpha = 0.05, power = 0.8 }) {
  const zA = alpha === 0.05 ? 1.96 : alpha === 0.1 ? 1.645 : 2.576;
  const zB = power === 0.8 ? 0.8416 : power === 0.9 ? 1.2816 : 0.5244;
  return Math.round((2 * (zA + zB) ** 2 * sdMultiple ** 2) / relLift ** 2);
}
