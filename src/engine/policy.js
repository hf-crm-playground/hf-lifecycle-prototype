// Trip Decision Service, policy v1 — a direct port of the design's pseudocode,
// with the uplift-table ranking (v1 after 60 days) as a switch.
import { ACTIONS, ACTION_BY_ID, FIXED, P_WINS, PHASE_LABEL, confAtLeast } from './actions.js';
import { AS_OF } from './travellers.js';
import { stableHash } from './rng.js';

export const DEFAULT_POLICY = {
  cap30d: 8,
  subcap: { email: 6, push: 4, whatsapp: 2 },
  reserveTier0: 2,
  globalHoldout: 0.1,
  actionControl: 0.1,
  captureBudget: 2,
  ranking: 'v1', // 'v1' = tier then fixed order; 'uplift' = tier then S(a) from the uplift table
  lambda: 0.2, // shadow price of one send from the monthly budget (€)
  cUnsub: 30, // expected 12-month marketing-attributable revenue of a reachable customer (€)
  cPaid: 4, // paid-acquisition cost avoided per order (€2–5 placeholder)
  applyDemotions: true,
  whatsappEnabled: true,
  v2NetworkEvents: false,
  killed: {},
  marketCap: {},
};

export const DEMOTED = new Set(['P3', 'I5']); // actions whose control showed no revenue uplift after 60 days (sample)

const GAP_BY_TIER = { 0: 24, 1: 48, 2: 48, 3: 72 };
const WINDOWS = { email: [9, 20], push: [10, 19], whatsapp: [10, 19] };
const TIER0_WINDOW = [8, 23];

export const EXPERIMENT_FOR = {
  I1: 'E1', P1: 'E2', P2: 'E4', T3: 'E4', B1: 'E6', I7: 'E9', T4: 'E10', C1: 'E11', C2: 'E11',
};

export function canReceive(c, ch, p) {
  switch (ch) {
    case 'email':
      return c.consentEmail && !c.emailComplaint90d;
    case 'push':
      // Push needs the app, a live token and a known timezone (in-trip uses the destination's).
      return c.appInstalled && c.pushToken && (c.phase === 'in_trip' || c.tzKnown) && c.hoursSincePushTxn >= 6;
    case 'in_app':
      return c.appInstalled && c.inAppSession14d;
    case 'whatsapp':
      return p.whatsappEnabled && c.consentWhatsapp && c.market !== 'US';
    case 'native_flow':
      return c.consentEmail || (c.appInstalled && c.pushToken);
    default:
      return false;
  }
}

function pickChannel(c, a, p) {
  for (const ch of a.channels) {
    if (!canReceive(c, ch, p)) continue;
    const sub = p.subcap[ch];
    if (sub !== undefined && (c.sends30d[ch] || 0) >= sub) continue;
    return ch;
  }
  return null;
}

// S(a) = ΔR + Cap(a) + w_app·ΔA + w_util·U − λ·1[a ≠ none] − c_unsub·ΔP_unsub − c_spam·ΔP_spam
// v1 starting values: w_app = w_util = 0; c_spam = 5 × c_unsub.
export function score(c, a, ch, p) {
  if (!a.value) return null;
  const jitter = 0.7 + stableHash(`${c.id}|${a.id}|cell`) * 0.6; // cell-level variation in the uplift table
  const dR = a.value.lift * a.value.perConv * jitter;
  let cap = 0;
  if (a.capture && ch) {
    const p0 = a.captureAction ? c.p0Buy : a.value.base;
    const paid = Math.min(0.95, c.paidShare * (c.couponUser ? 1.2 : 1));
    cap = p0 * paid * (P_WINS[ch] || 0.4) * p.cPaid - (a.perk || 0);
  }
  const counts = ch && ch !== 'in_app';
  const lambda = counts ? p.lambda : 0;
  const dUnsub = ch === 'email' ? 0.0008 : ch === 'push' ? 0.0015 : ch === 'whatsapp' ? 0.002 : 0;
  const dSpam = ch === 'email' ? 0.0001 : 0;
  const unsub = p.cUnsub * dUnsub;
  const spam = 5 * p.cUnsub * dSpam;
  const total = dR + cap - lambda - unsub - spam;
  return { dR, cap, lambda, unsub, spam, total };
}

