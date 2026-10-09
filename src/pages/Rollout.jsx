import React, { useState } from 'react';
import { useApp } from '../store.js';
import { ASSUMPTIONS, EXPERIMENTS, GAPS, ROLLOUT } from '../engine/content.js';
import { Card, Pill, Segmented, Stat } from '../components/ui.jsx';

const STATUSES = ['Open', 'In progress', 'Resolved'];
const TONE = { Open: 'bad', 'In progress': 'warn', Resolved: 'good' };

export default function Rollout() {
  const { phaseNow, setPhaseNow, gapStatus, setGapStatus } = useApp();
  const [filter, setFilter] = useState('blocking');
  const statusOf = (n) => gapStatus[n] || 'Open';
  const blockingNext = GAPS.filter((g) => g.by <= phaseNow + 1 && statusOf(g.n) !== 'Resolved');
  const shown = GAPS.filter((g) => (filter === 'all' ? true : filter === 'blocking' ? g.by <= phaseNow + 1 : statusOf(g.n) !== 'Resolved'));
  const resolved = GAPS.filter((g) => statusOf(g.n) === 'Resolved').length;
  const phase = ROLLOUT[phaseNow];

  return (
    <div className="page">
      <header className="page-head">
        <h1>Rollout and gaps</h1>
        <p className="muted lead-narrow">
          In-trip goes first because run-out and activation decisions have outcomes within 48–72 hours and produce the fastest evidence. Each phase has an exit gate; no phase starts on a calendar date alone.
        </p>
      </header>

      <div className="filters">
        <div className="setting">
          <span className="setting-label">Where we are</span>
          <Segmented label="Current phase" value={String(phaseNow)} onChange={(v) => setPhaseNow(Number(v))} options={ROLLOUT.map((p) => ({ value: String(p.id), label: `Phase ${p.id}` }))} />
        </div>
      </div>

      <Card title="90-day plan" sub="Phases by week, with the gate that ends each one">
        <Timeline current={phaseNow} />
      </Card>

      <div className="stats">
        <Stat label={`Phase ${phase.id}: ${phase.name}`} value={`Weeks ${phase.weeks[0]}–${phase.weeks[1]}`} note={phase.experiments} />
        <Stat label="Exit gate" value={phaseNow === 3 ? 'Day-90 read' : `Phase ${phaseNow + 1}`} note={phase.gate} />
        <Stat label={`Gaps needed by Phase ${Math.min(phaseNow + 1, 3)} still open`} value={String(blockingNext.length)} note={blockingNext.slice(0, 3).map((g) => `#${g.n}`).join(', ') + (blockingNext.length > 3 ? ' …' : '')} tone={blockingNext.length ? undefined : 'good'} />
        <Stat label="Gaps resolved" value={`${resolved} of ${GAPS.length}`} note="Status is kept in this browser" />
      </div>

      <div className="grid-2">
        {ROLLOUT.map((p) => (
          <Card key={p.id} title={`Phase ${p.id} · ${p.name}`} sub={`Weeks ${p.weeks[0]}–${p.weeks[1]}`} className={p.id === phaseNow ? 'is-current' : ''} actions={p.id === phaseNow ? <Pill tone="info">Now</Pill> : p.id < phaseNow ? <Pill tone="good">Done</Pill> : null}>
            <p className="small">{p.scope}</p>
            <p className="small"><strong>Experiments:</strong> {p.experiments}</p>
            <p className="small"><strong>Exit gate:</strong> {p.gate}</p>
          </Card>
        ))}
      </div>

      <Card title="Experiments" sub="Each is read on the outcome it names; none changes who receives R1 or when">
        <div className="table-box">
          <table className="table">
            <thead>
              <tr>
                <th scope="col">ID</th>
                <th scope="col">Phase</th>
                <th scope="col">Test</th>
                <th scope="col">Read on</th>
                <th scope="col">Status</th>
              </tr>
            </thead>
            <tbody>
              {EXPERIMENTS.map((e) => (
                <tr key={e.id}>
                  <td className="strong">{e.id}</td>
                  <td>{e.phase}</td>
                  <td className="small">{e.test}</td>
                  <td className="small">{e.metric}</td>
                  <td>{e.phase < phaseNow ? <Pill tone="good">Read</Pill> : e.phase === phaseNow ? <Pill tone="info">Running</Pill> : <Pill>Planned</Pill>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <Card
        title="Data gaps and decisions Holafly must resolve"
        sub="Four gaps block Phase 1 outright: identity, daily usage, the transactional boundary and the net-revenue definition."
        actions={
          <Segmented
            label="Show"
            value={filter}
            onChange={setFilter}
            options={[
              { value: 'blocking', label: `Needed by Phase ${Math.min(phaseNow + 1, 3)}` },
              { value: 'open', label: 'Not resolved' },
              { value: 'all', label: 'All 24' },
            ]}
          />
        }
      >
        <div className="table-box">
          <table className="table">
            <thead>
              <tr>
                <th scope="col">#</th>
                <th scope="col">Gap or decision</th>
                <th scope="col">Why it matters</th>
                <th scope="col">Owner</th>
                <th scope="col">Needed by</th>
                <th scope="col">Status</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((g) => (
                <tr key={g.n}>
                  <td className="strong">{g.n}</td>
                  <td className="small">{g.gap}</td>
                  <td className="small">{g.why}</td>
                  <td className="small">{g.owner}</td>
                  <td>Phase {g.by}</td>
                  <td>
                    <label className="status-select">
                      <span className="sr-only">Status of gap {g.n}</span>
                      <select value={statusOf(g.n)} onChange={(e) => setGapStatus({ ...gapStatus, [g.n]: e.target.value })} className={`is-${TONE[statusOf(g.n)]}`}>
                        {STATUSES.map((st) => (
                          <option key={st}>{st}</option>
                        ))}
                      </select>
                    </label>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <Card title="Assumptions to confirm before build">
        <ul className="layer-list">
          {ASSUMPTIONS.map((a) => (
            <li key={a.id}>
              <strong>{a.id}</strong>
              <span className="small block">{a.text}</span>
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}

function Timeline({ current }) {
  const left = 24;
  const right = 736;
  const wk = (w) => left + ((w - 1) / 13) * (right - left);
  const y0 = 56;
  const h = 64;
  return (
    <svg viewBox="0 0 760 196" role="img" aria-label="Rollout phases 0 to 3 across 13 weeks, each ending in a gate" className="chart">
      {Array.from({ length: 14 }, (_, i) => i + 1).map((w) => (
        <g key={w}>
          <line x1={wk(w)} x2={wk(w)} y1={y0 - 8} y2={y0 + h + 8} className="grid" />
          {w <= 13 && (
            <text x={(wk(w) + wk(w + 1)) / 2} y={y0 - 14} textAnchor="middle" className="tick">
              {w}
            </text>
          )}
        </g>
      ))}
      <text x={left} y={20} className="tick">Week</text>
      {ROLLOUT.map((p) => {
        const x = wk(p.weeks[0]) + 2;
        const w = wk(p.weeks[1] + 1) - wk(p.weeks[0]) - 4;
        const on = p.id === current;
        const done = p.id < current;
        return (
          <g key={p.id}>
            <rect x={x} y={y0} width={w} height={h} rx="10" fill={on ? '#2a78d6' : done ? '#dbe7f8' : '#f1f1f4'} stroke={on ? '#2a78d6' : '#d8d8e0'} />
            <text x={x + 10} y={y0 + 24} className="tl-name" fill={on ? '#fff' : '#292b2e'}>
              Phase {p.id}
            </text>
            <text x={x + 10} y={y0 + 44} className="tl-sub" fill={on ? '#fff' : '#5c6066'}>
              {p.name.length > Math.floor(w / 6.3) ? `${p.name.slice(0, Math.floor(w / 6.3) - 1)}…` : p.name}
            </text>
            <path d={`M${wk(p.weeks[1] + 1)} ${y0 + h + 14}l8 8l-8 8l-8 -8z`} fill={done || on ? '#1a6b37' : '#8a8996'} />
            <text x={wk(p.weeks[1] + 1) - 12} y={y0 + h + 27} textAnchor="end" className="tick">
              {p.id === 3 ? 'Day-90 read' : `Gate ${p.id}→${p.id + 1}`}
            </text>
          </g>
        );
      })}
    </svg>
  );
}
