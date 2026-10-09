import React, { useState } from 'react';
import { DRIVERS } from '../data.js';
import { pct } from '../format.js';
import { BarList } from '../components/charts.jsx';
import { Card, COLORS, Segmented } from '../components/ui.jsx';

export default function Drivers() {
  const [model, setModel] = useState('uplift');
  const [pick, setPick] = useState(0);
  const m = DRIVERS[model];
  const sel = m.items[Math.min(pick, m.items.length - 1)];

  return (
    <div className="page">
      <header className="page-head">
        <h1>Drivers</h1>
        <p className="muted">What each model relies on most, so the team can sanity-check the decisions.</p>
      </header>

      <div className="filters">
        <Segmented
          label="Model"
          value={model}
          onChange={(v) => {
            setModel(v);
            setPick(0);
          }}
          options={Object.entries(DRIVERS).map(([value, d]) => ({ value, label: d.label }))}
        />
      </div>

      <div className="grid-2">
        <Card title={`${m.label}: what matters most`} sub={m.note}>
          <div className="driver-list">
            {m.items.map((d, i) => (
              <button key={d.name} type="button" className={`driver-row${i === pick ? ' is-active' : ''}`} onClick={() => setPick(i)} aria-pressed={i === pick}>
                <span className="driver-name">{d.name}</span>
                <span className="driver-track">
                  <span style={{ width: `${(d.weight / m.items[0].weight) * 100}%`, background: i === pick ? COLORS.treated : '#b9b0f2' }} />
                </span>
                <span className="driver-val">{pct(d.weight)}</span>
              </button>
            ))}
          </div>
        </Card>
        <Card title={sel.name} sub={`${pct(sel.weight)} of the model's predictive weight`}>
          <p className="lead">{sel.read}</p>
          <h3 className="h3">How to use it</h3>
          <ul className="reasons">
            <li>Check that the direction matches what the team sees in practice.</li>
            <li>If a driver looks wrong, flag it: it usually means a data problem, not a new insight.</li>
            <li>Weights are refreshed each month when the models retrain.</li>
          </ul>
          <h3 className="h3">All three models</h3>
          <BarList
            items={Object.entries(DRIVERS).map(([k, d]) => ({ label: d.label, value: d.items[0].weight, color: k === model ? COLORS.treated : '#b9b0f2', display: d.items[0].name }))}
            labelWidth={150}
            valueWidth={200}
          />
          <p className="muted small">Top driver per model.</p>
        </Card>
      </div>
    </div>
  );
}
