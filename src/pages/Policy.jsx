import React, { useMemo, useState } from 'react';
import { useApp } from '../store.js';
import { ACTIONS, FIXED, PHASE_LABEL, TIERS } from '../engine/actions.js';
import { DEFAULT_POLICY, DEMOTED, EXPERIMENT_FOR, runDecisions, summarize } from '../engine/policy.js';
import { MARKETS } from '../engine/travellers.js';
import { eur, int, pct } from '../format.js';
import { Card, Modal, Pill, Segmented, Stat, TIER_COLOR, Toggle } from '../components/ui.jsx';

const CH = { email: 'Email', push: 'Push', in_app: 'In-app', whatsapp: 'WhatsApp', native_flow: 'Native flow' };

export default function Policy() {
  const { policy, setPolicy, summary: s, travellers, overrides } = useApp();
  const [spec, setSpec] = useState(null);

  // The same run under the design defaults, to show what the current settings change.
  const baseline = useMemo(() => summarize(runDecisions(travellers, DEFAULT_POLICY, overrides)), [travellers, overrides]);
  const changed = JSON.stringify(policy) !== JSON.stringify(DEFAULT_POLICY);
  const toggleKill = (id) => setPolicy({ killed: { ...policy.killed, [id]: !policy.killed[id] } });
  const setMarketCap = (code, v) => {
    const mc = { ...policy.marketCap };
    if (v === 'default') delete mc[code];
    else mc[code] = Number(v);
    setPolicy({ marketCap: mc });
  };

  return (
    <div className="page">
      <header className="page-head">
        <h1>Decision policy</h1>
        <p className="muted lead-narrow">
          Policy v1 is deterministic and readable in full: four priority tiers, a hard cap across email, push and WhatsApp, and “no message” whenever confidence, budget, fatigue or consent say so. Every setting here is a versioned override; change one and the whole run recomputes.
        </p>
      </header>

      <div className="stats">
        <Stat label="Messages this run" value={int(s.sent)} note={changed ? `${s.sent - baseline.sent >= 0 ? '+' : ''}${int(s.sent - baseline.sent)} vs design defaults` : 'Design defaults'} />
        <Stat label="Expected S(a), all sends" value={eur(s.expectedValue)} note={changed ? `${s.expectedValue - baseline.expectedValue >= 0 ? '+' : '−'}${eur(Math.abs(s.expectedValue - baseline.expectedValue))} vs defaults` : `ΔR ${eur(s.expectedDR)} + capture ${eur(s.expectedCap)}`} tone="good" />
        <Stat label="“No message” share" value={pct(s.none / s.total)} note={`${int(s.holdout)} holdout · ${int(s.control)} action control`} />
        <Stat label="Capture sends" value={int(s.capture)} note={`Sub-budget ${policy.captureBudget} of ${policy.cap30d} sends`} />
      </div>

      <div className="grid-opt">
        <Card title="Budget and channel rules">
          <div className="setting">
            <span className="setting-label">Cap per trailing 30 days (Phase 2 tests 6 / 8 / 10)</span>
            <Segmented label="Cap" value={String(policy.cap30d)} onChange={(v) => setPolicy({ cap30d: Number(v) })} options={['6', '8', '10'].map((v) => ({ value: v, label: v }))} />
          </div>
          <div className="setting">
            <span className="setting-label">Sends held back for Tier 0 while pre-trip or in-trip</span>
            <Segmented label="Reserve" value={String(policy.reserveTier0)} onChange={(v) => setPolicy({ reserveTier0: Number(v) })} options={['0', '1', '2', '3'].map((v) => ({ value: v, label: v }))} />
          </div>
          <div className="setting">
            <span className="setting-label">Capture sub-budget (C1, C2)</span>
            <Segmented label="Capture budget" value={String(policy.captureBudget)} onChange={(v) => setPolicy({ captureBudget: Number(v) })} options={['0', '1', '2', '3'].map((v) => ({ value: v, label: v }))} />
          </div>
          <div className="setting">
            <span className="setting-label">Global holdout (20% if the eligible base is under 100,000)</span>
            <Segmented label="Holdout" value={String(policy.globalHoldout)} onChange={(v) => setPolicy({ globalHoldout: Number(v) })} options={[{ value: '0.1', label: '10%' }, { value: '0.2', label: '20%' }]} />
          </div>
          <div className="setting">
            <span className="setting-label">Ranking within a tier</span>
            <Segmented
              label="Ranking"
              value={policy.ranking}
              onChange={(v) => setPolicy({ ranking: v })}
              options={[
                { value: 'v1', label: 'Fixed order (v1)' },
                { value: 'uplift', label: 'Uplift table (E8)' },
              ]}
            />
            <span className="muted small">Uplift table replaces the tie-break with S(a) after 60 days; tiers stay as a safety order and only S(a) &gt; 0 is sent.</span>
          </div>
          <label className="range">
            <span className="range-head">
              <span>λ, shadow price of one send</span>
              <strong>{eur(policy.lambda, { exact: true, digits: 2 })}</strong>
            </span>
            <input type="range" min="0" max="1" step="0.05" value={policy.lambda} onChange={(e) => setPolicy({ lambda: Number(e.target.value) })} />
          </label>
          <Toggle checked={policy.applyDemotions} onChange={(v) => setPolicy({ applyDemotions: v })} label="Demote actions with no uplift after 60 days" hint={`Currently demoted: ${[...DEMOTED].join(', ')}`} />
          <Toggle checked={policy.whatsappEnabled} onChange={(v) => setPolicy({ whatsappEnabled: v })} label="WhatsApp channel" hint="Only where a sender, consent and a marketing-allowed market exist; never US numbers." />
          <Toggle checked={policy.v2NetworkEvents} onChange={(v) => setPolicy({ v2NetworkEvents: v })} label="Network-country events available (gap 16)" hint="Unlocks I6 add destination." />
        </Card>

        <div className="stack">
          <Card title="Tiers" sub="Actions sit in four priority tiers; a run-out top-up always outranks a referral">
            <div className="table-box">
              <table className="table compact">
                <thead>
                  <tr>
                    <th scope="col">Tier</th>
                    <th scope="col">Purpose</th>
                    <th scope="col">Minimum trip confidence</th>
                    <th scope="col" className="num">Gap since last send</th>
                    <th scope="col" className="num">Sends this run</th>
                  </tr>
                </thead>
                <tbody>
                  {TIERS.map((t) => (
                    <tr key={t.tier}>
                      <td><span className="tier-dot" style={{ background: TIER_COLOR[t.tier] }} /> {t.tier}</td>
                      <td>{t.purpose}</td>
                      <td className="small">{t.minConfidence}</td>
                      <td className="num">{t.minGapH} h</td>
                      <td className="num strong">{int(s.byTier[t.tier] || 0)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="muted small">
              Sub-caps: email 6, push 4, WhatsApp 2. In-app placements do not count, but only one is active at a time and it expires after 7 days. A content_key delivered twice in 60 days without a click is suppressed for 60 days. Send windows: email 09:00–20:00, push and WhatsApp 10:00–19:00 local; Tier 0 08:00–23:00.
            </p>
          </Card>
          <Card title="Market rules" sub="Cap overrides for markets with stricter norms">
            <div className="market-grid">
              {MARKETS.map((m) => (
                <label key={m.code} className="field">
                  <span>
                    {m.code} {m.whatsapp ? '' : '(no WhatsApp marketing)'}
                  </span>
                  <select value={policy.marketCap[m.code] ?? 'default'} onChange={(e) => setMarketCap(m.code, e.target.value)}>
                    <option value="default">Default ({policy.cap30d})</option>
                    <option value="6">6 per 30 days</option>
                    <option value="8">8 per 30 days</option>
                  </select>
                </label>
              ))}
            </div>
          </Card>
        </div>
      </div>

      <Card title="Decision table" sub="Eighteen ranked actions plus three fixed-rule sends. Select an action for its full specification; switch it off with its kill switch.">
        <div className="table-box">
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Action</th>
                <th scope="col">Phase</th>
                <th scope="col">Decision point</th>
                <th scope="col">Channels</th>
                <th scope="col" className="num">Sends</th>
                <th scope="col">Status</th>
                <th scope="col">On</th>
              </tr>
            </thead>
            <tbody>
              {TIERS.map((t) => (
                <React.Fragment key={t.tier}>
                  <tr className="group-row">
                    <td colSpan={7}>
                      <span className="tier-dot" style={{ background: TIER_COLOR[t.tier] }} /> Tier {t.tier} · {t.purpose}
                    </td>
                  </tr>
                  {ACTIONS.filter((a) => a.tier === t.tier).map((a) => {
                    const off = policy.killed[a.id];
                    const status = off ? <Pill tone="bad">Paused</Pill> : a.needsData && !policy.v2NetworkEvents ? <Pill>Needs data</Pill> : policy.applyDemotions && DEMOTED.has(a.id) ? <Pill tone="warn">Demoted</Pill> : <Pill tone="good">Live</Pill>;
                    return (
                      <tr key={a.id} className={off ? 'is-off' : ''}>
                        <td>
                          <button type="button" className="row-btn" onClick={() => setSpec(a)}>
                            {a.id} · {a.name}
                          </button>
                          <span className="muted small block mono">{a.key}</span>
                        </td>
                        <td className="nowrap">{PHASE_LABEL[a.phase]}</td>
                        <td className="small">{a.trigger}</td>
                        <td className="small nowrap">{a.channels.map((c) => CH[c]).join(' → ')}</td>
                        <td className="num">{int(s.byAction[a.id] || 0)}</td>
                        <td>{status}</td>
                        <td>
                          <input type="checkbox" checked={!off} onChange={() => toggleKill(a.id)} aria-label={`Kill switch for ${a.id}`} />
                        </td>
                      </tr>
                    );
                  })}
                </React.Fragment>
              ))}
              <tr className="group-row">
                <td colSpan={7}>
                  <span className="tier-dot" style={{ background: TIER_COLOR.fixed }} /> Fixed rules · scheduled before ranking, never optimised
                </td>
              </tr>
              {FIXED.map((f) => (
                <tr key={f.id}>
                  <td>
                    <button type="button" className="row-btn" onClick={() => setSpec(f)}>
                      {f.id} · {f.name}
                    </button>
                    <span className="muted small block mono">{f.key}</span>
                  </td>
                  <td className="nowrap">{f.id === 'F1' ? 'Trip end' : f.id === 'R1' ? 'Post-trip' : 'In-trip'}</td>
                  <td className="small">{f.moment}</td>
                  <td className="small nowrap">{f.channels.map((c) => CH[c]).join(' → ')}</td>
                  <td className="num">{f.id === 'R2' ? `${int(s.flags)} flags` : int(s.byAction[f.id] || 0)}</td>
                  <td><Pill tone="info">Policy send</Pill></td>
                  <td />
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      {spec && (
        <Modal title={`${spec.id} · ${spec.name}`} onClose={() => setSpec(null)}>
          <dl className="spec">
            <div><dt>content_key</dt><dd className="mono">{spec.key}</dd></div>
            {spec.tier !== undefined && <div><dt>Tier and phase</dt><dd>Tier {spec.tier} · {PHASE_LABEL[spec.phase]}</dd></div>}
            <div><dt>Decision point</dt><dd>{spec.trigger || spec.moment}</dd></div>
            <div><dt>Eligibility</dt><dd>{spec.eligibility || spec.who}</dd></div>
            {spec.suppression && <div><dt>Action-specific suppression</dt><dd>{spec.suppression}</dd></div>}
            {spec.rules && <div><dt>Rules that bind it</dt><dd>{spec.rules}</dd></div>}
            <div><dt>Channels</dt><dd>{spec.channels.map((c) => CH[c]).join(', then ')}</dd></div>
            {spec.metric && <div><dt>Success metric</dt><dd>{spec.metric}</dd></div>}
            {spec.minConf && <div><dt>Minimum trip confidence</dt><dd>{spec.minConf}</dd></div>}
            {spec.value && (
              <div>
                <dt>Sample value model</dt>
                <dd>
                  Outcome without a message {pct(spec.value.base)}; uplift {pct(spec.value.lift, 1)}; {eur(spec.value.perConv, { exact: true })} net revenue per extra outcome{spec.capture ? '; capture value added' : ''}.
                </dd>
              </div>
            )}
            {spec.id && EXPERIMENT_FOR[spec.id] && <div><dt>Experiment</dt><dd>{EXPERIMENT_FOR[spec.id]}</dd></div>}
            {spec.replaces && <div><dt>Replaces</dt><dd className="mono">{spec.replaces}</dd></div>}
          </dl>
        </Modal>
      )}
    </div>
  );
}
