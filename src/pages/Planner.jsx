import React from 'react';
import { usePersisted } from '../store.js';
import { DEFAULT_PLANNER, acquisitionMarginal, bestSplit, retentionMarginal, totalProfit } from '../planner.js';
import { eur } from '../format.js';
import { LineChart } from '../components/charts.jsx';
import { Card, COLORS, Stat } from '../components/ui.jsx';

export default function Planner() {
  const [p, setP] = usePersisted('gc.planner.v1', DEFAULT_PLANNER);
  const total = p.total;
  const retention = Math.min(p.retention, total);
  const best = bestSplit(total);
  const now = totalProfit(total, retention);
  const gain = best.profit - now;
  const move = best.retention - retention;
  const rM = retentionMarginal(retention);
  const aM = acquisitionMarginal(total - retention);

  const step = Math.max(500, Math.round(total / 40 / 500) * 500);
  const xs = [];
  for (let r = 0; r <= total; r += step) xs.push(r);
  const idx = (v) => xs.reduce((b, x, i) => (Math.abs(x - v) < Math.abs(xs[b] - v) ? i : b), 0);

  return (
    <div className="page">
      <header className="page-head">
        <h1>Next-Euro Planner</h1>
        <p className="muted">Compares what one more euro earns in retention and in acquisition, on the same scale: profit per month.</p>
      </header>

      <div className="grid-opt">
        <Card title="Your split">
          <label className="field">
            <span>Total monthly marketing budget</span>
            <span className="money-input wide">
              €
              <input
                type="number"
                min="5000"
                max="200000"
                step="1000"
                value={total}
                onChange={(e) => {
                  const t = Math.max(5000, Math.min(200000, Number(e.target.value) || 0));
                  setP({ total: t, retention: Math.min(retention, t) });
                }}
              />
            </span>
          </label>
          <label className="range">
            <span className="range-head">
              <span>Retention share</span>
              <strong>
                {eur(retention, { exact: true })} · {Math.round((retention / total) * 100)}%
              </strong>
            </span>
            <input type="range" min="0" max={total} step="250" value={retention} onChange={(e) => setP({ total, retention: Number(e.target.value) })} />
            <span className="muted small">Acquisition gets the rest: {eur(total - retention, { exact: true })}</span>
          </label>
          <button type="button" className="btn" onClick={() => setP({ total, retention: best.retention })} disabled={Math.abs(move) < 250}>
            Use the best split
          </button>
        </Card>

        <div className="stack">
          <div className="stats is-3">
            <Stat label="Next €1 in retention" value={eur(rM, { exact: true, digits: 2 })} note="profit per month" tone={rM >= aM ? 'good' : undefined} />
            <Stat label="Next €1 in acquisition" value={eur(aM, { exact: true, digits: 2 })} note="profit per month" tone={aM > rM ? 'good' : undefined} />
            <Stat
              label="Gain from the best split"
              value={eur(Math.max(0, gain))}
              note={Math.abs(move) < 250 ? 'You are at the best split' : `Move ${eur(Math.abs(move))} to ${move > 0 ? 'retention' : 'acquisition'}`}
              tone="good"
            />
          </div>
          <Card title="Profit after marketing spend, by retention budget" sub="Same total budget; only the split changes">
            <LineChart
              ariaLabel="Profit after spend by retention budget"
              labels={xs.map((x) => eur(x))}
              xTitle="Retention budget per month"
              height={260}
              series={[{ name: 'Profit after spend', color: COLORS.treated, values: xs.map((x) => totalProfit(total, x)) }]}
              markers={[
                { i: idx(retention), v: totalProfit(total, xs[idx(retention)]), color: COLORS.paid, label: 'Today', row: 0 },
                { i: idx(best.retention), v: totalProfit(total, xs[idx(best.retention)]), color: COLORS.owned, label: 'Best split', row: 1 },
              ]}
              yFormat={(v) => eur(v)}
            />
            <p className="small">
              Returns fall as each budget grows. The best split is where the next euro earns the same in both. Curves are sample estimates; in a pilot they come from holdout tests and the media-mix model.
            </p>
          </Card>
        </div>
      </div>
    </div>
  );
}
