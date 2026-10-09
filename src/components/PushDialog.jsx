import React, { useMemo, useState } from 'react';
import { useApp } from '../store.js';
import { eur, int } from '../format.js';
import { Modal, Pill } from './ui.jsx';

// Review before send: the marketer sees count, cost and expected profit, then approves the push.
export default function PushDialog({ title, groups, onClose }) {
  const { integrations, setPushLog, showToast } = useApp();
  const destinations = integrations.filter((i) => i.destination && i.connected);
  const ads = integrations.filter((i) => i.ads && i.connected);
  const [dest, setDest] = useState(destinations[0]?.id || '');
  const [exclude, setExclude] = useState(ads.length > 0);
  const [selected, setSelected] = useState(() => groups.map((g) => g.key));

  const chosen = groups.filter((g) => selected.includes(g.key));
  const totals = useMemo(
    () => chosen.reduce((t, g) => ({ count: t.count + g.count, spend: t.spend + g.spend, net: t.net + g.net, excl: t.excl + (g.excluded || 0) }), { count: 0, spend: 0, net: 0, excl: 0 }),
    [chosen],
  );

  const approve = () => {
    const destName = destinations.find((d) => d.id === dest)?.name;
    const at = new Date().toISOString();
    setPushLog((log) => [
      ...chosen.map((g) => ({
        id: `${at}-${g.key}`,
        at,
        key: g.key,
        label: g.label,
        count: g.count,
        spend: g.spend,
        net: g.net,
        destination: destName,
        exclusions: exclude ? ads.map((a) => a.name) : [],
        excluded: exclude ? g.excluded || 0 : 0,
      })),
      ...log,
    ]);
    showToast(`Pushed ${int(totals.count)} customers to ${destName}${exclude && totals.excl ? ` and excluded ${int(totals.excl)} from paid audiences` : ''} (simulated).`);
    onClose();
  };

  const toggle = (key) => setSelected((s) => (s.includes(key) ? s.filter((k) => k !== key) : [...s, key]));

  return (
    <Modal
      title={title}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn ghost" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="btn primary" disabled={!dest || chosen.length === 0} onClick={approve}>
            Approve and push {chosen.length > 0 ? int(totals.count) : ''}
          </button>
        </>
      }
    >
      <div className="table-box">
        <table className="table">
          <thead>
            <tr>
              <th scope="col">
                <span className="sr-only">Include</span>
              </th>
              <th scope="col">Audience</th>
              <th scope="col" className="num">Customers</th>
              <th scope="col" className="num">Cost</th>
              <th scope="col" className="num">Expected profit added</th>
            </tr>
          </thead>
          <tbody>
            {groups.map((g) => (
              <tr key={g.key}>
                <td>
                  <input type="checkbox" checked={selected.includes(g.key)} onChange={() => toggle(g.key)} aria-label={`Include ${g.label}`} />
                </td>
                <td>{g.label}</td>
                <td className="num">{int(g.count)}</td>
                <td className="num">{eur(g.spend)}</td>
                <td className="num strong">{eur(g.net)}</td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr>
              <td />
              <td>Total</td>
              <td className="num">{int(totals.count)}</td>
              <td className="num">{eur(totals.spend)}</td>
              <td className="num strong">{eur(totals.net)}</td>
            </tr>
          </tfoot>
        </table>
      </div>

      <div className="form-grid">
        <label className="field">
          <span>Send through</span>
          <select value={dest} onChange={(e) => setDest(e.target.value)}>
            {destinations.length === 0 && <option value="">No destination connected</option>}
            {destinations.map((d) => (
              <option key={d.id} value={d.id}>
                {d.name}
              </option>
            ))}
          </select>
        </label>
        <label className="check">
          <input type="checkbox" checked={exclude} disabled={ads.length === 0} onChange={(e) => setExclude(e.target.checked)} />
          <span>
            Exclude these customers from retargeting in {ads.length ? ads.map((a) => a.name).join(' and ') : 'connected ad platforms'}
            <span className="muted small block">{int(totals.excl)} customers would leave paid audiences while owned channels reach them.</span>
          </span>
        </label>
      </div>
      <p className="muted small">
        A random holdout keeps getting nothing, so the profit added is measured, not assumed. <Pill tone="info">Simulated push</Pill>
      </p>
    </Modal>
  );
}
