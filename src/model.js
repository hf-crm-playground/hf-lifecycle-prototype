// Decision and measurement model for the prototype.
// All money is in euros. One plan = one monthly cycle (30 days), matching the 30-day purchase predictions.

export const PAID_CPA = 14; // what a return through paid search or retargeting costs
export const RETARGET_MONTHLY = 0.6; // retargeting spend per customer per month while in the audience
export const WEEKS_PER_MONTH = 30 / 7;

const ZERO = { net: 0, spend: 0, delta: 0, gain: 0, paidSaving: 0, discountCost: 0, wasted: 0, sendCost: 0, backlash: 0 };

export function eligible(c, a) {
  if (a.channel === 'Email') return c.consentEmail;
  if (a.channel === 'SMS') return c.consentSms;
  if (a.channel === 'Push') return c.push;
  return true;
}

// Expected effect of one action on one customer, compared with doing nothing.
export function evaluate(c, a) {
  if (!a || a.id === 'none') return ZERO;
  const persuadable = 4 * c.churn * (1 - c.churn); // peaks at 50% churn risk
  const resp = persuadable * (1 - 0.6 * c.fatigue) * (c.stage === 'Lapsed' ? 0.6 : 1);
  const delta = resp * (a.baseLift + (a.offerLift || 0) * c.priceSensitivity);
  const perOrderOffer = (a.discountPct || 0) * c.aov + (a.shipping || 0);
  // The offer is paid on every order it touches, including orders that would have happened anyway.
  const discountCost = (c.p0 + delta) * perOrderOffer;
  const wasted = c.p0 * perOrderOffer; // offer given on orders that would have happened anyway
  const backlash = c.fatigue > 0.7 ? (c.fatigue - 0.7) * 0.25 * c.value12m * 0.3 : 0;
  const paidSaving = c.p0 * c.paidShare * (a.capture || 0) * PAID_CPA;
  const sendCost = a.sendCost || 0;
  const gain = delta * c.orderProfit;
  const net = gain - sendCost - discountCost - backlash + paidSaving;
  return { net, spend: sendCost + discountCost, delta, gain, paidSaving, discountCost, wasted, sendCost, backlash };
}

export function upliftByAction(c, actions) {
  return actions.map((a) => ({ action: a, ok: eligible(c, a), ev: evaluate(c, a) }));
}

function finish(rows, settings) {
  for (const r of rows) {
    r.retargetSaving = 0;
    r.excluded = false;
    if (settings.paidExclusions && !r.holdout && r.action !== 'none' && r.c.paidShare > 0.2) {
      r.excluded = true;
      r.retargetSaving = RETARGET_MONTHLY;
    }
  }
  return rows;
}

// The engine: pick the most profitable action per customer, then fit the plan to the monthly budget.
export function optimize(customers, actions, settings) {
  const enabled = actions.filter((a) => a.enabled);
  const rows = [];
  const cand = [];
  for (const c of customers) {
    const holdout = c.holdoutRoll < settings.holdoutPct;
    const row = { c, holdout, action: 'none', ev: ZERO, best: null, alt: null };
    rows.push(row);
    if (holdout) continue;
    if (settings.fatigueCap && c.fatigue > 0.8) {
      row.capped = true;
      continue;
    }
    for (const a of enabled) {
      if (!eligible(c, a)) continue;
      const ev = evaluate(c, a);
      if (ev.net <= 0) continue;
      if (!row.best || ev.net > row.best.ev.net) row.best = { a, ev };
      if (ev.spend < 0.05 && (!row.alt || ev.net > row.alt.ev.net)) row.alt = { a, ev };
    }
    if (row.best) cand.push(row);
  }
  cand.sort((x, y) => y.best.ev.net / Math.max(y.best.ev.spend, 0.02) - x.best.ev.net / Math.max(x.best.ev.spend, 0.02));
  let spent = 0;
  for (const r of cand) {
    if (spent + r.best.ev.spend <= settings.budget) {
      r.action = r.best.a.id;
      r.ev = r.best.ev;
      spent += r.ev.spend;
    } else if (r.alt && spent + r.alt.ev.spend <= settings.budget) {
      r.action = r.alt.a.id;
      r.ev = r.alt.ev;
      spent += r.ev.spend;
    }
  }
  return finish(rows, settings);
}

// "Current targeting": the usual rule — at-risk and lapsed customers get a 10% offer, most likely buyers first, until the budget runs out.
export function responseBaseline(customers, actions, settings) {
  const offer = actions.find((a) => a.id === 'email10') || {
    id: 'email10', channel: 'Email', sendCost: 0.003, discountPct: 0.1, shipping: 0, baseLift: 0.06, offerLift: 0.1, capture: 0.4,
  };
  const rows = customers.map((c) => ({ c, holdout: c.holdoutRoll < settings.holdoutPct, action: 'none', ev: ZERO }));
  const pool = rows
    .filter((r) => !r.holdout && r.c.consentEmail && (r.c.stage === 'At-risk' || r.c.stage === 'Lapsed'))
    .sort((x, y) => y.c.p0 - x.c.p0);
  let spent = 0;
  for (const r of pool) {
    const ev = evaluate(r.c, offer);
    if (spent + ev.spend > settings.budget) break;
    r.action = offer.id;
    r.ev = ev;
    spent += ev.spend;
  }
  return finish(rows, { ...settings, paidExclusions: false });
}