function nextWindow(c, ch, tier) {
  if (ch === 'in_app' || ch === 'native_flow') return { held: false, at: AS_OF };
  const [lo, hi] = tier === 0 ? TIER0_WINDOW : WINDOWS[ch] || [9, 20];
  const h = c.localHour;
  if (h >= lo && h < hi) return { held: false, at: AS_OF };
  const wait = h < lo ? lo - h : 24 - h + lo;
  return { held: true, at: new Date(Date.parse(AS_OF) + wait * 3600e3).toISOString(), opens: `${String(lo).padStart(2, '0')}:00 local` };
}

const SKIP_LABEL = {
  OUTRANKED: 'outranked',
  RECENCY_GAP: 'recency gap',
  CONTENT_FATIGUE: 'ignored twice in 60 d',
  LOW_TRIP_CONFIDENCE: 'low trip confidence',
  BUDGET_RESERVED_FOR_TIER0: 'budget reserved for Tier 0',
  NO_DELIVERABLE_CHANNEL: 'no channel',
  CAPTURE_BUDGET_SPENT: 'capture budget spent',
  KILL_SWITCH: 'paused',
  DEMOTED: 'demoted',
  USAGE_STALE: 'usage stale',
  DATA_GAP: 'needs v2 data',
  SCORE_NOT_POSITIVE: 'S(a) ≤ 0',
};

function context(c) {
  switch (c.phase) {
    case 'pre_trip':
      return `Pre-trip, ordered ${c.hoursSinceOrder} h ago${c.installCompleted ? ', installed' : ', not installed'}`;
    case 'in_trip': {
      let s = `In-trip day ${c.daysInTrip}`;
      if (c.dataRemainingPct !== null && c.dataRemainingPct !== undefined) s += `, ${c.dataRemainingPct}% data left`;
      if (c.projectedRunoutDays !== null && c.projectedRunoutDays !== undefined && (c.dataRemainingPct <= 30 || c.runoutBeforePlanEnd)) s += `, burn rate says run-out in ~${Math.max(1, Math.round(c.projectedRunoutDays))} d`;
      return s;
    }
    case 'trip_end_provisional':
      return `Trip end provisional, ${c.daysSinceLastUsage} d without usage`;
    case 'post_trip':
      return `Post-trip, end confirmed ${c.daysAfterConfirm} d ago`;
    default:
      return `Between trips, ${c.daysSinceTripEnd} d since last trip`;
  }
}

const CH_LABEL = { email: 'email', push: 'push', in_app: 'in-app', whatsapp: 'WhatsApp', native_flow: 'native cart flow' };

