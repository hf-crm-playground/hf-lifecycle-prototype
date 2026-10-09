import React, { useMemo, useState } from 'react';
import { useApp, usePersisted } from '../store.js';
import { DEFAULT_PLANNER, acquisitionMarginal, retentionMarginal } from '../planner.js';
import { aggregate, periodTotals, weeklySeries, PAID_CPA } from '../model.js';
import { STAGES } from '../data.js';
import { eur, int } from '../format.js';
import { audienceGroups, pushedThisCycle } from '../groups.js';
import { BarList, LineChart, Legend } from '../components/charts.jsx';
import { Card, COLORS, Pill, Segmented, Select, Stat } from '../components/ui.jsx';

const ROUTE_COSTS = [
  { label: 'Paid search', value: PAID_CPA * 1.25, color: COLORS.paid },
  { label: 'Retargeting', value: PAID_CPA * 0.75, color: COLORS.paid },
  { label: 'SMS', value: 0.9, color: COLORS.owned },
  { label: 'Email', value: 0.4, color: COLORS.owned },
  { label: 'Push', value: 0.05, color: COLORS.owned },
];

export default function Summary() {
  const { planRows, baseRows, actions, pushLog, openPush, customers } = useApp();
  const [weeks, setWeeks] = useState('13');
  const [stage, setStage] = useState('all');
  const [channel, setChannel] = useState('all');
  const [tab, setTab] = useState('overview');
  const [planner] = usePersisted('gc.planner.v1', DEFAULT_PLANNER);
  const rM = retentionMarginal(planner.retention);
  const aM = acquisitionMarginal(planner.total - planner.retention);

  const w = Number(weeks);
  const channelOf = (id) => actions.find((a) => a.id === id)?.channel;
  const filter = (r) => (stage === 'all' || r.c.stage === stage) && (channel === 'all' || r.holdout || r.action === 'none' || channelOf(r.action) === channel);

  const plan = useMemo(() => aggregate(planRows, filter), [planRows, stage, channel, actions]);
  const base = useMemo(() => aggregate(baseRows, (r) => stage === 'all' || r.c.stage === stage), [baseRows, stage]);
  const totals = periodTotals(plan, w);
  const series = weeklySeries(plan, 13).slice(13 - w);
  const discountSaved = Math.max(0, base.wasted - plan.wasted) * totals.factor;
  const roi = totals.spend > 0 ? totals.profit / totals.spend : 0;

  const stageCounts = STAGES.map((s) => ({ s, n: customers.filter((c) => c.stage === s).length }));
  const groups = audienceGroups(planRows, actions, filter).slice(0, 6);

  return (
    <div className="page">
      <header className="page-head">
        <div className="title-row">
          <h1>Executive Summary</h1>
          <Pill tone="warn">Sample data</Pill>
        </div>
        <p className="muted">How much profit lifecycle marketing added, measured against a random holdout, and what to do next.</p>
      </header>

      <div className="filters">
        <Select label="Period" value={weeks} onChange={setWeeks} options={[{ value: '13', label: 'Last 90 days' }, { value: '4', label: 'Last 30 days' }]} />
        <Select label="Lifecycle stage" value={stage} onChange={setStage} options={[{ value: 'all', label: 'All stages' }, ...STAGES.map((s) => ({ value: s, label: s }))]} />
        <Select label="Channel" value={channel} onChange={setChannel} options={[{ value: 'all', label: 'All channels' }, { value: 'Email', label: 'Email' }, { value: 'SMS', label: 'SMS' }, { value: 'Push', label: 'Push' }]} />
        <div className="filters-end">
          <Segmented label="View" value={tab} onChange={setTab} options={[{ value: 'overview', label: 'Overview' }, { value: 'stages', label: 'By stage' }]} />
        </div>
      </div>

      <div className="stats">
        <Stat label="Profit added vs holdout" value={eur(totals.profit)} note={roi ? `${eur(roi, { exact: true, digits: 2 })} per €1 of lifecycle spend` : 'No spend in this view'} tone="good" />
        <Stat label="Paid cost avoided" value={eur(totals.paid)} note="Returns through owned channels instead of ads" tone="good" />
        <Stat label="Wasted discount avoided" value={eur(discountSaved)} note="Offers no longer given to customers who buy anyway, vs a 10% offer to at-risk customers" tone="good" />
        <Stat label="Extra orders" value={int(totals.purchases)} note={`Above the holdout rate · ${int(plan.treated)} customers in this month's plan`} />
      </div>

      {tab === 'overview' ? (
        <div className="grid-2">
          <Card title="Weekly profit per customer" sub="Customers in the plan vs the random holdout. The shaded gap is the profit added.">
            <Legend items={[{ name: 'In the plan', color: COLORS.treated }, { name: 'Holdout', color: COLORS.holdout, dashed: true }]} />
            <LineChart
              ariaLabel="Weekly profit per customer, plan vs holdout"
              labels={series.map((s) => `Week ${s.week}`)}
              series={[
                { name: 'In the plan', color: COLORS.treated, values: series.map((s) => s.treated) },
                { name: 'Holdout', color: COLORS.holdout, dashed: true, values: series.map((s) => s.holdout) },
              ]}
              shade={[0, 1]}
              fromZero={false}
              yFormat={(v) => eur(v, { exact: true, digits: 2 })}
            />
          </Card>

          <Card title="Cost to bring a customer back" sub="Average cost per returning existing customer, by route">
            <BarList items={ROUTE_COSTS} format={(v) => eur(v, { exact: true, digits: 2 })} labelWidth={100} />
            <div className="card-foot">
              <Legend items={[{ name: 'Paid', color: COLORS.paid }, { name: 'Owned', color: COLORS.owned }]} />
              <span className="strong">{int(plan.excluded)} customers kept out of paid audiences</span>
            </div>
          </Card>

          <Card title="Lifecycle stages" sub="Customers by stage today">
            <BarList
              items={stageCounts.map(({ s, n }) => ({ label: s, value: n, color: COLORS.stage, muted: stage !== 'all' && stage !== s }))}
              format={int}
              labelWidth={90}
            />
          </Card>

          <Card title="Where the next euro goes" sub="Profit from one more euro, at today's spend" actions={<a href="#/planner" className="btn small">Open the planner</a>}>
            <div className="duo">
              <div className={`duo-item${rM >= aM ? ' is-accent' : ''}`}>
                <span className="duo-label">Retention</span>
                <span className="duo-value">{eur(rM, { exact: true, digits: 2 })}</span>
                <span className="muted small">per extra €1</span>
              </div>
              <div className={`duo-item${aM > rM ? ' is-accent' : ''}`}>
                <span className="duo-label">Acquisition</span>
                <span className="duo-value">{eur(aM, { exact: true, digits: 2 })}</span>
                <span className="muted small">per extra €1</span>
              </div>
            </div>
            <p className="small">
              {Math.abs(rM - aM) < 0.08
                ? 'The split is close to balanced: both earn about the same on the next euro.'
                : rM > aM
                  ? 'Retention is under-funded at the current split. The planner shows how much to move.'
                  : 'Acquisition earns more on the next euro at the current split.'}
            </p>
          </Card>
        </div>
      ) : (
        <Card title="Plan and results by lifecycle stage" sub="This month's plan · profit added per month, before ramp-up">
          <div className="table-box">
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">Stage</th>
                  <th scope="col" className="num">Customers</th>
                  <th scope="col" className="num">In the plan</th>
                  <th scope="col" className="num">Holdout</th>
                  <th scope="col" className="num">Spend</th>
                  <th scope="col" className="num">Profit added</th>
                </tr>
              </thead>
              <tbody>
                {STAGES.map((s) => {
                  const a = aggregate(planRows, (r) => r.c.stage === s);
                  return (
                    <tr key={s}>
                      <td>{s}</td>
                      <td className="num">{int(a.customers)}</td>
                      <td className="num">{int(a.treated)}</td>
                      <td className="num">{int(a.holdout)}</td>
                      <td className="num">{eur(a.spend)}</td>
                      <td className="num strong">{eur(a.total)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      <Card
        title="Recommended actions this month"
        sub="Ranked by expected profit added. Approve to push to your email platform and ad exclusions."
        actions={
          <button type="button" className="btn primary" disabled={groups.length === 0} onClick={() => openPush({ title: 'Review and push', groups })}>
            Review all
          </button>
        }
      >
        <div className="table-box">
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Audience</th>
                <th scope="col">Action</th>
                <th scope="col" className="num">Customers</th>
                <th scope="col" className="num">Cost</th>
                <th scope="col" className="num">Expected profit added</th>
                <th scope="col">Status</th>
                <th scope="col">
                  <span className="sr-only">Review</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {groups.length === 0 && (
                <tr>
                  <td colSpan={7} className="muted">No actions match these filters.</td>
                </tr>
              )}
              {groups.map((g) => {
                const done = pushedThisCycle(pushLog, g.key);
                return (
                  <tr key={g.key}>
                    <td>{g.stage}</td>
                    <td>{g.actionName}</td>
                    <td className="num">{int(g.count)}</td>
                    <td className="num">{eur(g.spend)}</td>
                    <td className="num strong">{eur(g.net)}</td>
                    <td>{done ? <Pill tone="good">Pushed</Pill> : <Pill tone="warn">Needs review</Pill>}</td>
                    <td className="num">
                      <button type="button" className="btn small" onClick={() => openPush({ title: `Review: ${g.label}`, groups: [g] })}>
                        {done ? 'Push again' : 'Review'}
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
