import React, { useMemo, useState } from 'react';
import { useApp } from '../store.js';
import { P_WINS } from '../engine/actions.js';
import { eur, int, pct } from '../format.js';
import { BarList } from '../components/charts.jsx';
import { Card, COLORS, Pill, Segmented, Stat } from '../components/ui.jsx';

const PRESETS = [
  { label: 'Cart, high paid share', p0: 0.8, paid: 0.6, ch: 'push', cPaid: 5, perk: 0 },
  { label: 'Medium intent', p0: 0.5, paid: 0.3, ch: 'push', cPaid: 5, perk: 0 },
  { label: 'Repeat traveller, email', p0: 0.5, paid: 0.4, ch: 'email', cPaid: 4, perk: 0.3 },
];

const SIGNALS = [
  { signal: 'Browse or cart in the last 24 h without an order (Viewed Product, Added to Cart, Checkout Started, view_item, begin_checkout)', p0: 'High', paid: 'Coupon search before checkout; affiliate last click', action: 'C1 intent capture, within minutes' },
  { signal: 'Plan ending within 72 h with usage (I2)', p0: 'High', paid: 'Brand search for a top-up; PPC click', action: 'Capture value is added to I2’s score; no new action' },
  { signal: 'Declared travel date within 7 days and no order', p0: 'Medium-high', paid: 'Comparison sites, brand PPC', action: 'C2 purchase-window capture' },
  { signal: 'Repeat traveller whose past trips predict a purchase this month', p0: 'Medium', paid: 'Comparison sites', action: 'C2 at lower priority' },
  { signal: 'Prior coupon use (Coupon Used, Coupon Assigned)', p0: 'Multiplier on P(paid)', paid: 'Coupon affiliates', action: 'Content: a direct perk instead of a code hunt' },
];

