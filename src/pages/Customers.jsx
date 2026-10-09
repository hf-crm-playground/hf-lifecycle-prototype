import React, { useMemo, useState } from 'react';
import { useApp } from '../store.js';
import { STAGES } from '../data.js';
import { upliftByAction, PAID_CPA } from '../model.js';
import { eur, int, pct } from '../format.js';
import { BarList } from '../components/charts.jsx';
import { Card, COLORS, Icon, Pill, Select } from '../components/ui.jsx';

const PAGE = 25;

const SORTS = {
  value: { label: 'Predicted 12-month profit', get: (r) => r.c.value12m },
  risk: { label: 'Churn risk', get: (r) => r.c.churn },
  uplift: { label: 'Profit added by action', get: (r) => r.ev.net + (r.retargetSaving || 0) },
  next: { label: 'Days to next purchase', get: (r) => -r.c.nextPurchaseIn },
};

function riskTone(v) {
  if (v >= 0.6) return 'bad';
  if (v >= 0.35) return 'warn';
  return 'good';
}

function reasons(c, row, actionsById) {
  const out = [];
  if (row.holdout) return ['In the random holdout: gets nothing, so results can be measured.'];
  if (row.capped) return ['Message fatigue is high: paused to avoid unsubscribes.'];
  const a = actionsById[row.action];
  if (row.action === 'none') {
    if (c.churn < 0.2) out.push('Likely to buy anyway: a message would add little.');
    else if (c.churn > 0.85) out.push('Very unlikely to respond to any action right now.');
    else out.push('No action adds more profit than it costs.');
  } else {
    if (c.churn > 0.3 && c.churn < 0.7) out.push(`On the fence: ${pct(c.churn)} churn risk, the range where a nudge moves people most.`);
    if (a.discountPct || a.shipping) out.push(`Price-sensitive (${pct(c.priceSensitivity)}): an offer changes what they do.`);
    else if (c.priceSensitivity < 0.5) out.push('Not price-sensitive: a plain reminder works without giving away margin.');
    if (c.paidShare > 0.35) out.push(`${pct(c.paidShare)} of their returns come through paid ads: reaching them first saves ad spend.`);
    if (c.nextPurchaseIn < 10 && c.nextPurchaseIn > -30) out.push('Close to their usual buying moment.');
  }
  return out.slice(0, 3);
}

