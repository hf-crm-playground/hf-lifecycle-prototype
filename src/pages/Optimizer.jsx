import React, { useDeferredValue, useMemo } from 'react';
import { useApp } from '../store.js';
import { aggregate, optimize, responseBaseline } from '../model.js';
import { eur, int } from '../format.js';
import { audienceGroups } from '../groups.js';
import { BarList, Legend, LineChart } from '../components/charts.jsx';
import { Card, COLORS, Pill, Stat, Toggle } from '../components/ui.jsx';

const BUDGET_MAX = 8000;
const CURVE_STEPS = [0, 500, 1000, 1500, 2000, 3000, 4000, 5000, 6000, 8000];

export default function Optimizer() {
  const { customers, actions, setActions, settings, setSettings, planRows, plan, base, openPush, pushLog } = useApp();

  const set = (patch) => setSettings({ ...settings, ...patch });
  const setAction = (id, patch) => setActions(actions.map((a) => (a.id === id ? { ...a, ...patch } : a)));

  const curveKey = useDeferredValue(JSON.stringify([actions, settings.holdoutPct, settings.fatigueCap, settings.paidExclusions]));
  const curve = useMemo(() => {
    const [acts] = JSON.parse(curveKey);
    return CURVE_STEPS.map((b) => {
      const s = { ...settings, budget: b };
      return { b, engine: aggregate(optimize(customers, acts, s)).total, rule: aggregate(responseBaseline(customers, acts, s)).total };
    });
  }, [curveKey]);

  const budgetIdx = CURVE_STEPS.reduce((best, b, i) => (Math.abs(b - settings.budget) < Math.abs(CURVE_STEPS[best] - settings.budget) ? i : best), 0);
  const groups = audienceGroups(planRows, actions);
  const cap = curve.find((p, i) => i > 0 && Math.abs(p.engine - curve[i - 1].engine) < 1);

  return (
    <div className="page">
      <header className="page-head">
        <h1>Lifecycle Optimizer</h1>
        <p className="muted">Set the monthly budget and guardrails. The engine picks the most profitable action for each customer, including doing nothing.</p>
      </header>

      <div className="grid-opt">
        <Card title="Budget and guardrails">
          <label className="range">
            <span className="range-head">
              <span>Monthly budget</span>
              <strong>{eur(settings.budget, { exact: true })}</strong>
            </span>
            <input type="range" min="0" max={BUDGET_MAX} step="250" value={settings.budget} onChange={(e) => set({ budget: Number(e.target.value) })} />
            <span className="muted small">Message costs plus the cost of discounts and free shipping.</span>
          </label>
          <label className="range">
            <span className="range-head">
              <span>Random holdout</span>
              <strong>{settings.holdoutPct}% of customers</strong>
            </span>
            <input type="range" min="0" max="30" step="1" value={settings.holdoutPct} onChange={(e) => set({ holdoutPct: Number(e.target.value) })} />
            <span className="muted small">{settings.holdoutPct < 5 ? 'Too small to measure results reliably.' : 'Gets nothing, so every result is measured against it.'}</span>
          </label>
          <Toggle checked={settings.fatigueCap} onChange={(v) => set({ fatigueCap: v })} label="Pause message-fatigued customers" hint="Skips customers whose sends far outrun their opens." />
          <Toggle checked={settings.paidExclusions} onChange={(v) => set({ paidExclusions: v })} label="Exclude reachable customers from retargeting" hint="Removes them from Google and Meta audiences while owned channels reach them." />
        </Card>

        <div className="stack">
          <div className="stats is-3">
            <Stat label="Customers in the plan" value={int(plan.treated)} note={`${int(plan.holdout)} in holdout · ${int(plan.nonHoldout - plan.treated)} get nothing`} />
            <Stat label="Monthly spend" value={eur(plan.spend)} note={plan.spend < settings.budget - 250 ? 'Below budget: more spend would not pay back' : 'Budget fully used'} tone={plan.spend < settings.budget - 250 ? 'good' : undefined} />
            <Stat label="Profit added per month" value={eur(plan.total)} note={`${eur(plan.paidSaving + plan.retargetSaving)} of it from paid ads avoided`} tone="good" />
          </div>
          <Card title="Engine vs current targeting" sub="Profit added per month at each budget. Current targeting: 10% offer to at-risk and lapsed customers, most likely buyers first.">
            <Legend items={[{ name: 'Engine', color: COLORS.treated }, { name: 'Current targeting', color: COLORS.holdout, dashed: true }]} />
            <LineChart
              ariaLabel="Profit added by budget, engine vs current targeting"
              labels={CURVE_STEPS.map((b) => eur(b))}
              xTitle="Monthly budget"
              height={250}
              series={[
                { name: 'Engine', color: COLORS.treated, values: curve.map((p) => p.engine) },
                { name: 'Current targeting', color: COLORS.holdout, dashed: true, values: curve.map((p) => p.rule) },
              ]}
              markers={[{ i: budgetIdx, v: curve[budgetIdx].engine, color: COLORS.treated, label: 'Your budget' }]}
              xEvery={1}
              yFormat={(v) => eur(v)}
            />
            <p className="small">
              At {eur(settings.budget, { exact: true })} the engine adds <strong>{eur(plan.total)}</strong> a month; current targeting adds <strong>{eur(base.total)}</strong>.
              {cap ? ` Returns flatten above about ${eur(cap.b)}: past that, no extra action pays for itself.` : ''}
            </p>
          </Card>
        </div>
      </div>

      <Card
        title="Action library"
        sub="What the engine can choose from. Edit costs or switch actions off and the plan updates."
        actions={
          <button type="button" className="btn primary" disabled={groups.length === 0} onClick={() => openPush({ title: 'Review and push this month’s plan', groups })}>
            Review and push plan
          </button>
        }
      >
        <div className="table-box">
          <table className="table">
            <thead>
              <tr>
                <th scope="col">On</th>
                <th scope="col">Action</th>
                <th scope="col">Channel</th>
                <th scope="col" className="num">Cost per send</th>
                <th scope="col" className="num">Offer</th>
                <th scope="col" className="num">Customers</th>
                <th scope="col" className="num">Spend</th>
                <th scope="col" className="num">Profit added</th>
              </tr>
            </thead>
            <tbody>
              {actions.map((a) => {
                const b = plan.byAction[a.id] || { count: 0, spend: 0, net: 0 };
                return (
                  <tr key={a.id} className={a.enabled ? '' : 'is-off'}>
                    <td>
                      <input type="checkbox" checked={a.enabled} onChange={(e) => setAction(a.id, { enabled: e.target.checked })} aria-label={`Use ${a.name}`} />
                    </td>
                    <td className="strong">{a.name}</td>
                    <td>{a.channel}</td>
                    <td className="num">
                      <span className="money-input">
                        €
                        <input
                          type="number"
                          min="0"
                          step="0.001"
                          value={a.sendCost}
                          onChange={(e) => setAction(a.id, { sendCost: Math.max(0, Number(e.target.value) || 0) })}
                          aria-label={`Cost per send for ${a.name}`}
                        />
                      </span>
                    </td>
                    <td className="num">
                      {a.shipping ? (
                        <span className="money-input">
                          €
                          <input type="number" min="0" step="0.1" value={a.shipping} onChange={(e) => setAction(a.id, { shipping: Math.max(0, Number(e.target.value) || 0) })} aria-label={`Shipping cost for ${a.name}`} />
                        </span>
                      ) : a.discountPct ? (
                        <span className="money-input">
                          <input
                            type="number"
                            min="0"
                            max="50"
                            step="1"
                            value={Math.round(a.discountPct * 100)}
                            onChange={(e) => setAction(a.id, { discountPct: Math.min(0.5, Math.max(0, (Number(e.target.value) || 0) / 100)) })}
                            aria-label={`Discount percent for ${a.name}`}
                          />
                          %
                        </span>
                      ) : (
                        <span className="muted">None</span>
                      )}
                    </td>
                    <td className="num">{int(b.count)}</td>
                    <td className="num">{eur(b.spend)}</td>
                    <td className="num strong">{eur(b.net)}</td>
                  </tr>
                );
              })}
              <tr>
                <td />
                <td className="strong">Do nothing</td>
                <td className="muted">—</td>
                <td className="num muted">€0</td>
                <td className="num muted">None</td>
                <td className="num">{int((plan.byAction.none || { count: 0 }).count)}</td>
                <td className="num">€0</td>
                <td className="num">€0</td>
              </tr>
            </tbody>
          </table>
        </div>
      </Card>

      <div className="grid-2">
        <Card title="Where the plan's profit comes from" sub="Per month, after all costs">
          <BarList
            items={[
              { label: 'Extra orders', value: plan.gain, color: COLORS.treated },
              { label: 'Paid returns avoided', value: plan.paidSaving, color: COLORS.owned },
              { label: 'Retargeting avoided', value: plan.retargetSaving, color: COLORS.owned },
              { label: 'Discounts and shipping', value: -plan.discountCost, color: COLORS.paid },
              { label: 'Message costs', value: -plan.sendCost, color: COLORS.paid },
              { label: 'Fatigue risk', value: -plan.backlash, color: COLORS.paid },
            ]}
            format={(v) => eur(v)}
            labelWidth={160}
          />
        </Card>
        <Card title="Push history" sub="Every approved push, newest first">
          {pushLog.length === 0 ? (
            <p className="muted">Nothing pushed yet. Review the plan to send the first audiences.</p>
          ) : (
            <ul className="log">
              {pushLog.slice(0, 8).map((p) => (
                <li key={p.id}>
                  <span>
                    <strong>{p.label}</strong>
                    <span className="muted small block">
                      {new Date(p.at).toLocaleString('en-GB', { dateStyle: 'medium', timeStyle: 'short' })} · {p.destination}
                      {p.excluded ? ` · ${int(p.excluded)} excluded from ads` : ''}
                    </span>
                  </span>
                  <span className="num">
                    {int(p.count)} <Pill tone="good">Pushed</Pill>
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  );
}
