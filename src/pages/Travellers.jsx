import React, { useMemo, useState } from 'react';
import { useApp } from '../store.js';
import { ACTIONS, ACTION_BY_ID, PHASES, PHASE_LABEL, REASON_TEXT } from '../engine/actions.js';
import { MARKETS, builderReasons, tripProperties } from '../engine/travellers.js';
import { nbaEvent, nbaProfileProps } from '../engine/policy.js';
import { eur, int } from '../format.js';
import { BarList } from '../components/charts.jsx';
import { Card, COLORS, Icon, Pill, Segmented, Select, TIER_COLOR } from '../components/ui.jsx';

const PAGE = 25;
const CH = { email: 'Email', push: 'Push', in_app: 'In-app', whatsapp: 'WhatsApp', native_flow: 'Native cart flow' };

function decisionLabel(d) {
  if (d.outcome === 'sent') return ACTION_BY_ID[d.action].name;
  return 'No message';
}

function contextLine(c) {
  if (c.phase === 'in_trip') {
    const parts = [`day ${c.daysInTrip}`];
    if (c.dataRemainingPct !== null && c.dataRemainingPct !== undefined) parts.push(`${c.dataRemainingPct}% data`);
    else parts.push('unlimited');
    if (c.planEndHours !== null) parts.push(`plan ends ${c.planEndHours < 48 ? `in ${c.planEndHours} h` : `in ${Math.round(c.planEndHours / 24)} d`}`);
    return parts.join(' · ');
  }
  if (c.phase === 'pre_trip') return `ordered ${c.hoursSinceOrder} h ago · ${c.installCompleted ? 'installed' : 'not installed'}`;
  if (c.phase === 'trip_end_provisional') return `${c.daysSinceLastUsage} d without usage`;
  if (c.phase === 'post_trip') return `end confirmed ${c.daysAfterConfirm} d ago`;
  return `${c.daysSinceTripEnd} d since last trip`;
}