export default function Customers() {
  const { planRows, actions } = useApp();
  const [q, setQ] = useState('');
  const [stage, setStage] = useState('all');
  const [action, setAction] = useState('all');
  const [sort, setSort] = useState('value');
  const [page, setPage] = useState(0);
  const [openId, setOpenId] = useState(null);

  const actionsById = useMemo(() => Object.fromEntries([...actions.map((a) => [a.id, a]), ['none', { id: 'none', name: 'Do nothing' }]]), [actions]);

  const rows = useMemo(() => {
    const term = q.trim().toLowerCase();
    const list = planRows.filter(
      (r) =>
        (stage === 'all' || r.c.stage === stage) &&
        (action === 'all' || (action === 'holdout' ? r.holdout : !r.holdout && r.action === action)) &&
        (!term || r.c.id.toLowerCase().includes(term) || r.c.source.toLowerCase().includes(term) || r.c.country.toLowerCase() === term),
    );
    const get = SORTS[sort].get;
    return list.sort((a, b) => get(b) - get(a));
  }, [planRows, q, stage, action, sort]);

  const pages = Math.max(1, Math.ceil(rows.length / PAGE));
  const view = rows.slice(page * PAGE, page * PAGE + PAGE);
  const open = openId ? planRows.find((r) => r.c.id === openId) : null;
  const reset = (fn) => (v) => {
    fn(v);
    setPage(0);
  };

  return (
    <div className="page">
      <header className="page-head">
        <h1>Customer Explorer</h1>
        <p className="muted">Every customer's predicted value, risk and the action the engine picked, with the reasons.</p>
      </header>

      <div className="filters">
        <label className="field search">
          <span>Search</span>
          <span className="search-box">
            <Icon name="search" size={16} />
            <input type="search" value={q} onChange={(e) => reset(setQ)(e.target.value)} placeholder="Customer ID, channel or country" />
          </span>
        </label>
        <Select label="Lifecycle stage" value={stage} onChange={reset(setStage)} options={[{ value: 'all', label: 'All stages' }, ...STAGES.map((s) => ({ value: s, label: s }))]} />
        <Select
          label="Action"
          value={action}
          onChange={reset(setAction)}
          options={[{ value: 'all', label: 'All actions' }, { value: 'none', label: 'Do nothing' }, ...actions.map((a) => ({ value: a.id, label: a.name })), { value: 'holdout', label: 'Holdout' }]}
        />
        <Select label="Sort by" value={sort} onChange={reset(setSort)} options={Object.entries(SORTS).map(([value, s]) => ({ value, label: s.label }))} />
      </div>

      <div className={`explorer${open ? ' has-detail' : ''}`}>
        <Card title={`${int(rows.length)} customers`} sub="Select a customer to see why the engine chose their action.">
          <div className="table-box">
            <table className="table is-clickable">
              <thead>
                <tr>
                  <th scope="col">Customer</th>
                  <th scope="col">Stage</th>
                  <th scope="col" className="num">12-month profit</th>
                  <th scope="col" className="num">Churn risk</th>
                  <th scope="col" className="num col-next">Next purchase</th>
                  <th scope="col">Action</th>
                  <th scope="col" className="num">Profit added</th>
                </tr>
              </thead>
              <tbody>
                {view.map((r) => (
                  <tr key={r.c.id} className={openId === r.c.id ? 'is-selected' : ''}>
                    <td>
                      <button type="button" className="row-btn" onClick={() => setOpenId(r.c.id)}>
                        {r.c.id}
                      </button>
                      <span className="muted small block nowrap">{r.c.source} · {r.c.country}</span>
                    </td>
                    <td className="nowrap">{r.c.stage}</td>
                    <td className="num">{eur(r.c.value12m)}</td>
                    <td className="num">
                      <Pill tone={riskTone(r.c.churn)}>{pct(r.c.churn)}</Pill>
                    </td>
                    <td className="num col-next">{r.c.nextPurchaseIn >= 0 ? `in ${r.c.nextPurchaseIn} d` : `${-r.c.nextPurchaseIn} d overdue`}</td>
                    <td>{r.holdout ? <Pill>Holdout</Pill> : actionsById[r.action]?.name}</td>
                    <td className="num strong">{r.holdout ? '—' : eur(r.ev.net + (r.retargetSaving || 0), { exact: true, digits: 2 })}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="pager">
            <button type="button" className="btn small" disabled={page === 0} onClick={() => setPage(page - 1)}>
              Previous
            </button>
            <span className="muted small">
              Page {page + 1} of {pages}
            </span>
            <button type="button" className="btn small" disabled={page >= pages - 1} onClick={() => setPage(page + 1)}>
              Next
            </button>
          </div>
        </Card>

        {open && <Detail row={open} actions={actions} actionsById={actionsById} onClose={() => setOpenId(null)} />}
      </div>
    </div>
  );
}

function Detail({ row, actions, actionsById, onClose }) {
  const c = row.c;
  const uplift = upliftByAction(c, actions);
  const items = [
    { label: 'Do nothing', value: 0, color: COLORS.holdout, display: '€0.00' },
    ...uplift.map((u) => ({
      label: u.action.name,
      value: u.ok ? u.ev.net : 0,
      color: u.action.id === row.action ? COLORS.treated : '#b9b0f2',
      display: u.ok ? undefined : 'No consent',
      muted: !u.ok,
    })),
  ];
  const chosen = actionsById[row.action];
  return (
    <Card
      className="detail"
      title={c.id}
      sub={`${c.stage} · ${c.source} · ${c.country} · ${c.orders} orders`}
      actions={
        <button type="button" className="icon-btn" onClick={onClose} aria-label="Close customer detail">
          <Icon name="close" />
        </button>
      }
    >
      <div className="mini-stats">
        <div>
          <span className="muted small">12-month profit</span>
          <strong>{eur(c.value12m)}</strong>
        </div>
        <div>
          <span className="muted small">Churn risk</span>
          <strong>{pct(c.churn)}</strong>
        </div>
        <div>
          <span className="muted small">Buys within 30 days</span>
          <strong>{pct(c.p0)}</strong>
        </div>
        <div>
          <span className="muted small">Usual gap</span>
          <strong>{Math.round(c.interPurchase)} days</strong>
        </div>
      </div>

      <div className="decision">
        <span className="muted small">Recommended action</span>
        <strong className="decision-name">{row.holdout ? 'Holdout: no action' : row.capped ? 'Paused: message fatigue' : chosen?.name}</strong>
        <ul className="reasons">
          {reasons(c, row, actionsById).map((t) => (
            <li key={t}>{t}</li>
          ))}
        </ul>
      </div>

      <h3 className="h3">Profit added by each action</h3>
      <p className="muted small">Expected profit this month compared with doing nothing, after send cost, discounts and paid savings.</p>
      <BarList items={items} format={(v) => eur(v, { exact: true, digits: 2 })} labelWidth={180} valueWidth={84} />

      <h3 className="h3">How they come back</h3>
      <div className="split-bar" role="img" aria-label={`${pct(c.paidShare)} of returns through paid ads`}>
        <span style={{ width: `${c.paidShare * 100}%`, background: COLORS.paid }} />
        <span style={{ width: `${(1 - c.paidShare) * 100}%`, background: COLORS.owned }} />
      </div>
      <div className="split-legend small">
        <span>
          <span className="swatch" style={{ background: COLORS.paid }} /> Paid ads {pct(c.paidShare)} · about {eur(PAID_CPA, { exact: true })} each
        </span>
        <span>
          <span className="swatch" style={{ background: COLORS.owned }} /> Owned and direct {pct(1 - c.paidShare)}
        </span>
      </div>

      <h3 className="h3">Consent and fatigue</h3>
      <div className="chips">
        <Pill tone={c.consentEmail ? 'good' : 'neutral'}>Email {c.consentEmail ? 'yes' : 'no'}</Pill>
        <Pill tone={c.consentSms ? 'good' : 'neutral'}>SMS {c.consentSms ? 'yes' : 'no'}</Pill>
        <Pill tone={c.push ? 'good' : 'neutral'}>Push {c.push ? 'yes' : 'no'}</Pill>
        <Pill tone={c.fatigue > 0.7 ? 'bad' : 'neutral'}>{c.sends30d} sends in 30 days</Pill>
        {row.excluded && <Pill tone="info">Excluded from retargeting</Pill>}
      </div>
    </Card>
  );
}