export function decide(c, p = DEFAULT_POLICY, ov = {}) {
  const capEff = p.marketCap[c.market] ?? p.cap30d;
  const used = c.sends30d.total;
  const budget = capEff - used;
  const base = {
    c,
    decisionId: `dec_${Math.floor(stableHash(`${c.id}|${AS_OF}`) * 1e10).toString(36)}`,
    arm: 'treatment',
    budget: { used, cap: capEff, left: Math.max(0, budget) },
    candidates: [],
    also: [],
    override: false,
  };
  const none = (reason, extra = {}) => finalize({ ...base, outcome: 'none', action: null, channel: null, reason, ...extra }, p);

  if (ov.exclude) return none('OPERATOR_EXCLUDE');
  if (stableHash(`${c.id}|holdout`) < p.globalHoldout) return none('HOLDOUT', { arm: 'holdout' });
  if (c.careOpen72h) return none('CARE_OPEN');
  if (c.refunded) return none('REFUNDED');
  if (budget <= 0) return none('CAP_REACHED');
  const reserve = c.phase === 'pre_trip' || c.phase === 'in_trip' ? p.reserveTier0 : 0;

  // R2 costs no send budget: the service only sets the flag at a success moment.
  if (c.phase === 'in_trip' && c.appInstalled && c.successMoment) base.also.push({ id: 'R2', text: 'rating_prompt_allowed = true (app enforces store quotas)' });

  // Fixed-rule sends are scheduled before ranking and consume budget like any other send.
  const fixedDue = [];
  if (c.phase === 'trip_end_provisional' && c.daysSinceLastUsage === 15 && !c.feedbackCollectedThisTrip) fixedDue.push('F1');
  if (c.phase === 'post_trip' && c.daysAfterConfirm === 3 && !c.reviewInvitedThisTrip) fixedDue.push('R1');

  // Evaluate every ranked action, recording why each is rejected.
  const cands = [];
  for (const a of ACTIONS) {
    const res = a.check(c);
    const row = { id: a.id, a, status: 'rejected', why: null, channel: null, score: null };
    if (!res.ok) {
      row.why = res.why;
      if (res.why !== 'NOT_AT_DECISION_POINT') base.candidates.push(row);
      continue;
    }
    base.candidates.push(row);
    if (p.killed[a.id]) { row.why = 'KILL_SWITCH'; continue; }
    if (a.needsData && !p.v2NetworkEvents) { row.why = 'DATA_GAP'; continue; }
    if (p.applyDemotions && DEMOTED.has(a.id)) { row.why = 'DEMOTED'; continue; }
    if (!confAtLeast(c.tripConfidence, a.minConf)) { row.why = 'LOW_TRIP_CONFIDENCE'; continue; }
    if (a.tier > 0 && budget - reserve <= 0) { row.why = 'BUDGET_RESERVED_FOR_TIER0'; continue; }
    if (c.hoursSinceLastSend < GAP_BY_TIER[a.tier]) { row.why = 'RECENCY_GAP'; continue; }
    if (c.fatigueKey === a.key) { row.why = 'CONTENT_FATIGUE'; continue; }
    if (a.captureAction && c.captureSends30d >= p.captureBudget) { row.why = 'CAPTURE_BUDGET_SPENT'; continue; }
    row.channel = pickChannel(c, a, p);
    row.score = score(c, a, row.channel, p);
    row.status = 'eligible';
    cands.push(row);
  }

  // Manual override: force one action at the next eligible point (flagged, excluded from uplift training).
  if (ov.manual) {
    const forced = cands.find((r) => r.id === ov.manual);
    if (forced) {
      forced.status = 'chosen';
      cands.filter((r) => r !== forced).forEach((r) => { r.status = 'rejected'; r.why = 'OUTRANKED'; });
      if (!forced.channel) return none('NO_DELIVERABLE_CHANNEL', { chosenCandidate: forced.id });
      return finalize({ ...base, outcome: 'sent', action: forced.id, channel: forced.channel, reason: 'MANUAL_ACTION', override: true, window: nextWindow(c, forced.channel, forced.a.tier) }, p);
    }
  }

  if (fixedDue.length) {
    const fx = ACTION_BY_ID[fixedDue[0]];
    if (budget - reserve > 0) {
      const ch = fx.channels.find((x) => canReceive(c, x, p)) || null;
      cands.forEach((r) => { r.status = 'rejected'; r.why = 'OUTRANKED'; });
      if (!ch) return none('NO_DELIVERABLE_CHANNEL', { chosenCandidate: fx.id });
      return finalize({ ...base, outcome: 'sent', action: fx.id, channel: ch, reason: 'FIXED_RULE', fixed: true, window: nextWindow(c, ch, 3) }, p);
    }
  }

  if (!cands.length) return none('NO_ELIGIBLE_ACTION');

  let ranked;
  if (p.ranking === 'uplift') {
    ranked = cands.filter((r) => r.score && r.score.total > 0).sort((x, y) => x.a.tier - y.a.tier || y.score.total - x.score.total);
    cands.filter((r) => !ranked.includes(r)).forEach((r) => { r.status = 'rejected'; r.why = 'SCORE_NOT_POSITIVE'; });
    if (!ranked.length) return none('SCORE_NOT_POSITIVE');
  } else {
    ranked = [...cands].sort((x, y) => x.a.tier - y.a.tier || x.a.rank - y.a.rank);
  }
  const best = ranked[0];
  ranked.slice(1).forEach((r) => { r.status = 'rejected'; r.why = 'OUTRANKED'; });
  best.status = 'chosen';

  if (!best.channel) return none('NO_DELIVERABLE_CHANNEL', { chosenCandidate: best.id });

  if (best.a.underTest && stableHash(`${c.id}|${best.id}|${c.tripId || 'none'}`) < p.actionControl) {
    return none('ACTION_CONTROL', { arm: 'action_control', chosenCandidate: best.id, channelWouldBe: best.channel });
  }

  const captureOnly = best.a.captureAction || (best.score && best.score.cap > best.score.dR);
  return finalize({
    ...base,
    outcome: 'sent',
    action: best.id,
    channel: best.channel,
    reason: captureOnly ? 'CAPTURE_ONLY' : null,
    window: nextWindow(c, best.channel, best.a.tier),
  }, p);
}

