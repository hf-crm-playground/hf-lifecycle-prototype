// Sample travellers, each with the snapshot the Trip State Builder would emit
// (field names follow trip_state_builder/models.py and klaviyo.py) plus the
// consent, cap and engagement fields the Decision Service reads.
import { clamp, mulberry32, weighted } from './rng.js';

export const AS_OF = '2026-10-09T10:00:00Z';
export const RUN_DAY_OF_MONTH = 9;

export const MARKETS = [
  { code: 'ES', locale: 'es-ES', w: 0.22, whatsapp: true, leadP60h: 96, tzOffset: 2 },
  { code: 'MX', locale: 'es-MX', w: 0.12, whatsapp: true, leadP60h: 120, tzOffset: -6 },
  { code: 'US', locale: 'en-US', w: 0.18, whatsapp: false, leadP60h: 144, tzOffset: -4 },
  { code: 'GB', locale: 'en-GB', w: 0.1, whatsapp: true, leadP60h: 120, tzOffset: 1 },
  { code: 'DE', locale: 'de-DE', w: 0.1, whatsapp: true, leadP60h: 168, tzOffset: 2 },
  { code: 'FR', locale: 'fr-FR', w: 0.1, whatsapp: true, leadP60h: 120, tzOffset: 2 },
  { code: 'IT', locale: 'it-IT', w: 0.08, whatsapp: true, leadP60h: 96, tzOffset: 2 },
  { code: 'BR', locale: 'pt-BR', w: 0.1, whatsapp: true, leadP60h: 144, tzOffset: -3 },
];

const DESTINATIONS = [
  { code: 'JP', w: 0.12, tz: 9 }, { code: 'US', w: 0.12, tz: -5 }, { code: 'EUROPE', w: 0.14, tz: 1 },
  { code: 'TH', w: 0.07, tz: 7 }, { code: 'MX', w: 0.06, tz: -6 }, { code: 'TR', w: 0.06, tz: 3 },
  { code: 'MA', w: 0.04, tz: 1 }, { code: 'CO', w: 0.05, tz: -5 }, { code: 'KR', w: 0.05, tz: 9 },
  { code: 'ID', w: 0.04, tz: 8 }, { code: 'AE', w: 0.04, tz: 4 }, { code: 'ASIA', w: 0.05, tz: 8 },
  { code: 'PE', w: 0.03, tz: -5 }, { code: 'GB', w: 0.05, tz: 1 }, { code: 'CN', w: 0.04, tz: 8 },
  { code: 'AU', w: 0.04, tz: 10 },
];

const PHASE_W = [
  { id: 'pre_trip', w: 0.22 },
  { id: 'in_trip', w: 0.3 },
  { id: 'trip_end_provisional', w: 0.05 },
  { id: 'post_trip', w: 0.17 },
  { id: 'between_trips', w: 0.26 },
];

