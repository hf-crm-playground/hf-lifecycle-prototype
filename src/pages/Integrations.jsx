import React from 'react';
import { useApp } from '../store.js';
import { eur, int } from '../format.js';
import { Card, Pill } from '../components/ui.jsx';

export default function Integrations() {
  const { integrations, setIntegrations, pushLog, setPushLog, showToast } = useApp();

  const toggle = (id) => {
    setIntegrations(
      integrations.map((i) =>
        i.id === id ? { ...i, connected: !i.connected, lastSync: !i.connected ? 'just now' : null } : i,
      ),
    );
    const it = integrations.find((i) => i.id === id);
    showToast(`${it.name} ${it.connected ? 'disconnected' : 'connected'} (simulated).`);
  };

  return (
    <div className="page">
      <header className="page-head">
        <h1>Integrations</h1>
        <p className="muted">Where the data comes from and where decisions go. The engine decides; your existing tools still send.</p>
      </header>

      <div className="int-grid">
        {integrations.map((i) => (
          <div key={i.id} className="int-card">
            <div className="int-top">
              <span className="int-logo" aria-hidden="true">
                {i.name[0]}
              </span>
              <div>
                <strong>{i.name}</strong>
                <span className="muted small block">{i.kind}</span>
              </div>
              {i.connected ? <Pill tone="good">Connected</Pill> : <Pill>Not connected</Pill>}
            </div>
            <p className="small">{i.role}</p>
            <div className="int-foot">
              <span className="muted small">{i.connected ? `Last sync ${i.lastSync}` : 'No data yet'}</span>
              <button type="button" className={`btn small${i.connected ? ' ghost' : ''}`} onClick={() => toggle(i.id)}>
                {i.connected ? 'Disconnect' : 'Connect'}
              </button>
            </div>
          </div>
        ))}
      </div>

      <Card
        title="Audit log"
        sub="Every push, who approved it and where it went"
        actions={
          pushLog.length > 0 && (
            <button type="button" className="btn small ghost" onClick={() => setPushLog([])}>
              Clear log
            </button>
          )
        }
      >
        {pushLog.length === 0 ? (
          <p className="muted">No pushes yet. Approve a plan in the Lifecycle Optimizer or from the Executive Summary.</p>
        ) : (
          <div className="table-box">
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">When</th>
                  <th scope="col">Audience</th>
                  <th scope="col">Destination</th>
                  <th scope="col" className="num">Customers</th>
                  <th scope="col" className="num">Expected profit</th>
                  <th scope="col">Ad exclusions</th>
                  <th scope="col">Approved by</th>
                </tr>
              </thead>
              <tbody>
                {pushLog.map((p) => (
                  <tr key={p.id}>
                    <td>{new Date(p.at).toLocaleString('en-GB', { dateStyle: 'medium', timeStyle: 'short' })}</td>
                    <td>{p.label}</td>
                    <td>{p.destination}</td>
                    <td className="num">{int(p.count)}</td>
                    <td className="num">{eur(p.net)}</td>
                    <td>{p.exclusions.length ? `${int(p.excluded)} in ${p.exclusions.join(', ')}` : 'None'}</td>
                    <td>You (demo)</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
