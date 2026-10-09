import React, { useRef, useState } from 'react';

function niceStep(range) {
  const raw = range / 4;
  const p = Math.pow(10, Math.floor(Math.log10(raw)));
  const n = raw / p;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * p;
}

function niceMax(v) {
  if (v <= 0) return 1;
  const p = Math.pow(10, Math.floor(Math.log10(v)));
  const n = v / p;
  const step = n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10;
  return step * p;
}

// Line chart with a crosshair tooltip. One y-axis only.
export function LineChart({ labels, series, height = 240, yFormat = (v) => v, shade, markers = [], xEvery, ariaLabel, xTitle, fromZero = true }) {
  const ref = useRef(null);
  const [hover, setHover] = useState(null);
  const W = 640;
  const H = height;
  const padL = 52;
  const padR = 18;
  const padT = 18;
  const padB = xTitle ? 46 : 30;
  const all = series.flatMap((s) => s.values);
  const hi = Math.max(...all);
  let lo = Math.min(...all);
  if (fromZero) lo = lo < 0 && -lo > 0.03 * hi ? lo : 0;
  else lo = lo - (hi - lo) * 0.6;
  const stepV = niceStep(Math.max(hi - lo, 1e-6) * 1.05);
  const min = Math.floor(lo / stepV) * stepV;
  const max = Math.ceil((hi * 1.04) / stepV) * stepV;
  const n = labels.length;
  const x = (i) => padL + (n === 1 ? 0 : (i * (W - padL - padR)) / (n - 1));
  const y = (v) => padT + (1 - (Math.max(v, min) - min) / (max - min)) * (H - padT - padB);
  const ticks = [];
  for (let t = min; t <= max + stepV / 2; t += stepV) ticks.push(t);
  const every = xEvery || Math.max(1, Math.ceil(n / 6));
  const path = (vals) => vals.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join('');

  const onMove = (e) => {
    const r = ref.current.getBoundingClientRect();
    const px = ((e.clientX - r.left) / r.width) * W;
    const i = Math.round(((px - padL) / (W - padL - padR)) * (n - 1));
    setHover(Math.max(0, Math.min(n - 1, i)));
  };

  let shadePath = null;
  if (shade) {
    const top = series[shade[0]].values;
    const bot = series[shade[1]].values;
    shadePath = path(top) + bot.map((v, i) => `L${x(n - 1 - i).toFixed(1)} ${y(bot[n - 1 - i]).toFixed(1)}`).join('') + 'Z';
  }

  return (
    <div className="chart-wrap">
      <svg
        ref={ref}
        viewBox={`0 0 ${W} ${H}`}
        role="img"
        aria-label={ariaLabel}
        className="chart"
        onMouseMove={onMove}
        onMouseLeave={() => setHover(null)}
      >
        {ticks.map((t, i) => (
          <g key={i}>
            <line x1={padL} x2={W - padR} y1={y(t)} y2={y(t)} className={Math.abs(t) < stepV / 2 || (i === 0 && min > 0) ? 'axis' : 'grid'} />
            <text x={padL - 8} y={y(t) + 4} textAnchor="end" className="tick">
              {yFormat(t)}
            </text>
          </g>
        ))}
        {labels.map((l, i) =>
          i % every === 0 || i === n - 1 ? (
            <text key={i} x={x(i)} y={H - padB + 18} textAnchor="middle" className="tick">
              {l}
            </text>
          ) : null,
        )}
        {xTitle && (
          <text x={(padL + W - padR) / 2} y={H - 6} textAnchor="middle" className="tick">
            {xTitle}
          </text>
        )}
        {shadePath && <path d={shadePath} fill={series[shade[0]].color} opacity="0.12" />}
        {series.map((s) => (
          <path
            key={s.name}
            d={path(s.values)}
            fill="none"
            stroke={s.color}
            strokeWidth={s.dashed ? 2 : 2.5}
            strokeDasharray={s.dashed ? '5 4' : undefined}
            strokeLinejoin="round"
            strokeLinecap="round"
          />
        ))}
        {markers.map((m) => (
          <g key={m.label}>
            <line x1={x(m.i)} x2={x(m.i)} y1={padT} y2={H - padB} stroke={m.color} strokeDasharray="3 3" />
            <circle cx={x(m.i)} cy={y(m.v)} r="5" fill={m.color} stroke="#fff" strokeWidth="2" />
            <text
              x={x(m.i) + (m.i > n * 0.7 ? -8 : 8)}
              y={padT + 12 + (m.row || 0) * 16}
              textAnchor={m.i > n * 0.7 ? 'end' : 'start'}
              className="marker-label"
            >
              {m.label}
            </text>
          </g>
        ))}
        {hover !== null && (
          <g>
            <line x1={x(hover)} x2={x(hover)} y1={padT} y2={H - padB} className="crosshair" />
            {series.map((s) => (
              <circle key={s.name} cx={x(hover)} cy={y(s.values[hover])} r="4.5" fill={s.color} stroke="#fff" strokeWidth="2" />
            ))}
          </g>
        )}
      </svg>
      {hover !== null && (
        <div className="tooltip" style={{ left: `${(x(hover) / W) * 100}%`, top: 8, transform: `translateX(${hover > n / 2 ? '-105%' : '5%'})` }}>
          <div className="tooltip-title">{labels[hover]}</div>
          {series.map((s) => (
            <div key={s.name} className="tooltip-row">
              <span className="swatch" style={{ background: s.color }} />
              <span>{s.name}</span>
              <strong>{yFormat(s.values[hover])}</strong>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// Horizontal bars with direct value labels. Handles negatives with a zero line.
export function BarList({ items, format = (v) => v, labelWidth = 150, valueWidth = 72 }) {
  const [hover, setHover] = useState(null);
  const max = Math.max(...items.map((i) => i.value), 0);
  const min = Math.min(...items.map((i) => i.value), 0);
  const span = max - min || 1;
  const zero = (-min / span) * 100;
  return (
    <div className="barlist" role="list">
      {items.map((it, k) => {
        const w = (Math.abs(it.value) / span) * 100;
        const left = it.value >= 0 ? zero : zero - w;
        return (
          <div
            key={it.label}
            role="listitem"
            className={`barlist-row${hover === k ? ' is-hover' : ''}${it.muted ? ' is-muted' : ''}`}
            style={{ gridTemplateColumns: `${labelWidth}px minmax(0,1fr) ${valueWidth}px` }}
            onMouseEnter={() => setHover(k)}
            onMouseLeave={() => setHover(null)}
            title={it.tip || `${it.label}: ${format(it.value)}`}
          >
            <span className="barlist-label">{it.label}</span>
            <span className="barlist-track">
              {min < 0 && <span className="barlist-zero" style={{ left: `${zero}%` }} />}
              <span
                className="barlist-bar"
                style={{ left: `${left}%`, width: `max(${w}%, ${it.value === 0 ? 0 : 3}px)`, background: it.color }}
              />
            </span>
            <span className="barlist-value">{it.display ?? format(it.value)}</span>
          </div>
        );
      })}
    </div>
  );
}

// Stacked columns (parts of a whole per period) with a per-column tooltip.
export function StackedColumns({ labels, series, height = 240, yFormat = (v) => v, ariaLabel }) {
  const [hover, setHover] = useState(null);
  const W = 640;
  const H = height;
  const padL = 52;
  const padR = 12;
  const padT = 14;
  const padB = 30;
  const totals = labels.map((_, i) => series.reduce((s, se) => s + Math.max(0, se.values[i]), 0));
  const max = niceMax(Math.max(...totals) * 1.05);
  const n = labels.length;
  const slot = (W - padL - padR) / n;
  const bw = Math.min(34, slot * 0.62);
  const y = (v) => padT + (1 - v / max) * (H - padT - padB);
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => f * max);
  return (
    <div className="chart-wrap">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={ariaLabel} className="chart" onMouseLeave={() => setHover(null)}>
        {ticks.map((t, i) => (
          <g key={i}>
            <line x1={padL} x2={W - padR} y1={y(t)} y2={y(t)} className={i === 0 ? 'axis' : 'grid'} />
            <text x={padL - 8} y={y(t) + 4} textAnchor="end" className="tick">
              {yFormat(t)}
            </text>
          </g>
        ))}
        {labels.map((l, i) => {
          let acc = 0;
          const cx = padL + slot * i + slot / 2;
          return (
            <g key={l} onMouseEnter={() => setHover(i)}>
              <rect x={padL + slot * i} y={padT} width={slot} height={H - padT - padB} fill="transparent" />
              {series.map((s, k) => {
                const v = Math.max(0, s.values[i]);
                const y0 = y(acc);
                acc += v;
                const y1 = y(acc);
                const h = Math.max(0, y0 - y1 - (k < series.length - 1 ? 2 : 0));
                const top = k === series.length - 1;
                return (
                  <path
                    key={s.name}
                    d={
                      top && h > 4
                        ? `M${cx - bw / 2} ${y0}V${y1 + 4}Q${cx - bw / 2} ${y1} ${cx - bw / 2 + 4} ${y1}H${cx + bw / 2 - 4}Q${cx + bw / 2} ${y1} ${cx + bw / 2} ${y1 + 4}V${y0}Z`
                        : `M${cx - bw / 2} ${y0}V${y0 - h}H${cx + bw / 2}V${y0}Z`
                    }
                    fill={s.color}
                    opacity={hover === null || hover === i ? 1 : 0.45}
                  />
                );
              })}
              {(i % Math.ceil(n / 7) === 0 || i === n - 1) && (
                <text x={cx} y={H - padB + 18} textAnchor="middle" className="tick">
                  {l}
                </text>
              )}
            </g>
          );
        })}
      </svg>
      {hover !== null && (
        <div
          className="tooltip"
          style={{ left: `${((padL + slot * hover + slot / 2) / W) * 100}%`, top: 8, transform: `translateX(${hover > n / 2 ? '-108%' : '8%'})` }}
        >
          <div className="tooltip-title">{labels[hover]}</div>
          {series.map((s) => (
            <div key={s.name} className="tooltip-row">
              <span className="swatch" style={{ background: s.color }} />
              <span>{s.name}</span>
              <strong>{yFormat(s.values[hover])}</strong>
            </div>
          ))}
          <div className="tooltip-row tooltip-total">
            <span />
            <span>Total</span>
            <strong>{yFormat(totals[hover])}</strong>
          </div>
        </div>
      )}
    </div>
  );
}

export function Legend({ items }) {
  return (
    <div className="legend">
      {items.map((i) => (
        <span key={i.name} className="legend-item">
          <span className={`legend-mark${i.dashed ? ' is-dashed' : ''}`} style={{ '--c': i.color }} />
          {i.name}
        </span>
      ))}
    </div>
  );
}

// Point + whisker per row around a zero line: an estimate and its 95% interval.
export function ForestPlot({ rows, format = (v) => v, labelWidth = 210, ariaLabel, unit = '', width = 640 }) {
  const [hover, setHover] = useState(null);
  const W = width;
  const rowH = 30;
  const padT = 26;
  const padB = 28;
  const H = padT + rows.length * rowH + padB;
  const x0 = labelWidth;
  const x1 = W - 70;
  const lo = Math.min(0, ...rows.map((r) => r.low));
  const hi = Math.max(0, ...rows.map((r) => r.high));
  const span = hi - lo || 1;
  const pad = span * 0.06;
  const X = (v) => x0 + ((v - (lo - pad)) / (span + 2 * pad)) * (x1 - x0);
  const step = niceStep(span);
  const ticks = [];
  for (let t = Math.ceil((lo - pad) / step) * step; t <= hi + pad; t += step) ticks.push(t);
  return (
    <div className="chart-wrap">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={ariaLabel} className="chart" onMouseLeave={() => setHover(null)}>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={X(t)} x2={X(t)} y1={padT - 6} y2={H - padB} className={Math.abs(t) < step / 2 ? 'axis' : 'grid'} />
            <text x={X(t)} y={H - padB + 16} textAnchor="middle" className="tick">
              {format(t)}
            </text>
          </g>
        ))}
        <text x={x1 + 8} y={padT - 10} className="tick">{unit}</text>
        {rows.map((r, i) => {
          const y = padT + i * rowH + rowH / 2;
          return (
            <g key={r.label} onMouseEnter={() => setHover(i)} opacity={hover === null || hover === i ? 1 : 0.5}>
              <rect x="0" y={y - rowH / 2} width={W} height={rowH} fill="transparent" />
              <text x={x0 - 12} y={y + 4} textAnchor="end" className="forest-label">
                {r.label}
              </text>
              <line x1={X(r.low)} x2={X(r.high)} y1={y} y2={y} stroke={r.color} strokeWidth="2" strokeLinecap="round" />
              <line x1={X(r.low)} x2={X(r.low)} y1={y - 5} y2={y + 5} stroke={r.color} strokeWidth="2" />
              <line x1={X(r.high)} x2={X(r.high)} y1={y - 5} y2={y + 5} stroke={r.color} strokeWidth="2" />
              <circle cx={X(r.value)} cy={y} r="5" fill={r.color} stroke="#fff" strokeWidth="2" />
              <text x={x1 + 8} y={y + 4} className="forest-value">
                {format(r.value)}
              </text>
            </g>
          );
        })}
      </svg>
      {hover !== null && (
        <div className="tooltip" style={{ left: `${(X(rows[hover].value) / W) * 100}%`, top: padT + hover * rowH + 26, transform: 'translateX(-50%)' }}>
          <div className="tooltip-title">{rows[hover].label}</div>
          <div className="tooltip-row"><span /><span>Estimate</span><strong>{format(rows[hover].value)}</strong></div>
          <div className="tooltip-row"><span /><span>95% interval</span><strong>{format(rows[hover].low)} to {format(rows[hover].high)}</strong></div>
          {rows[hover].note && <div className="tooltip-row"><span /><span>{rows[hover].note}</span><strong /></div>}
        </div>
      )}
    </div>
  );
}