export function generateTravellers(n = 6000, seed = 23092026) {
  const r = mulberry32(seed);
  const pick = (p) => r() < p;
  const int = (lo, hi) => lo + Math.floor(r() * (hi - lo + 1));
  const expo = (mean) => -Math.log(1 - r()) * mean;
  const out = [];

  for (let i = 0; i < n; i++) {
    const m = weighted(r, MARKETS);
    let d = weighted(r, DESTINATIONS);
    if (d.code === m.code) d = DESTINATIONS[(DESTINATIONS.indexOf(d) + 3) % DESTINATIONS.length];
    const phase = weighted(r, PHASE_W).id;
    const firstChannel = pick(0.3) ? 'app' : 'web';
    const appInstalled = firstChannel === 'app' || pick(0.33);
    const appIdentity = !appInstalled && firstChannel === 'web' && pick(0.06) ? 'unknown' : appInstalled ? 'linked' : 'none';
    const pushToken = appInstalled && pick(0.72);
    const total = Math.min(9, Math.round(expo(3.2)));
    let email = 0;
    let push = 0;
    let wa = 0;
    const consentWhatsapp = m.whatsapp && pick(0.16);
    for (let k = 0; k < total; k++) {
      const x = r();
      if (pushToken && x < 0.35 && push < 4) push++;
      else if (consentWhatsapp && x < 0.45 && wa < 2) wa++;
      else email++;
    }
    const sends = email + push + wa;
    const orders = 1 + Math.floor(expo(1.2));

    const c = {
      id: `hf_${String(204801 + i)}`,
      market: m.code,
      locale: m.locale,
      home: m.code,
      destination: d.code,
      destTz: d.tz,
      homeTz: m.tzOffset,
      phase,
      firstChannel,
      appInstalled,
      appIdentity,
      pushToken,
      inAppSession14d: appInstalled && pick(0.6),
      tzKnown: pick(0.72),
      consentEmail: pick(0.9),
      consentWhatsapp,
      emailComplaint90d: pick(0.005),
      plansSubscriber: pick(0.05),
      refunded: pick(0.012),
      careOpen72h: pick(0.03),
      hoursSincePushTxn: pick(0.08) ? int(1, 5) : 99,
      sends30d: { email, push, whatsapp: wa, total: sends },
      hoursSinceLastSend: sends ? Math.round(clamp(expo(70), 2, 700)) : 9999,
      captureSends30d: pick(0.1) ? int(1, 2) : 0,
      fatigueKey: null,
      utcHourOffset: 0,
      orders,
      couponUser: pick(0.2),
      paidShare: clamp(0.15 + r() * 0.5 + (firstChannel === 'web' ? 0.08 : -0.05), 0.05, 0.85),
      p0Buy: 0,
      npsScore: null,
      daysSinceNps: 99,
      referralPrompts12m: pick(0.2) ? int(1, 3) : 0,
      referralLastPromptDays: pick(0.15) ? int(5, 300) : 999,
      wasReferred: pick(0.08),
      aoStatus: pick(0.14) ? 'active' : 'none',
      aoEligible: pick(0.65),
      aoClaimedThisMonth: false,
      aoClaimReminders: 0,
      aoPrompt30d: pick(0.1),
      loyaltyTier: pick(0.25) ? 'Globetrotter' : pick(0.4) ? 'Explorer' : 'Traveller',
      tripsCompleted: Math.max(0, orders - 1 - (phase === 'pre_trip' ? 0 : 0)),
      // builder snapshot fields
      tripConfidence: 'none',
      tripId: null,
      tripStartedAt: null,
      daysInTrip: null,
      daysSinceLastUsage: null,
      usageFresh: true,
      dataRemainingPct: null,
      projectedRunoutDays: null,
      planType: pick(0.72) ? 'finite' : 'unlimited',
      planEndHours: null,
      pendingActivation: false,
      testInstallDetected: false,
      departureLikely: false,
      longTrip: false,
    };

    if (phase === 'pre_trip') {
      c.hoursSinceOrder = Math.round(clamp(expo(110), 1, 900));
      c.testInstallDetected = pick(0.12);
      c.tripConfidence = c.testInstallDetected ? 'medium' : 'low';
      c.pendingActivation = true;
      c.installStarted = c.testInstallDetected || pick(0.3);
      c.hoursSinceInstallStart = c.installStarted ? int(1, 96) : null;
      c.installCompleted = c.testInstallDetected || (c.installStarted && pick(0.45));
      c.pastP60 = c.hoursSinceOrder > m.leadP60h;
      c.declaredDepartureDays = pick(0.3) ? int(-2, 30) : null;
      c.departureLikely = c.declaredDepartureDays !== null && c.declaredDepartureDays <= 0 && !c.installCompleted;
      c.justInstalled = appInstalled && pick(0.06);
      c.featureUsed = appInstalled && pick(0.2);
      c.intent24h = pick(0.03);
      c.orderLast24h = c.hoursSinceOrder < 24;
      c.p0Buy = 0.2;
    } else if (phase === 'in_trip') {
      c.daysInTrip = clamp(Math.round(expo(7)) + 1, 1, 60);
      c.longTrip = c.daysInTrip > 30;
      c.tripConfidence = pick(0.86) ? 'high' : 'medium';
      c.usageFresh = pick(0.9);
      c.daysSinceLastUsage = pick(0.85) ? 0 : int(1, 4);
      c.usageLast24h = c.daysSinceLastUsage === 0;
      c.planEndHours = Math.round(clamp(expo(140), 2, 900));
      if (c.planType === 'finite') {
        c.dataRemainingPct = Math.round(clamp(100 - r() * 100 * (0.4 + c.daysInTrip / 20), 0, 100));
        const burnPctPerDay = clamp(6 + r() * 14, 3, 30);
        c.projectedRunoutDays = +(c.dataRemainingPct / burnPctPerDay).toFixed(1);
        c.runoutBeforePlanEnd = c.projectedRunoutDays * 24 < c.planEndHours;
      }
      c.toppedUp24h = pick(0.04);
      c.lowDataTxnAlert12h = c.dataRemainingPct !== null && c.dataRemainingPct <= 20 && pick(0.15);
      c.newerPlan = pick(0.05);
      c.orderLast24h = pick(0.04);
      c.aoThresholdEvent = c.aoStatus === 'active' && pick(0.3);
      c.paidPlanActive = c.aoStatus === 'active' ? pick(0.3) : true;
      c.inTripAppSession = appInstalled && pick(0.55);
      c.featureUsed = appInstalled && pick(0.3);
      c.careThisTrip = pick(0.06);
      c.successMoment = c.daysInTrip >= 5 && pick(0.4);
      c.networkOutsidePlan = pick(0.03);
      c.p0Buy = 0.45;
    } else if (phase === 'trip_end_provisional') {
      c.tripConfidence = 'high';
      c.daysSinceLastUsage = int(14, 16);
      c.feedbackCollectedThisTrip = false;
      c.p0Buy = 0.1;
    } else if (phase === 'post_trip') {
      c.tripConfidence = 'high';
      c.daysAfterConfirm = int(0, 28);
      c.daysSinceLastUsage = 16 + c.daysAfterConfirm;
      c.orderLast30d = pick(0.08);
      c.t1Last3d = pick(0.05);
      c.p2IgnoredThisTrip = c.firstChannel === 'web' && pick(0.3);
      c.npsScore = pick(0.35) ? weighted(r, [{ v: 10, w: 3 }, { v: 9, w: 3 }, { v: 8, w: 2 }, { v: 7, w: 1 }, { v: 5, w: 1 }, { v: 2, w: 0.6 }]).v : null;
      c.daysSinceNps = c.npsScore !== null ? int(0, 10) : 99;
      c.i7ThisTrip = pick(0.08);
      c.careThisTrip = pick(0.05);
      c.reviewInvitedThisTrip = c.daysAfterConfirm > 3 || pick(0.1);
      c.declaredWithin7d = pick(0.02);
      c.repeatPredicted = c.orders >= 3 && pick(0.15);
      c.p0Buy = 0.15;
    } else {
      c.tripConfidence = 'high';
      c.daysSinceTripEnd = int(31, 420);
      c.daysSinceLastUsage = c.daysSinceTripEnd + 14;
      c.aoClaimedThisMonth = c.aoStatus === 'active' && pick(0.55);
      c.aoClaimReminders = c.aoStatus === 'active' && !c.aoClaimedThisMonth ? int(0, 2) : 0;
      c.orderLast60d = pick(0.06);
      c.orderLast30d = c.orderLast60d && pick(0.5);
      c.anniversaryWindow = pick(0.04);
      c.intent24h = pick(0.06);
      c.declaredWithin7d = pick(0.04);
      c.repeatPredicted = c.orders >= 3 && pick(0.12);
      c.orderLast24h = false;
      c.p0Buy = c.intent24h ? 0.8 : c.declaredWithin7d ? 0.6 : c.repeatPredicted ? 0.5 : 0.08;
    }

    if (phase !== 'pre_trip') {
      const firstUsageDaysAgo =
        phase === 'in_trip' ? c.daysInTrip - 1 : (c.daysSinceLastUsage || 0) + int(3, 20);
      const start = new Date(Date.parse(AS_OF) - firstUsageDaysAgo * 864e5).toISOString().slice(0, 10);
      c.tripId = `${c.id}:${start}`;
      c.tripStartedAt = start;
    }
    if (pick(0.04)) c.fatigueKey = ['topup_runout', 'always_on_claim', 'next_destination', 'app_install_posttrip', 'activate_help'][int(0, 4)];
    // Local hour at decision time (09:00 UTC run + offset), used for send windows.
    const tz = phase === 'in_trip' ? c.destTz : c.homeTz;
    c.localHour = (10 + tz + 24) % 24;
    c.localTzSource = phase === 'in_trip' ? 'destination' : c.tzKnown ? 'profile' : 'unknown';
    out.push(c);
  }
  return out;
}

