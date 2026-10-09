import React, { useMemo, useState } from 'react';
import { useApp } from '../store.js';
import { GUARDRAILS, INTERIM_READS, LAYERS, powerPerArm, upliftTable } from '../engine/results.js';
import { eur, int, pct } from '../format.js';
import { ForestPlot } from '../components/charts.jsx';
import { Card, Pill, Segmented, Stat, TIER_COLOR } from '../components/ui.jsx';

const STATUS_TONE = { Keep: 'good', Demoted: 'bad', Collecting: 'neutral', 'Not running': 'neutral', 'Read on paid share': 'info' };

export default function Measurement() {
  const { summary: s } = useApp();
  const table = useMemo(() => upliftTable(s.byAction), [s.byAction]);
  const [sd, setSd] = useState(2.5);
  const [lift, setLift] = useState(0.05);
  const [hold, setHold] = useState('0.1');
  const n = powerPerArm({ sdMultiple: sd, relLift: lift });
  const base = Math.round(n / Number(hold));

  const forestRows = table
    .filter((r) => r.status !== 'Not running')
    .map((r) => ({
      label: `${r.a.id} · ${r.a.key}`,
      value: r.uplift * 100,
      low: r.low * 100,
      high: r.high * 100,
      color: TIER_COLOR[r.a.tier],
      note: `${r.status} · ${int(r.controlOutcomes)} control outcomes`,
    }));

  return (
    <div className="page">
      <header className="page-head">
        <h1>Measurement</h1>
        <p className="muted lead-narrow">
          The primary result is incremental 12-month net revenue per eligible customer: treatment minus a randomised global holdout, intention-to-treat. Every action also has its own randomised control, so each row of the decision table earns its place or is removed. Klaviyo-attributed conversions are reported, never used as the primary metric.
        </p>
      </header>

      <div className="grid-2">
        <Card title="Interim reads of the primary metric" sub="Net revenue per eligible customer, treatment − holdout, 95% interval; fixed read dates, no peeking">
          <ForestPlot
            ariaLabel="Interim reads at day 30, 60 and 90"
            rows={INTERIM_READS.map((r) => ({ label: `Day ${r.day}`, value: r.value, low: r.low, high: r.high, color: '#2a78d6', note: `capture beside it: ${eur(r.capture, { exact: true, digits: 2 })}` }))}
            format={(v) => eur(v, { exact: true, digits: 2 })}
            labelWidth={90}
            unit="€ per customer"
          />
          <p className="small">
            Day 30 still includes zero; days 60 and 90 sit above it. The day-90 report can state 30–90-day incremental revenue and every adoption, claim and activation metric with intervals. It cannot state the 12-month primary metric or the causal app effect, which arrive at month 12 of the Phase 2 cohorts.
          </p>
        </Card>

        <Card title="Experimental layers" sub="Assignment lives in the Decision Service and is written to the decision log and the profile">
          <ul className="layer-list">
            {LAYERS.map((l) => (
              <li key={l.layer}>
                <strong>{l.layer}</strong>
                <span className="small block">{l.assignment}</span>
                <span className="muted small block">Purpose: {l.purpose}</span>
              </li>
            ))}
          </ul>
        </Card>
      </div>

      <Card
        title="Per-action uplift from the action-level controls"
        sub="Outcome in the action’s own window, treated minus control, percentage points with 95% intervals. After 60 days these cells replace the fixed tier order within a tier; cells with fewer than 100 control outcomes fall back to their parent."
      >
        <ForestPlot ariaLabel="Uplift per action" rows={forestRows} format={(v) => `${v >= 0 ? '+' : ''}${v.toFixed(1)}`} labelWidth={260} unit="pp" width={1080} />
        <div className="table-box">
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Action</th>
                <th scope="col">Outcome window</th>
                <th scope="col" className="num">Treated</th>
                <th scope="col" className="num">Control</th>
                <th scope="col" className="num">Rate, treated vs control</th>
                <th scope="col" className="num">Net revenue per send</th>
                <th scope="col">Status</th>
              </tr>
            </thead>
            <tbody>
              {table.map((r) => (
                <tr key={r.a.id}>
                  <td>
                    <span className="code-id" style={{ borderColor: TIER_COLOR[r.a.tier] }}>{r.a.id}</span> <span className="small">{r.a.name}</span>
                  </td>
                  <td className="small">{r.a.metric.split(';')[0]}</td>
                  <td className="num">{int(r.nT)}</td>
                  <td className="num">{int(r.nC)}</td>
                  <td className="num">{pct(r.pT, 1)} vs {pct(r.pC, 1)}</td>
                  <td className="num">{eur(r.revenuePerSend, { exact: true, digits: 2 })}</td>
                  <td><Pill tone={STATUS_TONE[r.status]}>{r.status}</Pill></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="muted small">An action that cannot show revenue uplift in its control after 60 days is demoted to “no message” until its content or timing changes. Referral rows report referee revenue separately and never add it to the referrer’s incremental revenue.</p>
      </Card>

      <div className="grid-2">
        <Card title="Guardrails">
          <div className="table-box">
            <table className="table compact">
              <thead>
                <tr>
                  <th scope="col">Guardrail</th>
                  <th scope="col">Threshold</th>
                  <th scope="col">Now</th>
                  <th scope="col">If breached</th>
                </tr>
              </thead>
              <tbody>
                {GUARDRAILS.map((g) => (
                  <tr key={g.name}>
                    <td className="small strong">{g.name}</td>
                    <td className="small">{g.threshold}</td>
                    <td className="small">
                      <Pill tone={g.status === 'ok' ? 'good' : 'warn'}>{g.status === 'ok' ? 'Within' : 'Watch'}</Pill>
                      <span className="block">{g.current}</span>
                    </td>
                    <td className="small">{g.action}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>

        <Card title="Power: how many customers the revenue read needs" sub="n ≈ 2 (z₀.₉₇₅ + z₀.₈₀)² σ² / δ², with σ as a multiple of mean 90-day revenue">
          <label className="range">
            <span className="range-head">
              <span>Standard deviation of 90-day revenue (× mean)</span>
              <strong>{sd.toFixed(1)}×</strong>
            </span>
            <input type="range" min="1" max="4" step="0.1" value={sd} onChange={(e) => setSd(Number(e.target.value))} />
          </label>
          <label className="range">
            <span className="range-head">
              <span>Relative lift to detect</span>
              <strong>{pct(lift, 1)}</strong>
            </span>
            <input type="range" min="0.02" max="0.15" step="0.005" value={lift} onChange={(e) => setLift(Number(e.target.value))} />
          </label>
          <div className="setting">
            <span className="setting-label">Holdout share</span>
            <Segmented label="Holdout share" value={hold} onChange={setHold} options={[{ value: '0.1', label: '10%' }, { value: '0.2', label: '20%' }]} />
          </div>
          <div className="duo">
            <div className="duo-item">
              <span className="duo-label">Customers per arm</span>
              <span className="duo-value">{int(n)}</span>
            </div>
            <div className="duo-item is-accent">
              <span className="duo-label">Eligible base needed</span>
              <span className="duo-value">{int(base)}</span>
            </div>
          </div>
          <p className="small">With the design’s placeholders (σ 2.5× the mean, 5% lift) that is about 39,200 per arm and 390,000 eligible customers at a 10% holdout. With a smaller base, use 20% in Phase 1 and lean on binary action outcomes (top-up within 48 h, install within 72 h), which need far fewer customers.</p>
        </Card>
      </div>

      <Card title="Does the app cause higher LTV?" sub="Assumption A4">
        <div className="note-grid">
          <div>
            <span className="muted small">Descriptive gap today</span>
            <p className="lead">€93.60 vs €51.00</p>
            <p className="small">12-month LTV of in-app vs non-app buyers. A correlation: app installers may already be more frequent travellers.</p>
          </div>
          <div>
            <span className="muted small">Causal estimate</span>
            <p className="lead">Pending E4</p>
            <p className="small">First-time web buyers randomised to receive P2 and T3 or not. Assignment as an instrument gives the effect of adoption on customers the message moved (LATE). Interim at day 90; the 12-month read sets w_app.</p>
          </div>
          <div>
            <span className="muted small">Leakage rules</span>
            <p className="small">Features frozen at decided_at; outcome windows fixed per action and tied to the trip_id at decision time; uplift only from randomised rows; holdout excluded from campaigns too; app and non-app users never compared descriptively.</p>
          </div>
        </div>
      </Card>
    </div>
  );
}