export default function Travellers() {
  const { decisions } = useApp();
  const [q, setQ] = useState('');
  const [phase, setPhase] = useState('all');
  const [outcome, setOutcome] = useState('all');
  const [detail, setDetail] = useState('all');
  const [market, setMarket] = useState('all');
  const [page, setPage] = useState(0);
  const [openId, setOpenId] = useState(null);

  const rows = useMemo(() => {
    const term = q.trim().toLowerCase();
    return decisions.filter(
      (d) =>
        (phase === 'all' || d.c.phase === phase) &&
        (market === 'all' || d.c.market === market) &&
        (outcome === 'all' || d.outcome === outcome) &&
        (detail === 'all' || d.action === detail || d.reason === detail) &&
        (!term || d.c.id.includes(term) || d.c.destination.toLowerCase() === term),
    );
  }, [decisions, q, phase, outcome, detail, market]);

  const reset = (fn) => (v) => {
    fn(v);
    setPage(0);
  };
  const pages = Math.max(1, Math.ceil(rows.length / PAGE));
  const view = rows.slice(page * PAGE, page * PAGE + PAGE);
  const open = openId ? decisions.find((d) => d.c.id === openId) : null;
  const reasonOptions = [...new Set(decisions.filter((d) => d.outcome === 'none').map((d) => d.reason))];

  return (
    <div className="page">
      <header className="page-head">
        <h1>Traveller explorer</h1>
        <p className="muted lead-narrow">
          Every decision point in this run: the Trip State Builder snapshot, each candidate with its eligibility result and reason code, the chosen action or “no message”, and exactly what goes to Klaviyo.
        </p>
      </header>

      <div className="filters">
        <label className="field search">
          <span>Search</span>
          <span className="search-box">
            <Icon name="search" size={16} />
            <input type="search" value={q} onChange={(e) => reset(setQ)(e.target.value)} placeholder="Customer ID or destination (e.g. JP)" />
          </span>
        </label>
        <Select label="Trip phase" value={phase} onChange={reset(setPhase)} options={[{ value: 'all', label: 'All phases' }, ...PHASES.map((p) => ({ value: p.id, label: p.label }))]} />
        <Select label="Market" value={market} onChange={reset(setMarket)} options={[{ value: 'all', label: 'All markets' }, ...MARKETS.map((m) => ({ value: m.code, label: m.code }))]} />
        <Select
          label="Decision"
          value={detail}
          onChange={reset(setDetail)}
          options={[
            { value: 'all', label: 'Any decision' },
            ...ACTIONS.concat([ACTION_BY_ID.F1, ACTION_BY_ID.R1]).map((a) => ({ value: a.id, label: `${a.id} · ${a.name}` })),
            ...reasonOptions.map((r) => ({ value: r, label: `No message · ${r}` })),
          ]}
        />
        <div className="filters-end">
          <Segmented label="Outcome" value={outcome} onChange={reset(setOutcome)} options={[{ value: 'all', label: 'All' }, { value: 'sent', label: 'Message' }, { value: 'none', label: 'No message' }]} />
        </div>
      </div>

      <div className={`explorer${open ? ' has-detail' : ''}`}>
        <Card title={`${int(rows.length)} decision points`} sub="Select a traveller to see the full decision trace.">
          <div className="table-box">
            <table className="table is-clickable">
              <thead>
                <tr>
                  <th scope="col">Traveller</th>
                  <th scope="col">Trip state</th>
                  <th scope="col" className="col-next">Context</th>
                  <th scope="col">Decision</th>
                  <th scope="col">Channel or reason</th>
                </tr>
              </thead>
              <tbody>
                {view.map((d) => (
                  <tr key={d.c.id} className={openId === d.c.id ? 'is-selected' : ''}>
                    <td>
                      <button type="button" className="row-btn" onClick={() => setOpenId(d.c.id)}>
                        {d.c.id}
                      </button>
                      <span className="muted small block nowrap">
                        {d.c.market} → {d.c.destination}
                      </span>
                    </td>
                    <td className="nowrap">
                      {PHASE_LABEL[d.c.phase]}
                      <span className="muted small block">confidence {d.c.tripConfidence}</span>
                    </td>
                    <td className="small col-next">{contextLine(d.c)}</td>
                    <td>
                      {d.outcome === 'sent' ? (
                        <>
                          <span className="code-id" style={{ borderColor: TIER_COLOR[d.tier] }}>{d.action}</span> {decisionLabel(d)}
                        </>
                      ) : (
                        <span className="muted">No message</span>
                      )}
                    </td>
                    <td className="small nowrap">
                      {d.outcome === 'sent' ? (
                        <>
                          {CH[d.channel]}
                          {d.window?.held && <span className="muted block">held to {d.window.opens}</span>}
                        </>
                      ) : (
                        <span className="mono">{d.reason}</span>
                      )}
                    </td>
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
        {open && <Detail d={open} onClose={() => setOpenId(null)} />}
      </div>
    </div>
  );
}

const STATUS_TONE = { chosen: 'good', eligible: 'info', rejected: 'neutral' };

function Detail({ d, onClose }) {
  const { overrides, setOverrides, showToast } = useApp();
  const [tab, setTab] = useState('event');
  const c = d.c;
  const ov = overrides[c.id] || {};
  const setOv = (patch) => {
    const next = { ...ov, ...patch };
    Object.keys(next).forEach((k) => (next[k] === null || next[k] === false || next[k] === '') && delete next[k]);
    const all = { ...overrides };
    if (Object.keys(next).length) all[c.id] = next;
    else delete all[c.id];
    setOverrides(all);
    showToast('Override logged with author, time and reason; the decision was recomputed.');
  };
  const trip = tripProperties(c);
  const event = nbaEvent(d);
  const profile = nbaProfileProps(d);
  const sc = d.score;
  const eligibleIds = d.candidates.filter((r) => r.status !== 'rejected' || r.why === 'OUTRANKED').map((r) => r.id);

  return (
    <Card
      className="detail"
      title={c.id}
      sub={`${c.market} (${c.locale}) → ${c.destination} · ${c.firstChannel} buyer · ${c.appInstalled ? 'app installed' : 'no app'}`}
      actions={
        <button type="button" className="icon-btn" onClick={onClose} aria-label="Close traveller detail">
          <Icon name="close" />
        </button>
      }
    >
      <div className={`decision${d.outcome === 'sent' ? '' : ' is-none'}`}>
        <span className="muted small">{d.outcome === 'sent' ? `Decision · ${d.reason === 'FIXED_RULE' ? 'fixed rule' : `tier ${d.tier}`}` : 'Decision'}</span>
        <strong className="decision-name">
          {d.outcome === 'sent' ? `${d.action} · ${ACTION_BY_ID[d.action].name} by ${CH[d.channel].toLowerCase()}` : `No message · ${d.reason}`}
        </strong>
        <p className="small">{d.outcome === 'none' ? REASON_TEXT[d.reason] : d.reason ? REASON_TEXT[d.reason] : 'Highest-ranked eligible action.'}</p>
        {d.window?.held && <p className="small">Held until {d.window.opens}, then re-validated (the traveller may have topped up meanwhile).</p>}
        {d.also.map((x) => (
          <p key={x.id} className="small">
            Also: <span className="mono">{x.text}</span>
          </p>
        ))}
        <p className="reason-line">
          <span className="muted small block">nba_last_reason</span>“{d.sentence}”
        </p>
      </div>

      <h3 className="h3">Trip State Builder snapshot</h3>
      <dl className="kv">
        <div><dt>trip_state</dt><dd>{c.phase}</dd></div>
        <div><dt>trip_confidence</dt><dd>{c.tripConfidence}</dd></div>
        {c.daysInTrip !== null && <div><dt>days_in_trip</dt><dd>{c.daysInTrip}</dd></div>}
        {c.dataRemainingPct !== null && c.dataRemainingPct !== undefined && <div><dt>data_remaining_pct</dt><dd>{c.dataRemainingPct}%</dd></div>}
        {c.projectedRunoutDays !== null && c.projectedRunoutDays !== undefined && <div><dt>projected_runout</dt><dd>~{c.projectedRunoutDays} d</dd></div>}
        {c.planEndHours !== null && <div><dt>plan_end</dt><dd>in {c.planEndHours} h</dd></div>}
        <div><dt>usage_fresh</dt><dd>{c.usageFresh ? 'yes' : 'no (> 48 h)'}</dd></div>
        <div><dt>sends_30d</dt><dd>{c.sends30d.total} of {d.budget.cap}</dd></div>
      </dl>
      <ul className="reasons">
        {builderReasons(c).map((r) => (
          <li key={r} className="mono small">{r}</li>
        ))}
      </ul>

      <h3 className="h3">Candidates</h3>
      {d.candidates.length === 0 ? (
        <p className="muted small">{d.reason === 'HOLDOUT' || d.reason === 'CARE_OPEN' || d.reason === 'CAP_REACHED' || d.reason === 'REFUNDED' || d.reason === 'OPERATOR_EXCLUDE' ? 'Stopped by a global rule before any action was considered.' : 'No action is at its decision point for this traveller now.'}</p>
      ) : (
        <div className="table-box">
          <table className="table compact">
            <thead>
              <tr>
                <th scope="col">Action</th>
                <th scope="col">Result</th>
                <th scope="col" className="num">S(a)</th>
              </tr>
            </thead>
            <tbody>
              {d.candidates.map((r) => (
                <tr key={r.id}>
                  <td>
                    <span className="code-id" style={{ borderColor: TIER_COLOR[r.a.tier] }}>{r.id}</span> <span className="small">{r.a.name}</span>
                  </td>
                  <td>
                    <Pill tone={r.status === 'chosen' && d.outcome === 'sent' ? 'good' : STATUS_TONE[r.status]}>{r.status === 'chosen' ? (d.outcome === 'sent' ? 'Chosen' : 'Best, not sent') : r.status === 'eligible' ? 'Eligible' : r.why}</Pill>
                    {r.channel && <span className="muted small block">{CH[r.channel]}</span>}
                  </td>
                  <td className="num small">{r.score ? eur(r.score.total, { exact: true, digits: 2 }) : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {sc && (
        <>
          <h3 className="h3">Why {d.action} is worth a send: S(a)</h3>
          <BarList
            items={[
              { label: 'ΔR incremental net revenue', value: sc.dR, color: COLORS.treated },
              { label: 'Cap(a) paid click avoided', value: sc.cap, color: COLORS.owned },
              { label: 'λ shadow price of a send', value: -sc.lambda, color: COLORS.paid },
              { label: 'Unsubscribe risk', value: -sc.unsub, color: COLORS.paid },
              { label: 'Spam risk', value: -sc.spam, color: COLORS.paid },
            ]}
            format={(v) => eur(v, { exact: true, digits: 2 })}
            labelWidth={190}
            valueWidth={70}
          />
          <p className="muted small">S(a) = {eur(sc.total, { exact: true, digits: 2 })}. “No message” always scores €0. w_app and w_util are 0 until measured.</p>
        </>
      )}

      <h3 className="h3">Send budget, last 30 days</h3>
      <BarList
        items={[
          { label: 'All channels', value: c.sends30d.total, color: c.sends30d.total >= d.budget.cap ? COLORS.cta : COLORS.treated, display: `${c.sends30d.total} / ${d.budget.cap}` },
          { label: 'Email', value: c.sends30d.email, color: COLORS.treated, display: `${c.sends30d.email} / 6` },
          { label: 'Push', value: c.sends30d.push, color: COLORS.treated, display: `${c.sends30d.push} / 4` },
          { label: 'WhatsApp', value: c.sends30d.whatsapp, color: COLORS.treated, display: `${c.sends30d.whatsapp} / 2` },
        ]}
        labelWidth={100}
        valueWidth={56}
      />
      <div className="chips">
        <Pill tone={c.consentEmail ? 'good' : 'neutral'}>Email {c.consentEmail ? 'consented' : 'no consent'}</Pill>
        <Pill tone={c.pushToken ? 'good' : 'neutral'}>Push {c.pushToken ? 'token live' : 'no token'}</Pill>
        <Pill tone={c.inAppSession14d ? 'good' : 'neutral'}>In-app {c.inAppSession14d ? 'session ≤ 14 d' : 'no recent session'}</Pill>
        <Pill tone={c.consentWhatsapp ? 'good' : 'neutral'}>WhatsApp {c.consentWhatsapp ? 'consented' : 'no'}</Pill>
        <Pill>Local time {String(c.localHour).padStart(2, '0')}:00 ({c.localTzSource})</Pill>
      </div>

      <h3 className="h3">What Klaviyo receives</h3>
      <div className="seg-row">
        <Segmented
          label="Payload"
          value={tab}
          onChange={setTab}
          options={[
            { value: 'event', label: 'NBA Decision event' },
            { value: 'profile', label: 'nba_* properties' },
            { value: 'trip', label: 'trip_* properties' },
          ]}
        />
      </div>
      <pre className="code-block" tabIndex={0}>
        {tab === 'event'
          ? event
            ? JSON.stringify(event, null, 2)
            : d.outcome === 'sent'
              ? `// ${d.channel === 'in_app' ? 'In-app placement: no event. The service writes nba_inapp_action; a segment reads it and the in-app form targets that segment.' : 'C1 runs natively in the cart and browse flows: the service sets nba_capture_allowed = true and the flow checks it as a profile filter.'}`
              : '// "No message": logged in the warehouse and written to nba_last_reason, but no Klaviyo event.'
          : JSON.stringify(tab === 'profile' ? profile : trip, null, 2)}
      </pre>

      <h3 className="h3">Operator overrides</h3>
      <div className="override">
        <label className="check">
          <input type="checkbox" checked={!!ov.exclude} onChange={(e) => setOv({ exclude: e.target.checked })} />
          <span>
            nba_exclude
            <span className="muted small block">Remove from NBA marketing without touching consent.</span>
          </span>
        </label>
        <label className="field">
          <span>nba_manual_action (expires in 7 days)</span>
          <select value={ov.manual || ''} onChange={(e) => setOv({ manual: e.target.value })}>
            <option value="">None</option>
            {eligibleIds.map((id) => (
              <option key={id} value={id}>
                {id} · {ACTION_BY_ID[id].name}
              </option>
            ))}
          </select>
        </label>
        {d.override && <p className="small"><Pill tone="warn">override=true</Pill> Excluded from uplift training, since the choice was not random.</p>}
      </div>
    </Card>
  );
}
