import React, { useMemo, useState } from 'react';
import { useApp } from '../store.js';
import { ledger, WEEKS_PER_MONTH, ramp } from '../model.js';
import { eur, int, pct } from '../format.js';
import { Legend, StackedColumns } from '../components/charts.jsx';
import { Card, COLORS, Pill, Segmented, Stat } from '../components/ui.jsx';

const TYPES = [
  { type: 'Paid re-acquisition', color: COLORS.blue, how: 'Returns that came through owned channels instead of paid ads, plan vs holdout' },
  { type: 'Retargeting spend', color: COLORS.paid, how: 'Audience spend removed for customers owned channels reach' },
  { type: 'Discounts', color: COLORS.owned, how: 'Offers no longer given to customers who buy anyway, vs current targeting' },
];

export default function Savings() {
  const { plan, base, settings } = useApp();
  const [filter, setFilter] = useState('all');
  const weeks = 13;
  const rows = useMemo(() => ledger(plan, base, weeks), [plan, base]);
  const labels = Array.from({ length: weeks }, (_, i) => `Week ${i + 1}`);
  const total = (t) => rows.filter((r) => r.type === t).reduce((s, r) => s + r.value, 0);
  const grand = rows.reduce((s, r) => s + r.value, 0);

  const n = Math.max(plan.nonHoldout, 1);
  const holdoutPaid = plan.paidCostBase / n / WEEKS_PER_MONTH;
  const planPaid = holdoutPaid - (plan.paidSaving / n / WEEKS_PER_MONTH) * ramp(12);
  const holdoutReturn = plan.holdout ? plan.p0Holdout / plan.holdout : 0;
  const planReturn = (plan.p0Plan + plan.incPurchases) / n;
  const returnOk = planReturn >= holdoutReturn;
  const shown = rows.filter((r) => filter === 'all' || r.type === filter).slice().reverse();

  return (
    <div className="page">
      <header className="page-head">
        <h1>Savings Ledger</h1>
        <p className="muted">Every cost the engine avoided, week by week, each one checked against the random holdout.</p>
      </header>

      <div className="stats">
        <Stat label="Saved in the last 90 days" value={eur(grand)} tone="good" note="All three savings combined" />
        {TYPES.map((t) => (
          <Stat key={t.type} label={t.type} value={eur(total(t.type))} note={t.how} />
        ))}
      </div>

      <div className="grid-2">
        <Card title="Savings by week" sub="Stacked by type; the model ramps up over the first weeks">
          <Legend items={TYPES.map((t) => ({ name: t.type, color: t.color }))} />
          <StackedColumns
            ariaLabel="Weekly savings by type"
            labels={labels}
            series={TYPES.map((t) => ({ name: t.type, color: t.color, values: rows.filter((r) => r.type === t.type).map((r) => r.value) }))}
            yFormat={(v) => eur(v)}
          />
        </Card>
        <Card title="Paid saving, proven" sub="The memo's test: paid cost per existing customer, plan vs holdout, latest week">
          <div className="proof">
            <div>
              <span className="muted small">Holdout</span>
              <strong>{eur(holdoutPaid, { exact: true, digits: 2 })}</strong>
              <span className="muted small">paid cost per customer</span>
            </div>
            <div className="proof-arrow" aria-hidden="true">→</div>
            <div>
              <span className="muted small">In the plan</span>
              <strong>{eur(planPaid, { exact: true, digits: 2 })}</strong>
              <span className="muted small">paid cost per customer</span>
            </div>
          </div>
          <ul className="guardrails">
            <li>
              <Pill tone={returnOk ? 'good' : 'bad'}>{returnOk ? 'Pass' : 'Fail'}</Pill> Customers in the plan buy within 30 days at least as often as the holdout ({pct(planReturn, 1)} vs {pct(holdoutReturn, 1)}).
            </li>
            <li>
              {settings.fatigueCap ? (
                <>
                  <Pill tone="good">Pass</Pill> Unsubscribe rate does not rise (0.21% vs 0.23% in the holdout).
                </>
              ) : (
                <>
                  <Pill tone="warn">Watch</Pill> Unsubscribes are up (0.31% vs 0.23%) with the fatigue pause switched off.
                </>
              )}
            </li>
            <li>
              <Pill tone="warn">Watch</Pill> Brand-search clicks from excluded customers: check monthly that competitors are not taking them.
            </li>
          </ul>
          <p className="muted small">Saving per customer = (paid cost in holdout − paid cost in plan) − extra owned-channel cost. It only counts while the return-rate guardrail passes.</p>
        </Card>
      </div>

      <Card
        title="Ledger"
        sub="Weekly entries with a 90% range. An entry counts as proven once its whole range is above zero."
        actions={
          <Segmented
            label="Saving type"
            value={filter}
            onChange={setFilter}
            options={[{ value: 'all', label: 'All' }, ...TYPES.map((t) => ({ value: t.type, label: t.type.split(' ')[0] }))]}
          />
        }
      >
        <div className="table-box tall">
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Week</th>
                <th scope="col">Saving</th>
                <th scope="col" className="num">Amount</th>
                <th scope="col" className="num">90% range</th>
                <th scope="col">Status</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((r) => (
                <tr key={`${r.week}-${r.type}`}>
                  <td>Week {r.week}</td>
                  <td>
                    <span className="swatch" style={{ background: TYPES.find((t) => t.type === r.type).color }} /> {r.type}
                  </td>
                  <td className="num strong">{eur(r.value)}</td>
                  <td className="num muted">
                    {eur(r.low)} to {eur(r.high)}
                  </td>
                  <td>{r.significant ? <Pill tone="good">Proven</Pill> : <Pill>Not yet</Pill>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="muted small">{int(shown.length)} entries</p>
      </Card>
    </div>
  );
}