export const TRAVELLERS = generateTravellers();

// The Trip State Builder snapshot as Klaviyo profile properties (trip_state_builder/klaviyo.py).
export function tripProperties(c) {
  const props = {
    trip_state: c.phase,
    trip_confidence: c.tripConfidence,
    trip_id: c.tripId,
    trip_destination: c.destination,
    trip_days_in_trip: c.daysInTrip,
    trip_days_since_last_usage: c.daysSinceLastUsage,
    trip_usage_fresh: c.usageFresh,
    trip_data_remaining_pct: c.dataRemainingPct,
    trip_projected_runout_at: c.projectedRunoutDays !== null && c.projectedRunoutDays !== undefined ? `+${c.projectedRunoutDays} d` : null,
    trip_plan_ends_within_48h: c.planEndHours !== null ? c.planEndHours <= 48 : false,
    trip_pending_activation: c.pendingActivation,
    trip_test_install_detected: c.testInstallDetected,
    trip_departure_likely: c.departureLikely,
    trip_long_trip: c.longTrip,
    trips_completed: c.tripsCompleted,
    trip_state_as_of: AS_OF,
  };
  return Object.fromEntries(Object.entries(props).filter(([, v]) => v !== null && v !== undefined));
}

export function builderReasons(c) {
  const r = [];
  if (c.phase === 'pre_trip') {
    r.push(`pre_trip: eSIM ordered ${c.hoursSinceOrder} h ago, not yet used`);
    if (c.testInstallDetected) r.push('activated at home, under 50 MB: test install ignored');
    if (c.departureLikely) r.push('departure likely: declared date reached without activation');
  } else if (c.phase === 'in_trip') {
    r.push(`in_trip: last usage ${c.daysSinceLastUsage} d ago, within the 14-day rule`);
    r.push(c.tripConfidence === 'high' ? `confidence high: usage outside home ${c.home}` : 'confidence medium: usage seen only at home so far');
    if (!c.usageFresh) r.push('usage feed older than 48 h: run-out fields withheld');
    if (c.longTrip) r.push('long trip: more than 30 days in trip');
  } else if (c.phase === 'trip_end_provisional') {
    r.push(`trip_end_provisional: ${c.daysSinceLastUsage} d without usage; confirmed after 16 d`);
  } else if (c.phase === 'post_trip') {
    r.push(`post_trip: end confirmed ${c.daysAfterConfirm} d ago (16 d without usage)`);
  } else {
    r.push(`between_trips: last trip ended ${c.daysSinceTripEnd} d ago`);
  }
  return r;
}