export default function Capture() {
  const { decisions, policy } = useApp();
  const [v, setV] = useState(PRESETS[0]);
  const set = (patch) => setV({ ...v, ...patch, label: 'Custom' });
  const value = v.p0 * v.paid * P_WINS[v.ch] * v.cPaid - v.perk;

  const today = useMemo(() => {
    const t = { c1: 0, c2: 0, i2Cap: 0, i2: 0, spent: 0, paidLow: 0, capSum: 0 };
    for (const d of decisions) {
      if (d.action === 'C1') t.c1++;
      if (d.action === 'C2') t.c2++;
      if (d.action === 'I2' && d.score) {
        t.i2++;
        t.i2Cap += d.score.cap;
      }
      if (d.score && d.outcome === 'sent') t.capSum += d.score.cap;
      for (const r of d.candidates) {
        if (r.why === 'CAPTURE_BUDGET_SPENT') t.spent++;
        if (r.why === 'PAID_SHARE_BELOW_30') t.paidLow++;
      }
    }
    return t;
  }, [decisions]);

  // Measurement: avoided cost = (holdout paid share − treatment paid share) × orders per customer × c_paid − perk.
  const holdoutShare = 0.34;
  const treatShare = 0.27;
  const ordersPer = 0.21;
  const avoided = (holdoutShare - treatShare) * ordersPer * 4 - 0.004;

  return (
    <div className="page">
      <header className="page-head">
        <h1>Winning the last click</h1>
        <p className="muted lead-narrow">
          A message to a traveller who would buy anyway is worth sending only when the click it wins would otherwise go to a paid channel: an affiliate’s commission, a brand-search click, a comparison site’s fee. Where the alternative is organic or direct, winning the click only moves credit between reports and scores zero.
        </p>
      </header>

      <div className="stats">
        <Stat label="C1: native flows allowed this run" value={int(today.c1)} note="nba_capture_allowed = true; the cart and browse flows send" />
        <Stat label="C2 purchase-window sends" value={int(today.c2)} note="Scheduled by the service like any other action" />
        <Stat label="Capture value added to I2" value={eur(today.i2Cap)} note={`Across ${int(today.i2)} plan-extension sends`} tone="good" />
        <Stat label="Blocked by the capture sub-budget" value={int(today.spent)} note={`${policy.captureBudget} of ${policy.cap30d} sends; never displaces Tier 0`} />
      </div>

      <div className="grid-2">
        <Card title="Capture value calculator" sub="Cap(a) = P₀(buy) · P(paid last click | buy, none) · P(a wins the click | buy) · c_paid − d(a)">
          <div className="seg-row">
            {PRESETS.map((p) => (
              <button key={p.label} type="button" className={`btn small${v.label === p.label ? ' is-on' : ''}`} onClick={() => setV(p)}>
                {p.label}
              </button>
            ))}
          </div>
          <Slider label="P₀(buy): buys in the window with no message" value={v.p0} min={0} max={1} step={0.05} fmt={pct} onChange={(x) => set({ p0: x })} />
          <Slider label="P(paid last click): share whose last click would be paid" value={v.paid} min={0} max={1} step={0.05} fmt={pct} onChange={(x) => set({ paid: x })} />
          <div className="setting">
            <span className="setting-label">Channel, which sets P(a wins the click)</span>
            <Segmented
              label="Channel"
              value={v.ch}
              onChange={(x) => set({ ch: x })}
              options={[
                { value: 'in_app', label: `In-app ${P_WINS.in_app}` },
                { value: 'push', label: `Push ${P_WINS.push}` },
                { value: 'email', label: `Email ${P_WINS.email}` },
              ]}
            />
          </div>
          <Slider label="c_paid: cost avoided per paid order (placeholder €2–5)" value={v.cPaid} min={1} max={8} step={0.5} fmt={(x) => eur(x, { exact: true, digits: 2 })} onChange={(x) => set({ cPaid: x })} />
          <Slider label="d(a): perk in the message (points or a small code)" value={v.perk} min={0} max={3} step={0.1} fmt={(x) => eur(x, { exact: true, digits: 2 })} onChange={(x) => set({ perk: x })} />
          <div className={`calc-result${value > policy.lambda ? '' : ' is-low'}`}>
            <span className="muted small">Capture value of one send</span>
            <strong>{eur(value, { exact: true, digits: 2 })}</strong>
            <span className="small">
              {value > policy.lambda
                ? `Above the ${eur(policy.lambda, { exact: true, digits: 2 })} shadow price of a send: worth spending capture budget on.`
                : `Below the ${eur(policy.lambda, { exact: true, digits: 2 })} shadow price of a send: not worth a send on capture alone.`}
            </span>
          </div>
          <p className="muted small">The design’s arithmetic: P₀ 0.8, P(paid) 0.6, P(wins) 0.7, c_paid €5 gives about €1.70; P₀ 0.5 and P(paid) 0.3 gives about €0.50. Capture pays at the sharpest intent moments and rarely elsewhere, which is why it gets its own small budget.</p>
        </Card>

        <Card title="Cost to bring an existing traveller back" sub="Sample cost per returning customer, by route">
          <BarList
            items={[
              { label: 'Affiliate coupon', value: 6.5, color: COLORS.paid },
              { label: 'Brand search (PPC)', value: 4.2, color: COLORS.paid },
              { label: 'Comparison site', value: 3.8, color: COLORS.paid },
              { label: 'WhatsApp', value: 0.09, color: COLORS.owned },
              { label: 'Email', value: 0.004, color: COLORS.owned, display: '<€0.01' },
              { label: 'Push or in-app', value: 0.001, color: COLORS.owned, display: '<€0.01' },
            ]}
            format={(x) => eur(x, { exact: true, digits: 2 })}
            labelWidth={140}
          />
          <h3 className="h3">How the saving is measured</h3>
          <p className="small">
            Against the same global holdout, with order-level attribution: paid last-click share of orders and paid cost per order, treatment vs holdout. Total orders and revenue are the guardrail that capture is not cannibalising anything.
          </p>
          <div className="proof">
            <div>
              <span className="muted small">Holdout</span>
              <strong>{pct(holdoutShare)}</strong>
              <span className="muted small">paid last-click share</span>
            </div>
            <div className="proof-arrow" aria-hidden="true">→</div>
            <div>
              <span className="muted small">Treatment</span>
              <strong>{pct(treatShare)}</strong>
              <span className="muted small">paid last-click share</span>
            </div>
          </div>
          <p className="small mono">
            avoided = ({pct(holdoutShare)} − {pct(treatShare)}) × {ordersPer} orders × €4 − perk = {eur(avoided, { exact: true, digits: 3 })} per eligible customer per month
          </p>
          <p className="muted small">Two dashboards stay separate: attributed CRM revenue, which rises by construction once capture runs, and incremental-plus-capture net contribution, which is what the policy optimises.</p>
        </Card>
      </div>

      <Card title="High-confidence purchase signals in v1" sub="Where P₀(buy) is high enough for capture to pay">
        <div className="table-box">
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Signal</th>
                <th scope="col">P₀(buy) proxy</th>
                <th scope="col">Paid channel most likely to take the click</th>
                <th scope="col">Capture action</th>
              </tr>
            </thead>
            <tbody>
              {SIGNALS.map((r) => (
                <tr key={r.signal}>
                  <td className="small">{r.signal}</td>
                  <td>{r.p0}</td>
                  <td className="small">{r.paid}</td>
                  <td className="small">{r.action}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="note-grid">
          <div>
            <h3 className="h3">Content and channel</h3>
            <p className="small">A direct deep link to checkout in the app with destination and eSIM prefilled, so the app purchase wins the click and advances app adoption. Loyalty points before discount codes; a small code only for known coupon users when cheaper than commission plus the affiliate’s coupon (tested in E11).</p>
          </div>
          <div>
            <h3 className="h3">Execution</h3>
            <p className="small">C1 must fire within minutes, so it runs natively in <span className="mono">CRM_TRIPS_PRE_ABANDONED_CART_V3</span>, <span className="mono">CRM_TRIPS_PRE_APP_VIEW_ITEM_RECOVERY_V2</span> and <span className="mono">APP - Cart Abandonment recovery</span>, gated by <span className="mono">nba_capture_allowed</span>, which the service sets hourly. One owner per moment.</p>
          </div>
          <div>
            <h3 className="h3">Risks and controls</h3>
            <p className="small">Crowding out incremental actions (sub-budget, Tier 0 precedence); fatigue on heavy buyers (the cap); margin erosion from codes (points first); attribution quality: without a last-click channel per order (gap 21), v1 runs capture only at cart and plan-end moments.</p>
          </div>
          <div>
            <h3 className="h3">This run</h3>
            <p className="small">
              {int(today.paidLow)} capture candidates were dropped because their segment’s paid last-click share is below 30%. Capture value across all sends: {eur(today.capSum)}.
            </p>
            <Pill tone="warn">Needs gap 21: order-level last-click channel</Pill>
          </div>
        </div>
      </Card>
    </div>
  );
}

function Slider({ label, value, min, max, step, fmt, onChange }) {
  return (
    <label className="range">
      <span className="range-head">
        <span>{label}</span>
        <strong>{fmt(value)}</strong>
      </span>
      <input type="range" min={min} max={max} step={step} value={value} onChange={(e) => onChange(Number(e.target.value))} />
    </label>
  );
}