function finalize(d, p) {
  const c = d.c;
  const a = d.action ? ACTION_BY_ID[d.action] : null;
  const skipped = d.candidates
    .filter((r) => r.status === 'rejected' && SKIP_LABEL[r.why])
    .slice(0, 2)
    .map((r) => `${shortName(r.a)} (${SKIP_LABEL[r.why]})`);
  const usedAfter = d.budget.used + (d.outcome === 'sent' && d.channel !== 'in_app' ? 1 : 0);
  let sentence;
  if (d.outcome === 'sent') {
    sentence = `${context(c)} → ${shortName(a)} by ${CH_LABEL[d.channel]}.`;
  } else {
    sentence = `${context(c)} → no message: ${shortReason(d)}.`;
  }
  if (skipped.length) sentence += ` Skipped: ${skipped.join(', ')}.`;
  sentence += ` Budget ${usedAfter} of ${d.budget.cap} used.`;
  d.sentence = sentence.length > 200 ? `${sentence.slice(0, 197)}…` : sentence;
  d.tier = a && a.tier !== undefined ? a.tier : a ? 'fixed' : null;
  d.score = d.action ? d.candidates.find((r) => r.id === d.action)?.score || null : null;
  d.experiment = d.action ? EXPERIMENT_FOR[d.action] || `AC-${d.action}` : d.chosenCandidate ? EXPERIMENT_FOR[d.chosenCandidate] || `AC-${d.chosenCandidate}` : null;
  return d;
}

function shortName(a) {
  const map = {
    I1: 'top-up prompt', I2: 'plan extension', I3: 'Always On ending', P1: 'activation help', I6: 'add destination',
    B1: 'Always On claim', I4: 'app balance', I5: 'feature intro', C1: 'intent capture', C2: 'purchase-window capture',
    T1: 'Always On enrol', T2: 'next destination', T3: 'app install', P2: 'app install', P3: 'feature primer',
    I7: 'referral', T4: 'referral', B2: 'seasonal prompt', F1: 'trip feedback', R1: 'review invitation', R2: 'rating prompt',
  };
  return a ? map[a.id] || a.name : '';
}

function shortReason(d) {
  const m = {
    HOLDOUT: 'measurement holdout', CARE_OPEN: 'open care conversation', REFUNDED: 'refunded order', CAP_REACHED: `${d.budget.cap} sends already this month`,
    NO_ELIGIBLE_ACTION: 'nothing useful to say now', NO_DELIVERABLE_CHANNEL: 'no deliverable channel', ACTION_CONTROL: 'action-level control',
    OPERATOR_EXCLUDE: 'excluded by operator', SCORE_NOT_POSITIVE: 'no action scores above zero',
  };
  return m[d.reason] || d.reason;
}

