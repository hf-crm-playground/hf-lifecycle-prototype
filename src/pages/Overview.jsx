import React from 'react';
import { useApp } from '../store.js';
import { PHASES, ACTION_BY_ID, REASON_TEXT } from '../engine/actions.js';
import { ROLLOUT } from '../engine/content.js';
import { INTERIM_READS, GUARDRAILS, cumulativeSeries } from '../engine/results.js';
import { eur, int, pct } from '../format.js';
import { BarList, LineChart } from '../components/charts.jsx';
import { Card, COLORS, Pill, Stat, TIER_COLOR } from '../components/ui.jsx';

const REASON_SHORT = {
  NO_ELIGIBLE_ACTION: 'Nothing useful to say now',
  HOLDOUT: 'Global holdout',
  CAP_REACHED: 'Cap of 8 reached',
  CARE_OPEN: 'Open care conversation',
  NO_DELIVERABLE_CHANNEL: 'No deliverable channel',
  REFUNDED: 'Refunded order',
  ACTION_CONTROL: 'Action-level control',
  SCORE_NOT_POSITIVE: 'S(a) not above zero',
  OPERATOR_EXCLUDE: 'Excluded by operator',
};

export default function Overview() {
  const { decisions, summary: s, travellers, phaseNow } = useApp();
  const read = INTERIM_READS[INTERIM_READS.length - 1];
  const series = cumulativeSeries(13);
  const avgSends = travellers.reduce((t, c) => t + c.sends30d.total, 0) / travellers.length;
  const phase = ROLLOUT.find((p) => p.id === phaseNow) || ROLLOUT[1];

  const tierItems = [0, 1, 2, 3]
    .map((t) => ({ label: `Tier ${t}`, value: s.byTier[t] || 0, color: TIER_COLOR[t], tip: ['Keep connected', 'App habit', 'Growth', 'Advocacy'][t] }))
    .concat([{ label: 'Fixed rule (F1, R1)', value: s.byTier.fixed || 0, color: TIER_COLOR.fixed }]);
  const reasonItems = Object.entries(s.byReason)
    .sort((a, b) => b[1] - a[1])
    .map(([k, v]) => ({ label: REASON_SHORT[k] || k, value: v, color: COLORS.holdout, tip: REASON_TEXT[k] }));

  const actionRows = Object.entries(s.byAction)
    .map(([id, n]) => {
      const ds = decisions.filter((d) => d.action === id);
      const ev = ds.reduce((t, d) => t + (d.score?.total || 0), 0);
      const ch = {};
      ds.forEach((d) => (ch[d.channel] = (ch[d.channel] || 0) + 1));
      return { id, a: ACTION_BY_ID[id], n, ev, ch };
    })
    .sort((a, b) => (a.a.tier ?? 9) - (b.a.tier ?? 9) || b.n - a.n);

  return (
    <div className="page">
      <header className="page-head">
        <div className="title-row">
          <h1>Trip NBA overview</h1>
          <Pill tone="warn">Sample data</Pill>
          <Pill tone="info">
            Phase {phase.id}: {phase.name}
          </Pill>
        </div>
        <p className="muted lead-narrow">
          One objective: net contribution per customer, meaning incremental 12-month net revenue plus the paid-acquisition cost avoided when a message wins a click a paid channel would otherwise take. The trip is the unit of decisioning, and “no message” is always a candidate.
        </p>
      </header>

      <div className="stats">
        <Stat
          label={`Incremental net revenue per eligible customer (day ${read.day})`}
          value={eur(read.value, { exact: true, digits: 2 })}
          note={`95% interval ${eur(read.low, { exact: true, digits: 2 })} to ${eur(read.high, { exact: true, digits: 2 })} vs global holdout`}
          tone="good"
        />
        <Stat label="Paid acquisition cost avoided per eligible customer" value={eur(read.capture, { exact: true, digits: 2 })} note="Capture value, reported beside, never inside, attributed CRM revenue" tone="good" />
        <Stat label="Decision points with a message" value={pct(s.sent / s.total)} note={`${int(s.sent)} of ${int(s.total)} this run; “no message” won ${int(s.none)}`} />
        <Stat label="Marketing sends per customer, last 30 days" value={avgSends.toFixed(1)} note="Cap 8 across email, push and WhatsApp; 0 customers above it" />
      </div>

      <div className="grid-2">
        <Card title="Cumulative incremental net revenue per eligible customer" sub="Treatment minus the 10% global holdout, intention-to-treat, with the fixed interim reads marked">
          <LineChart
            ariaLabel="Cumulative incremental net revenue per eligible customer by week"
            labels={series.map((p) => `Week ${p.week}`)}
            series={[{ name: 'Incremental net revenue', color: COLORS.treated, values: series.map((p) => p.incr) }]}
            markers={INTERIM_READS.map((r, k) => {
              const i = series.findIndex((p) => p.day >= r.day);
              return { i: i < 0 ? series.length - 1 : i, v: series[i < 0 ? series.length - 1 : i].incr, color: COLORS.treated, label: `Day ${r.day}: ${eur(r.value, { exact: true, digits: 2 })}`, row: k };
            })}
            yFormat={(v) => eur(v, { exact: true, digits: 2 })}
          />
          <p className="muted small">Day 30 still includes zero (95% interval {eur(INTERIM_READS[0].low, { exact: true, digits: 2 })} to {eur(INTERIM_READS[0].high, { exact: true, digits: 2 })}); days 60 and 90 sit above it. The 12-month primary read arrives at month 12 of the Phase 2 cohorts.</p>
        </Card>

        <Card title="This run’s decision points" sub={`${int(s.total)} travellers with a decision point · ${int(s.held)} sends held for the local send window`}>
          <h3 className="h3">Message sent, by tier</h3>
          <BarList items={tierItems} format={int} labelWidth={150} />
          <h3 className="h3">No message, by reason</h3>
          <BarList items={reasonItems} format={int} labelWidth={190} />
        </Card>

        <Card title="Travellers by trip phase" sub="From the Trip State Builder; share of each phase that gets a message this run">
          <BarList
            items={PHASES.map((p) => {
              const ph = s.byPhase[p.id] || { total: 0, sent: 0 };
              return { label: p.label, value: ph.total, color: COLORS.treated, display: `${int(ph.total)} · ${pct(ph.sent / Math.max(ph.total, 1))} sent` };
            })}
            format={int}
            labelWidth={170}
            valueWidth={120}
          />
          <p className="muted small">In-trip gets the most messages because Tier 0 connectivity actions have the shortest outcomes and a reserved budget.</p>
        </Card>

        <Card title="Guardrails" sub="Breaching one pauses the action or lowers a sub-cap" actions={<a href="#/measurement" className="btn small">Measurement</a>}>
          <ul className="guardrail-list">
            {GUARDRAILS.map((g) => (
              <li key={g.name}>
                <Pill tone={g.status === 'ok' ? 'good' : 'warn'}>{g.status === 'ok' ? 'Within' : 'Watch'}</Pill>
                <span>
                  <strong>{g.name}</strong>
                  <span className="muted small block">
                    {g.current} · threshold: {g.threshold}
                  </span>
                </span>
              </li>
            ))}
          </ul>
        </Card>
      </div>

      <Card title="Actions chosen this run" sub="Ranked by tier, then the decision table’s order (policy v1). Expected value is the sum of S(a) for the sends." actions={<a href="#/policy" className="btn small">Decision policy</a>}>
        <div className="table-box">
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Action</th>
                <th scope="col">Tier</th>
                <th scope="col">Phase</th>
                <th scope="col">Channels</th>
                <th scope="col" className="num">Sends</th>
                <th scope="col" className="num">Expected S(a)</th>
              </tr>
            </thead>
            <tbody>
              {actionRows.map((r) => (
                <tr key={r.id}>
                  <td>
                    <span className="code-id">{r.id}</span> {r.a.name}
                    <span className="muted small block mono">{r.a.key}</span>
                  </td>
                  <td>
                    <span className="tier-dot" style={{ background: TIER_COLOR[r.a.tier ?? 'fixed'] }} /> {r.a.tier ?? 'Fixed'}
                  </td>
                  <td>{PHASES.find((p) => p.id === r.a.phase)?.label || (r.id === 'F1' ? 'Trip end' : 'Post-trip')}</td>
                  <td className="small">{Object.entries(r.ch).map(([k, v]) => `${k.replace('_', ' ')} ${v}`).join(' · ')}</td>
                  <td className="num">{int(r.n)}</td>
                  <td className="num strong">{r.a.tier === undefined ? 'Not scored' : eur(r.ev)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
