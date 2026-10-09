import React, { useState } from 'react';
import { useApp } from '../store.js';
import { BUILDER_CONFIG, EVENTS, KLAVIYO_EXTERNAL, KLAVIYO_NATIVE } from '../engine/content.js';
import { nbaEvent } from '../engine/policy.js';
import { Card, Pill, Segmented } from '../components/ui.jsx';

const EV_TONE = { verify: 'info', required: 'warn', optional: 'neutral' };
const EV_LABEL = { verify: 'Available to verify', required: 'Required', optional: 'Optional' };

export default function DataKlaviyo() {
  const { decisions } = useApp();
  const [evFilter, setEvFilter] = useState('all');
  const sample = decisions.find((d) => d.action === 'I1' && d.channel === 'push') || decisions.find((d) => nbaEvent(d));
  const events = EVENTS.filter((e) => evFilter === 'all' || e.status === evFilter);

  return (
    <div className="page">
      <header className="page-head">
        <h1>Data and Klaviyo</h1>
        <p className="muted lead-narrow">
          Two Holafly-owned components sit in front of Klaviyo: the Trip State Builder turns order, activation and usage data into one trip state per customer, and the Trip Decision Service picks one action (or none) per decision point and hands it to Klaviyo as a single NBA Decision event. Klaviyo does what it is good at: consent, delivery, creative A/B tests and message analytics.
        </p>
      </header>

      <Card title="Architecture" sub="Six sources feed the builder hourly; message outcomes flow back so the next decision sees what was sent">
        <Architecture />
      </Card>

      <Card title="Trip state machine" sub="The single definition used by the builder, the decision table and the measurement plan">
          <StateMachine />
          <ul className="reasons small">
            <li>Pre-trip stays pre-trip on activation at home with no usage (test install).</li>
            <li>A new order mid-trip does not start a new trip: Japan → Korea with no 14-day gap is one trip, with a pending-activation flag.</li>
            <li>Post-trip sends go only on a confirmed end; if usage reappears the trip reopens and earlier post-trip sends are logged as mistimed.</li>
          </ul>
        </Card>
      <Card title="Builder thresholds" sub="trip_state_builder/config.py · every number is a policy choice, pinned by tests">
          <div className="table-box">
            <table className="table compact">
              <thead>
                <tr>
                  <th scope="col">Setting</th>
                  <th scope="col" className="num">Value</th>
                  <th scope="col">Meaning</th>
                </tr>
              </thead>
              <tbody>
                {BUILDER_CONFIG.map((r) => (
                  <tr key={r.key}>
                    <td className="mono small">{r.key}</td>
                    <td className="num strong">{r.value}</td>
                    <td className="small">{r.meaning}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="muted small">Runs hourly over customers with any event in the last 30 days plus everyone with a scheduled decision point; changed snapshots go to Klaviyo through Create or Update Profile as trip_* properties. Time-critical in-trip triggers (activation failed, data below 20%, Always On ending) are pushed as they happen.</p>
        </Card>

      <Card
        title="Minimum event schema"
        sub="23 event types. “Available to verify” means a metric with that name exists in Holafly’s Klaviyo account (observed 23 Sep 2026) but its payload, coverage and latency are unconfirmed."
        actions={
          <Segmented
            label="Filter events"
            value={evFilter}
            onChange={setEvFilter}
            options={[
              { value: 'all', label: 'All' },
              { value: 'verify', label: 'To verify' },
              { value: 'required', label: 'Required' },
            ]}
          />
        }
      >
        <div className="table-box tall">
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Event</th>
                <th scope="col">Source</th>
                <th scope="col">Klaviyo metric observed</th>
                <th scope="col">Status</th>
              </tr>
            </thead>
            <tbody>
              {events.map((e) => (
                <tr key={e.event}>
                  <td className="strong small">{e.event}</td>
                  <td className="small">{e.source}</td>
                  <td className="mono small">{e.metric}</td>
                  <td><Pill tone={EV_TONE[e.status]}>{EV_LABEL[e.status]}</Pill></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <div className="grid-2">
        <Card title="The activation contract" sub="One Create Event call per non-holdout decision that is not “no message”">
          <p className="small">
            Metric <span className="mono">NBA Decision</span> with properties <span className="mono">decision_id, action, content_key, channel, trip_phase, destination, locale, reason_codes, experiment_id, arm, expires_at</span>. One metric-triggered flow, the NBA Dispatcher, splits on action and channel; each message carries a channel-consent filter. In-app placements are a profile property read by a segment. “No message” is logged and written to nba_last_reason but creates no event.
          </p>
          <pre className="code-block" tabIndex={0}>{sample ? JSON.stringify(nbaEvent(sample), null, 2) : ''}</pre>
        </Card>
        <Card title="What Klaviyo does natively" sub="Checked against Klaviyo’s documentation on 23 Sep 2026">
          <ul className="layer-list">
            {KLAVIYO_NATIVE.map((k) => (
              <li key={k.cap}>
                <strong className="small">{k.cap}</strong>
                <span className="small block muted">{k.use}</span>
              </li>
            ))}
          </ul>
        </Card>
      </div>

      <Card title="What has to live outside Klaviyo" sub="Trip state, the cross-channel cap, incrementality and cross-flow arbitration">
        <div className="table-box">
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Need</th>
                <th scope="col">Why Klaviyo cannot do it natively</th>
                <th scope="col">Where it goes</th>
              </tr>
            </thead>
            <tbody>
              {KLAVIYO_EXTERNAL.map((k) => (
                <tr key={k.need}>
                  <td className="strong small">{k.need}</td>
                  <td className="small">{k.why}</td>
                  <td className="small">{k.where}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}

const ink = '#292b2e';
const quiet = '#5c6066';
const edge = '#9a9aa8';
const accent = '#2a78d6';

function Box({ x, y, w, h, title, sub, strong }) {
  return (
    <g>
      <rect x={x} y={y} width={w} height={h} rx="10" fill={strong ? '#eaf2fc' : '#fff'} stroke={strong ? accent : edge} strokeWidth={strong ? 2 : 1.25} />
      <text x={x + w / 2} y={sub ? y + h / 2 - 3 : y + h / 2 + 4} textAnchor="middle" fontSize="12.5" fontWeight="600" fill={ink}>
        {title}
      </text>
      {sub && (
        <text x={x + w / 2} y={y + h / 2 + 13} textAnchor="middle" fontSize="11" fill={quiet}>
          {sub}
        </text>
      )}
    </g>
  );
}

function Architecture() {
  const sources = [
    ['Orders and top-ups', 'Shopify / checkout'],
    ['Connectivity backend', 'activation, daily usage'],
    ['App SDK events', 'first_open, features'],
    ['Always On service', 'eligibility, claims'],
    ['Intercom', 'open conversations'],
    ['Klaviyo', 'consent, sends, engagement'],
  ];
  const sx = 16;
  const sw = 168;
  const sh = 38;
  const sy = (i) => 28 + i * 48;
  return (
    <svg viewBox="0 0 760 360" role="img" aria-label="Six sources feed the Trip State Builder, which feeds the Trip Decision Service, which sends one NBA Decision event to Klaviyo" className="chart">
      <defs>
        <marker id="arch-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
          <path d="M0 0L10 5L0 10z" fill={edge} />
        </marker>
      </defs>
      <g fill="none" stroke={edge} strokeWidth="1.25">
        {sources.map((_, i) => (
          <path key={i} d={`M${sx + sw} ${sy(i) + sh / 2}C${sx + sw + 24} ${sy(i) + sh / 2} ${214 - 24} 160 214 160`} markerEnd="url(#arch-arrow)" />
        ))}
        <path d="M354 160H398" markerEnd="url(#arch-arrow)" />
        <path d="M538 140C556 140 556 66 572 66" markerEnd="url(#arch-arrow)" />
        <path d="M658 90V128" markerEnd="url(#arch-arrow)" />
        <path d="M538 176C556 176 556 220 572 220" markerEnd="url(#arch-arrow)" />
        <path d="M468 190V250" markerEnd="url(#arch-arrow)" />
        <path d="M744 150H752V338H100V302" strokeDasharray="4 4" markerEnd="url(#arch-arrow)" />
      </g>
      {sources.map(([t, s], i) => (
        <Box key={t} x={sx} y={sy(i)} w={sw} h={sh} title={t} sub={s} />
      ))}
      <Box x={214} y={130} w={140} h={60} title="Trip State Builder" sub="warehouse, hourly" strong />
      <Box x={398} y={130} w={140} h={60} title="Trip Decision Service" sub="eligibility, scoring, cap" strong />
      <Box x={572} y={42} w={172} h={48} title="Klaviyo NBA Dispatcher" sub="metric-triggered flow" />
      <Box x={572} y={128} w={172} h={44} title="Email · push · WhatsApp" />
      <Box x={572} y={198} w={172} h={44} title="In-app form audience" sub="segment on nba_inapp_action" />
      <Box x={398} y={250} w={140} h={48} title="Decision log" sub="and holdouts" />
      <text x="556" y="34" fontSize="11" fill={quiet}>Create Event: NBA Decision</text>
      <text x="566" y="190" fontSize="11" fill={quiet} textAnchor="end">profile property</text>
      <text x="420" y="352" fontSize="11" fill={quiet} textAnchor="middle">message events return through Klaviyo’s own metrics (nightly Events API pull)</text>
    </svg>
  );
}

function StateMachine() {
  const y = 112;
  const w = 128;
  const h = 46;
  const xs = [12, 202, 392, 582];
  const names = [
    ['Pre-trip', 'order confirmed'],
    ['In-trip', 'confidence high/medium'],
    ['Trip end', 'provisional, day 14'],
    ['Post-trip', 'confirmed, day 16'],
  ];
  return (
    <svg viewBox="0 0 724 300" role="img" aria-label="Trip states: pre-trip, in-trip, provisional end, post-trip and between trips, with their transitions" className="chart">
      <defs>
        <marker id="sm-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
          <path d="M0 0L10 5L0 10z" fill={edge} />
        </marker>
      </defs>
      <g fill="none" stroke={edge} strokeWidth="1.25">
        <path d={`M${xs[0] + w} ${y + h / 2}H${xs[1]}`} markerEnd="url(#sm-arrow)" />
        <path d={`M${xs[1] + w} ${y + h / 2}H${xs[2]}`} markerEnd="url(#sm-arrow)" />
        <path d={`M${xs[2] + w} ${y + h / 2}H${xs[3]}`} markerEnd="url(#sm-arrow)" />
        <path d={`M${xs[2] + w / 2} ${y}C${xs[2] + w / 2} ${y - 50} ${xs[1] + w / 2} ${y - 50} ${xs[1] + w / 2} ${y}`} markerEnd="url(#sm-arrow)" />
        <path d={`M${xs[3] + w / 2} ${y + h}V${214}`} markerEnd="url(#sm-arrow)" />
        <path d={`M${xs[3]} ${238}H${xs[0] + w / 2}V${y + h}`} markerEnd="url(#sm-arrow)" />
        <path d={`M${xs[3] + w / 2} ${y}C${xs[3] + w / 2} ${y - 92} ${xs[0] + w / 2} ${y - 92} ${xs[0] + w / 2} ${y}`} strokeDasharray="4 4" markerEnd="url(#sm-arrow)" />
      </g>
      {names.map(([t, s], i) => (
        <Box key={t} x={xs[i]} y={y} w={w} h={h} title={t} sub={s} strong={i === 1} />
      ))}
      <Box x={xs[3]} y={214} w={w} h={h} title="Between trips" sub="30 d after end" />
      <text x={(xs[0] + w + xs[1]) / 2} y={y + h + 18} textAnchor="middle" fontSize="11" fill={quiet}>activation + usage</text>
      <text x={(xs[0] + w + xs[1]) / 2} y={y + h + 32} textAnchor="middle" fontSize="11" fill={quiet}>outside home</text>
      <text x={(xs[1] + w + xs[2]) / 2} y={y + h + 18} textAnchor="middle" fontSize="11" fill={quiet}>14 d no usage</text>
      <text x={(xs[2] + w + xs[3]) / 2} y={y + h + 18} textAnchor="middle" fontSize="11" fill={quiet}>16 d no usage</text>
      <text x={(xs[1] + xs[2] + w) / 2} y={y - 44} textAnchor="middle" fontSize="11" fill={quiet}>usage arrives late: trip reopens</text>
      <text x={(xs[0] + xs[3] + w) / 2} y={y - 76} textAnchor="middle" fontSize="11" fill={quiet}>new order (post-trip)</text>
      <text x={(xs[0] + w / 2 + xs[3]) / 2} y={254} textAnchor="middle" fontSize="11" fill={quiet}>new order or travel-date signal</text>
    </svg>
  );
}