// One hourly run over everyone with a decision point.
export function runDecisions(travellers, p = DEFAULT_POLICY, overrides = {}) {
  return travellers.map((c) => decide(c, p, overrides[c.id] || {}));
}

export function summarize(decisions) {
  const s = {
    total: decisions.length,
    sent: 0,
    none: 0,
    byReason: {},
    byAction: {},
    byTier: { 0: 0, 1: 0, 2: 0, 3: 0, fixed: 0 },
    byChannel: {},
    byPhase: {},
    held: 0,
    expectedValue: 0,
    expectedDR: 0,
    expectedCap: 0,
    capture: 0,
    holdout: 0,
    control: 0,
    flags: 0,
  };
  for (const d of decisions) {
    const ph = (s.byPhase[d.c.phase] ||= { total: 0, sent: 0 });
    ph.total++;
    if (d.also.length) s.flags++;
    if (d.outcome === 'sent') {
      s.sent++;
      ph.sent++;
      s.byAction[d.action] = (s.byAction[d.action] || 0) + 1;
      s.byTier[d.tier] = (s.byTier[d.tier] || 0) + 1;
      s.byChannel[d.channel] = (s.byChannel[d.channel] || 0) + 1;
      if (d.window?.held) s.held++;
      if (d.score) {
        s.expectedValue += d.score.total;
        s.expectedDR += d.score.dR;
        s.expectedCap += d.score.cap;
      }
      if (d.reason === 'CAPTURE_ONLY') s.capture++;
    } else {
      s.none++;
      s.byReason[d.reason] = (s.byReason[d.reason] || 0) + 1;
      if (d.reason === 'HOLDOUT') s.holdout++;
      if (d.reason === 'ACTION_CONTROL') s.control++;
    }
  }
  return s;
}

// Klaviyo Create Event body for the NBA Decision metric (the activation contract).
export function nbaEvent(d) {
  if (d.outcome !== 'sent' || d.channel === 'in_app' || d.channel === 'native_flow') return null;
  const a = ACTION_BY_ID[d.action];
  const reasonCodes = [d.reason || 'RANKED', ...d.candidates.filter((r) => r.status === 'rejected' && r.why !== 'NOT_AT_DECISION_POINT').map((r) => `${r.id}:${r.why}`)].slice(0, 6);
  return {
    data: {
      type: 'event',
      attributes: {
        properties: {
          decision_id: d.decisionId,
          action: d.action,
          content_key: a.key,
          channel: d.channel,
          trip_phase: d.c.phase,
          destination: d.c.destination,
          locale: d.c.locale,
          reason_codes: reasonCodes,
          experiment_id: d.experiment,
          arm: d.arm,
          expires_at: new Date(Date.parse(d.window?.at || AS_OF) + 24 * 3600e3).toISOString(),
        },
        time: d.window?.at || AS_OF,
        metric: { data: { type: 'metric', attributes: { name: 'NBA Decision' } } },
        profile: { data: { type: 'profile', attributes: { external_id: d.c.id } } },
      },
    },
  };
}

export function nbaProfileProps(d) {
  const props = {
    nba_holdout_group: d.arm === 'holdout' ? 'holdout' : 'treatment',
    nba_last_action: d.action ? ACTION_BY_ID[d.action].key : 'none',
    nba_last_decision_at: AS_OF,
    nba_last_reason: d.sentence,
    nba_sends_30d_total: d.c.sends30d.total,
    nba_sends_30d_email: d.c.sends30d.email,
    nba_sends_30d_push: d.c.sends30d.push,
    nba_sends_30d_whatsapp: d.c.sends30d.whatsapp,
  };
  if (d.channel === 'in_app') {
    props.nba_inapp_action = ACTION_BY_ID[d.action].key;
    props.nba_inapp_expires_at = new Date(Date.parse(AS_OF) + 7 * 864e5).toISOString();
  }
  if (d.action === 'C1') props.nba_capture_allowed = true;
  if (d.also.some((x) => x.id === 'R2')) props.rating_prompt_allowed = true;
  return props;
}

export { PHASE_LABEL, FIXED };