export function aggregate(rows, filter = () => true) {
  const t = {
    customers: 0, treated: 0, holdout: 0, spend: 0, net: 0, gain: 0, paidSaving: 0, retargetSaving: 0,
    discountCost: 0, wasted: 0, sendCost: 0, backlash: 0, incPurchases: 0, sends: 0, excluded: 0, baseProfit: 0, baseHoldoutProfit: 0,
    paidCostBase: 0, p0Plan: 0, p0Holdout: 0, byAction: {},
  };
  for (const r of rows) {
    if (!filter(r)) continue;
    t.customers++;
    const monthlyBase = r.c.p0;
    if (r.holdout) {
      t.holdout++;
      t.p0Holdout += r.c.p0;
      t.baseHoldoutProfit += monthlyBase * r.c.orderProfit;
      continue;
    }
    t.p0Plan += r.c.p0;
    t.baseProfit += monthlyBase * r.c.orderProfit;
    t.paidCostBase += monthlyBase * r.c.paidShare * PAID_CPA;
    if (r.action !== 'none') {
      t.treated++;
      t.sends++;
      t.spend += r.ev.spend;
      t.net += r.ev.net;
      t.gain += r.ev.gain;
      t.paidSaving += r.ev.paidSaving;
      t.discountCost += r.ev.discountCost;
      t.wasted += r.ev.wasted;
      t.sendCost += r.ev.sendCost;
      t.backlash += r.ev.backlash;
      t.incPurchases += r.ev.delta;
    }
    if (r.excluded) {
      t.excluded++;
      t.retargetSaving += r.retargetSaving;
    }
    const b = (t.byAction[r.action] ||= { count: 0, spend: 0, net: 0 });
    b.count++;
    b.spend += r.ev.spend;
    b.net += r.ev.net + (r.retargetSaving || 0);
  }
  t.total = t.net + t.retargetSaving;
  t.nonHoldout = t.customers - t.holdout;
  return t;
}

// Deterministic noise so charts look measured rather than drawn.
export function noiseAt(seed, i) {
  const x = Math.sin(seed * 97.13 + i * 12.9898) * 43758.5453;
  return x - Math.floor(x) - 0.5;
}

// The model learns over the first weeks, so realized uplift ramps up.
export const ramp = (w) => Math.min(1, 0.45 + w * 0.07);

// Weekly profit per customer, treated vs holdout, over the period.
export function weeklySeries(agg, weeks) {
  const n = Math.max(agg.nonHoldout, 1);
  const basePer = agg.baseProfit / n / WEEKS_PER_MONTH;
  const incPer = agg.total / n / WEEKS_PER_MONTH;
  const out = [];
  for (let w = 0; w < weeks; w++) {
    const holdout = basePer * (1 + noiseAt(1, w) * 0.06);
    const treated = holdout + incPer * ramp(w) * (1 + noiseAt(2, w) * 0.25);
    out.push({ week: w + 1, holdout, treated });
  }
  return out;
}

// Totals over the last `weeks` weeks, given the current monthly plan.
export function periodTotals(agg, weeks) {
  let factor = 0;
  const start = 13 - weeks;
  for (let w = start; w < 13; w++) factor += ramp(w);
  factor /= WEEKS_PER_MONTH;
  return {
    profit: agg.total * factor,
    paid: (agg.paidSaving + agg.retargetSaving) * factor,
    purchases: agg.incPurchases * factor,
    spend: (agg.spend * weeks) / WEEKS_PER_MONTH,
    factor,
  };
}

// Savings ledger: each saving by week, treated vs holdout, with a 90% range.
export function ledger(plan, base, weeks) {
  const rows = [];
  for (let w = 0; w < weeks; w++) {
    const rp = ramp(w) / WEEKS_PER_MONTH;
    const shrink = Math.min(w, 8);
    const types = [
      { type: 'Paid re-acquisition', value: plan.paidSaving * rp, spread: 0.55 - shrink * 0.045 },
      { type: 'Retargeting spend', value: plan.retargetSaving * rp, spread: 0.3 - shrink * 0.02 },
      { type: 'Discounts', value: Math.max(0, base.wasted - plan.wasted) * rp, spread: 0.45 - shrink * 0.035 },
    ];
    types.forEach((t, k) => {
      const v = t.value * (1 + noiseAt(5 + k, w) * 0.2);
      const half = Math.abs(v) * Math.max(t.spread, 0.12);
      rows.push({ week: w + 1, type: t.type, value: v, low: v - half, high: v + half, significant: v - half > 0 && w >= 2 });
    });
  }
  return rows;
}
