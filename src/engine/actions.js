// The NBA decision table from "Holafly NBA System Design" (Sep 2026), as data.
// Each action: when its decision point is reached (trigger), who is eligible, its own
// suppressions, channels in preference order, tier, and a sample value model for S(a).
// Value numbers are illustrative placeholders until the action-level controls report.

export const PHASES = [
  { id: 'pre_trip', label: 'Pre-trip', enters: 'eSIM purchased, not yet activated', optimises: 'Activation before departure; app install where it will be used' },
  { id: 'in_trip', label: 'In-trip', enters: 'First activation', optimises: 'Staying connected: top-up before data runs out, companion features, in-app habits' },
  { id: 'trip_end_provisional', label: 'Trip end (provisional)', enters: '14 days without usage', optimises: 'Nothing new until the end is confirmed at day 16; feedback at day 15' },
  { id: 'post_trip', label: 'Post-trip', enters: 'Trip end confirmed', optimises: 'Always On enrolment, referral, capturing the next destination' },
  { id: 'between_trips', label: 'Between trips', enters: '30 days after trip end, no new purchase', optimises: 'Monthly Always On claim as a habit; next-trip intent with low contact pressure' },
];

export const PHASE_LABEL = Object.fromEntries(PHASES.map((p) => [p.id, p.label]));

export const TIERS = [
  { tier: 0, purpose: 'Keep the customer connected', minConfidence: 'medium', minGapH: 24 },
  { tier: 1, purpose: 'Useful app habit in the moment', minConfidence: 'high (in-trip); none for B1', minGapH: 48 },
  { tier: 2, purpose: 'Growth: next trip, Always On, app', minConfidence: 'confirmed trip end for T-actions', minGapH: 48 },
  { tier: 3, purpose: 'Advocacy', minConfidence: 'high', minGapH: 72 },
];

const CONF_RANK = { none: 0, low: 1, medium: 2, high: 3 };
export const confAtLeast = (have, need) => CONF_RANK[have] >= CONF_RANK[need];

// Placeholder probabilities that a message wins the last click, by channel (design: in-app 0.8, push deep link 0.7, email 0.4).
export const P_WINS = { in_app: 0.8, push: 0.7, whatsapp: 0.7, email: 0.4, native_flow: 0.7 };

const ok = { ok: true };
const no = (why) => ({ ok: false, why });

export const ACTIONS = [
  // ---------------- Tier 0: keep the customer connected
  {
    id: 'I1', tier: 0, rank: 1, phase: 'in_trip', key: 'topup_runout', name: 'Top up before data runs out',
    channels: ['push', 'email', 'whatsapp'], minConf: 'medium', underTest: true,
    trigger: 'data_remaining_pct ≤ 20% or projected run-out before plan end',
    eligibility: 'Finite plan; usage no older than 48 h; not a Plans subscriber; plan end more than 24 h away',
    suppression: 'Top-up in last 24 h; backend low-data alert in last 12 h',
    metric: 'Top-up within 48 h vs action-level control; guardrail: unsubscribes',
    value: { base: 0.45, lift: 0.09, perConv: 9 }, capture: true,
    check(c) {
      if (c.phase !== 'in_trip') return no('NOT_AT_DECISION_POINT');
      if (c.planType !== 'finite') return no('NOT_AT_DECISION_POINT');
      if (!(c.dataRemainingPct <= 20 || c.runoutBeforePlanEnd)) return no('NOT_AT_DECISION_POINT');
      if (!c.usageFresh) return no('USAGE_STALE');
      if (c.plansSubscriber) return no('PLANS_SUBSCRIBER');
      if (c.planEndHours <= 24) return no('PLAN_ENDING_I2_INSTEAD');
      if (c.toppedUp24h) return no('ACTION_SUPPRESSED');
      if (c.lowDataTxnAlert12h) return no('TRANSACTIONAL_ALERT_SENT');
      return ok;
    },
  },
  {
    id: 'I2', tier: 0, rank: 2, phase: 'in_trip', key: 'plan_extend', name: 'Extend or buy a plan for the same destination',
    channels: ['push', 'email'], minConf: 'medium', underTest: true,
    trigger: 'plan_end_at within 48 h and usage in the last 24 h',
    eligibility: 'Trip still active; no newer plan for the destination',
    suppression: 'Order in last 24 h',
    metric: 'Repeat purchase within 72 h; connectivity-gap days after plan end',
    value: { base: 0.35, lift: 0.08, perConv: 14 }, capture: true,
    check(c) {
      if (c.phase !== 'in_trip') return no('NOT_AT_DECISION_POINT');
      if (!(c.planEndHours <= 48 && c.usageLast24h)) return no('NOT_AT_DECISION_POINT');
      if (c.newerPlan) return no('ACTION_SUPPRESSED');
      if (c.orderLast24h) return no('ACTION_SUPPRESSED');
      return ok;
    },
  },
  {
    id: 'I3', tier: 0, rank: 3, phase: 'in_trip', key: 'always_on_ending', name: 'Always On data ending: buy a plan',
    channels: ['push', 'email'], minConf: 'medium', underTest: true,
    trigger: 'hly_always_on_consumption_threshold',
    eligibility: 'Always On user in-trip; no paid plan active',
    suppression: 'Order in last 24 h',
    metric: 'Purchase within 48 h vs control',
    value: { base: 0.4, lift: 0.07, perConv: 8 },
    replaces: 'CRM_TRIPS_ALWAYS_ON_ENDING_TOP_UP_PUSH',
    check(c) {
      if (c.phase !== 'in_trip' || !c.aoThresholdEvent) return no('NOT_AT_DECISION_POINT');
      if (c.paidPlanActive) return no('ACTION_SUPPRESSED');
      if (c.orderLast24h) return no('ACTION_SUPPRESSED');
      return ok;
    },
  },
  {
    id: 'P1', tier: 0, rank: 4, phase: 'pre_trip', key: 'activate_help', name: 'Activation help, device-specific',
    channels: ['email', 'push'], minConf: 'low', underTest: true,
    trigger: "Not installed by the market's 60th-percentile purchase-to-activation lead time, or install started and not completed within 24 h",
    eligibility: 'eSIM not installed; order not refunded',
    suppression: 'Transactional QR email in last 24 h; already activated',
    metric: 'Install completed within 72 h vs holdout; guardrail: installation care contacts',
    value: { base: 0.55, lift: 0.08, perConv: 12 },
    check(c) {
      if (c.phase !== 'pre_trip') return no('NOT_AT_DECISION_POINT');
      if (c.installCompleted) return no('ALREADY_INSTALLED');
      const due = c.pastP60 || (c.installStarted && c.hoursSinceInstallStart >= 24);
      if (!due) return no('NOT_AT_DECISION_POINT');
      if (c.hoursSinceOrder < 24) return no('QR_EMAIL_LAST_24H');
      return ok;
    },
  },
  {
    id: 'I6', tier: 0, rank: 5, phase: 'in_trip', key: 'add_destination', name: 'Add the new destination',
    channels: ['push'], minConf: 'medium', underTest: true, needsData: 16,
    trigger: "Network country changes to one outside the plan's coverage (needs a network-country event, v2)",
    eligibility: 'Regional or single-country plan; usage stopped in the last 24 h',
    suppression: 'Order in last 24 h',
    metric: 'Purchase within 24 h; usage resumes',
    value: { base: 0.2, lift: 0.1, perConv: 12 },
    check(c) {
      if (c.phase !== 'in_trip' || !c.networkOutsidePlan) return no('NOT_AT_DECISION_POINT');
      if (c.orderLast24h) return no('ACTION_SUPPRESSED');
      return ok;
    },
  },

  // ---------------- Tier 1: useful app habit in the moment
  {
    id: 'B1', tier: 1, rank: 1, phase: 'between_trips', key: 'always_on_claim', name: "Claim this month's 1 GB",
    channels: ['push', 'in_app', 'email'], minConf: 'none', underTest: true,
    trigger: 'Claim window open (1st of month) and not claimed by day 3',
    eligibility: 'Always On active and eligible this month',
    suppression: 'Claimed already; two claim reminders sent this month',
    metric: 'Claim rate within 7 days vs control; guardrail: unsubscribes among Always On users',
    value: { base: 0.38, lift: 0.15, perConv: 3 },
    check(c) {
      if (!(c.phase === 'between_trips' || c.phase === 'post_trip')) return no('NOT_AT_DECISION_POINT');
      if (c.aoStatus !== 'active') return no('NOT_AT_DECISION_POINT');
      if (c.aoClaimedThisMonth) return no('ALREADY_CLAIMED');
      if (c.aoClaimReminders >= 2) return no('ACTION_SUPPRESSED');
      return ok;
    },
  },
  {
    id: 'I4', tier: 1, rank: 2, phase: 'in_trip', key: 'app_balance_intrip', name: 'Check your balance in the app',
    channels: ['push', 'email'], minConf: 'high', underTest: true,
    trigger: 'Day 2 of trip, app installed, no in-trip session',
    eligibility: 'App installed; high-confidence trip',
    suppression: 'Any send in last 48 h; I1 or I2 eligible (they outrank)',
    metric: 'In-trip app session within 3 days vs control',
    value: { base: 0.3, lift: 0.12, perConv: 1.4 },
    check(c) {
      if (c.phase !== 'in_trip' || c.daysInTrip !== 2) return no('NOT_AT_DECISION_POINT');
      if (!c.appInstalled || c.inTripAppSession) return no('NOT_AT_DECISION_POINT');
      return ok;
    },
  },
  {
    id: 'I5', tier: 1, rank: 3, phase: 'in_trip', key: 'feature_intro_intrip', name: 'Introduce VPN, virtual location or safe browsing',
    channels: ['in_app', 'push'], minConf: 'high', underTest: true,
    trigger: 'Days 3–7 of trip, app user, feature never used',
    eligibility: 'App installed; no care contact this trip',
    suppression: 'I1, I2, I4 eligible; feature used already',
    metric: 'Feature toggle within 7 days; secondary: sessions during rest of trip',
    value: { base: 0.08, lift: 0.006, perConv: 6 },
    check(c) {
      if (c.phase !== 'in_trip' || c.daysInTrip < 3 || c.daysInTrip > 7) return no('NOT_AT_DECISION_POINT');
      if (!c.appInstalled) return no('NOT_AT_DECISION_POINT');
      if (c.featureUsed) return no('FEATURE_USED');
      if (c.careThisTrip) return no('CARE_THIS_TRIP');
      return ok;
    },
  },
  {
    id: 'C1', tier: 1, rank: 4, phase: 'between_trips', key: 'capture_intent', name: 'Finish the purchase in the app (intent capture)',
    channels: ['native_flow'], minConf: 'none', underTest: true, captureAction: true,
    trigger: 'Browse or cart event in the last 24 h without an order',
    eligibility: 'Not in holdout; capture sub-budget left; segment paid-channel share ≥ 30%; no Tier 0 action pending',
    suppression: 'Order in last 24 h; C1 in last 72 h; one owner per moment (flow or dispatcher, never both)',
    metric: 'Paid last-click share and paid cost per order vs holdout; purchase rate and revenue unchanged; perk cost',
    value: { base: 0.8, lift: 0.01, perConv: 20 }, capture: true, perk: 0,
    check(c) {
      if (!(c.phase === 'between_trips' || c.phase === 'pre_trip')) return no('NOT_AT_DECISION_POINT');
      if (!c.intent24h) return no('NOT_AT_DECISION_POINT');
      if (c.paidShare < 0.3) return no('PAID_SHARE_BELOW_30');
      if (c.orderLast24h) return no('ACTION_SUPPRESSED');
      return ok;
    },
  },
  {
    id: 'C2', tier: 1, rank: 5, phase: 'between_trips', key: 'capture_window', name: 'Your eSIM for the destination, ready in the app',
    channels: ['push', 'email'], minConf: 'none', underTest: true, captureAction: true,
    trigger: 'Declared travel date within 7 days with no order, or a repeat traveller predicted to buy this month',
    eligibility: 'P0(buy) proxy medium-high or high; segment paid share ≥ 30%; capture sub-budget left',
    suppression: 'T2 or B2 sent in last 14 days; order in last 30 days',
    metric: 'Same as C1',
    value: { base: 0.5, lift: 0.02, perConv: 20 }, capture: true, perk: 0,
    check(c) {
      if (c.phase !== 'between_trips' && c.phase !== 'post_trip') return no('NOT_AT_DECISION_POINT');
      if (!(c.declaredWithin7d || c.repeatPredicted)) return no('NOT_AT_DECISION_POINT');
      if (c.paidShare < 0.3) return no('PAID_SHARE_BELOW_30');
      if (c.orderLast30d) return no('ACTION_SUPPRESSED');
      return ok;
    },
  },

  // ---------------- Tier 2: growth
  {
    id: 'T1', tier: 2, rank: 1, phase: 'post_trip', key: 'always_on_enrol', name: 'Claim 1 GB a month with Always On',
    channels: ['email', 'push'], minConf: 'high', underTest: true, needsConfirmedEnd: true,
    trigger: 'Trip end confirmed (day 16), plus 3 days',
    eligibility: 'Always On eligible; not activated',
    suppression: 'Always On prompt in last 30 days',
    metric: 'Always On activation within 14 days; incremental 12-month net revenue',
    value: { base: 0.05, lift: 0.05, perConv: 30 },
    check(c) {
      if (c.phase !== 'post_trip' || c.daysAfterConfirm < 3) return no('NOT_AT_DECISION_POINT');
      if (!c.aoEligible || c.aoStatus === 'active') return no('NOT_ELIGIBLE');
      if (c.aoPrompt30d) return no('ACTION_SUPPRESSED');
      return ok;
    },
  },
  {
    id: 'T2', tier: 2, rank: 2, phase: 'post_trip', key: 'next_destination', name: 'Your eSIM works in 190+ destinations',
    channels: ['email'], minConf: 'high', underTest: true, needsConfirmedEnd: true,
    trigger: 'Trip end plus 7 days, or a new calendar lead',
    eligibility: 'No order in last 30 days',
    suppression: 'T1 chosen in last 3 days',
    metric: 'Repeat order within 60 days vs holdout',
    value: { base: 0.1, lift: 0.025, perConv: 25 }, capture: true,
    check(c) {
      if (c.phase !== 'post_trip' || c.daysAfterConfirm < 5) return no('NOT_AT_DECISION_POINT');
      if (c.orderLast30d) return no('NOT_ELIGIBLE');
      if (c.t1Last3d) return no('ACTION_SUPPRESSED');
      return ok;
    },
  },
  {
    id: 'T3', tier: 2, rank: 3, phase: 'post_trip', key: 'app_install_posttrip', name: 'Keep your eSIM and perks in the app',
    channels: ['email'], minConf: 'high', underTest: true, needsConfirmedEnd: true, appEncouragement: true,
    trigger: 'Trip end confirmed, app not installed',
    eligibility: 'first_purchase_channel = web',
    suppression: 'P2 sent this trip and ignored; T1 chosen in last 3 days',
    metric: 'first_open within 14 days; in-app purchase within 90 days vs holdout',
    value: { base: 0.04, lift: 0.04, perConv: 7 },
    check(c) {
      if (c.phase !== 'post_trip') return no('NOT_AT_DECISION_POINT');
      if (c.firstChannel !== 'web' || c.appInstalled) return no('NOT_ELIGIBLE');
      if (c.appIdentity === 'unknown') return no('APP_IDENTITY_UNKNOWN');
      if (c.p2IgnoredThisTrip || c.t1Last3d) return no('ACTION_SUPPRESSED');
      return ok;
    },
  },
  {
    id: 'P2', tier: 2, rank: 4, phase: 'pre_trip', key: 'app_install_pretrip', name: 'Install the app to manage the eSIM',
    channels: ['email'], minConf: 'low', underTest: true, appEncouragement: true,
    trigger: 'Order on web, 24 h later',
    eligibility: 'first_purchase_channel = web; app not installed',
    suppression: 'P1 chosen in the same window (P1 outranks P2)',
    metric: 'App first_open within 14 days; in-app purchase within 30 days vs holdout',
    value: { base: 0.06, lift: 0.05, perConv: 6 },
    check(c) {
      if (c.phase !== 'pre_trip' || c.hoursSinceOrder < 24) return no('NOT_AT_DECISION_POINT');
      if (c.firstChannel !== 'web' || c.appInstalled) return no('NOT_ELIGIBLE');
      if (c.appIdentity === 'unknown') return no('APP_IDENTITY_UNKNOWN');
      return ok;
    },
  },
  {
    id: 'P3', tier: 2, rank: 5, phase: 'pre_trip', key: 'feature_primer', name: 'Prime one companion feature (safe browsing)',
    channels: ['push', 'in_app'], minConf: 'low', underTest: true,
    trigger: 'App installed, 48 h before declared departure or at install',
    eligibility: 'App installed; feature never used',
    suppression: 'Any pre-trip send in last 48 h',
    metric: 'Feature toggled during the trip',
    value: { base: 0.12, lift: 0.004, perConv: 5 },
    check(c) {
      if (c.phase !== 'pre_trip' || !c.appInstalled) return no('NOT_AT_DECISION_POINT');
      if (!(c.declaredDepartureDays !== null && c.declaredDepartureDays <= 2 && c.declaredDepartureDays >= 0) && !c.justInstalled) return no('NOT_AT_DECISION_POINT');
      if (c.featureUsed) return no('FEATURE_USED');
      return ok;
    },
  },

  // ---------------- Tier 3: advocacy (X1 = I7 in-trip / T4 post-trip)
  {
    id: 'I7', tier: 3, rank: 1, phase: 'in_trip', key: 'referral_intrip', name: 'Refer a friend (in-trip)',
    channels: ['in_app', 'email'], minConf: 'high', underTest: true, family: 'X1',
    trigger: 'Day 5 or later, right after a success moment (top-up, Always On claim, steady usage)',
    eligibility: 'High-confidence trip; no care contact this trip',
    suppression: 'Any send in last 48 h; referral prompt in last 60 days',
    metric: 'Share within 7 days; referee first order within 30 days; referee net revenue minus reward cost (reported separately)',
    value: { base: 0.02, lift: 0.04, perConv: 9 },
    check(c) {
      if (c.phase !== 'in_trip' || c.daysInTrip < 5 || !c.successMoment) return no('NOT_AT_DECISION_POINT');
      if (c.careThisTrip) return no('CARE_THIS_TRIP');
      if (c.referralLastPromptDays < 60) return no('REFERRAL_PROMPT_60D');
      if (c.referralPrompts12m >= 3) return no('REFERRAL_LIMIT_12M');
      return ok;
    },
  },
  {
    id: 'T4', tier: 3, rank: 2, phase: 'post_trip', key: 'referral_posttrip', name: 'Refer a friend (post-trip)',
    channels: ['email'], minConf: 'high', underTest: true, family: 'X1', needsConfirmedEnd: true,
    trigger: '3 days after a promoter score (9–10) on F1; otherwise trip end plus 7 days if no care contact during the trip',
    eligibility: 'Not prompted in-trip (I7); fewer than 3 referral prompts in 12 months; promoter-likely first',
    suppression: 'I7 sent this trip; referral prompt in last 60 days',
    metric: 'Referee first order within 30 days; referee net revenue minus reward cost',
    value: { base: 0.02, lift: 0.035, perConv: 9 },
    check(c) {
      if (c.phase !== 'post_trip') return no('NOT_AT_DECISION_POINT');
      const promoterDue = c.npsScore !== null && c.npsScore >= 9 && c.daysSinceNps >= 3;
      const defaultDue = c.daysAfterConfirm >= 5 && !c.careThisTrip;
      if (!promoterDue && !defaultDue) return no('NOT_AT_DECISION_POINT');
      if (c.i7ThisTrip) return no('ACTION_SUPPRESSED');
      if (c.referralLastPromptDays < 60) return no('REFERRAL_PROMPT_60D');
      if (c.referralPrompts12m >= 3) return no('REFERRAL_LIMIT_12M');
      return ok;
    },
  },
  {
    id: 'B2', tier: 3, rank: 3, phase: 'between_trips', key: 'seasonal_intent', name: 'Seasonal next-trip prompt',
    channels: ['email'], minConf: 'none', underTest: true,
    trigger: "60–120 days after trip end, or 90 days before the anniversary of last year's trip",
    eligibility: 'No order in last 60 days; not in holdout',
    suppression: 'B1 in last 7 days; any send in last 14 days',
    metric: 'Repeat order within 60 days vs holdout; guardrail: unsubscribe rate',
    value: { base: 0.06, lift: 0.02, perConv: 25 }, capture: true,
    check(c) {
      if (c.phase !== 'between_trips') return no('NOT_AT_DECISION_POINT');
      const due = (c.daysSinceTripEnd >= 60 && c.daysSinceTripEnd <= 120) || c.anniversaryWindow;
      if (!due) return no('NOT_AT_DECISION_POINT');
      if (c.orderLast60d) return no('NOT_ELIGIBLE');
      if (c.hoursSinceLastSend < 14 * 24) return no('ACTION_SUPPRESSED');
      return ok;
    },
  },
];

// Fixed-rule sends: scheduled by the service before ranking, never optimised.
export const FIXED = [
  {
    id: 'F1', key: 'trip_feedback', name: 'Trip feedback, 0–10 score', channels: ['in_app', 'email'],
    moment: '24 h after plan end or provisional trip end (day 15), whichever comes first',
    who: 'Every customer with a trip; not sentiment-gated',
    rules: 'Holafly-owned survey. 0–6 opens an Intercom care follow-up within 24 h and pauses marketing for 7 days; 7–8 nothing; 9–10 makes the customer a promoter for X1.',
  },
  {
    id: 'R1', key: 'review_invite', name: 'Public review invitation (Trustpilot or Google)', channels: ['email'],
    moment: 'Confirmed trip end (day 16) plus 3 days; one invitation per trip',
    who: 'Every customer with a confirmed trip, regardless of score; only deferral is an open care conversation',
    rules: 'Policy send, not optimised: no action-level control, no sentiment or engagement targeting, no timing tests, no incentive. Counts toward the cap.',
  },
  {
    id: 'R2', key: 'rating_prompt', name: 'App-store rating prompt (native)', channels: ['in_app'],
    moment: 'First app session after a success moment; never on launch, never as the result of a tap',
    who: 'App users, at most once per app version',
    rules: 'The NBA only sets rating_prompt_allowed; the app enforces Apple (3 per 365 days) and Google Play quotas. Costs no send budget.',
  },
];

export const ALL_ACTION_IDS = [...ACTIONS.map((a) => a.id), ...FIXED.map((f) => f.id)];
export const ACTION_BY_ID = Object.fromEntries([...ACTIONS, ...FIXED].map((a) => [a.id, a]));

export const REASON_TEXT = {
  HOLDOUT: 'Customer is in the measurement holdout; no NBA marketing until the quarter ends',
  CARE_OPEN: 'Open Intercom conversation in the last 72 h',
  REFUNDED: 'Refunded or cancelled order for the eSIM concerned',
  EMAIL_COMPLAINT: 'Hard bounce or spam complaint on email in the last 90 days',
  CAP_REACHED: 'Marketing sends already at the cap in the last 30 days',
  BUDGET_RESERVED_FOR_TIER0: 'Two sends held back for connectivity actions while pre-trip or in-trip',
  RECENCY_GAP: 'Last marketing send less than 24/48/72 h ago',
  LOW_TRIP_CONFIDENCE: 'Activation seen but no usage outside the home country yet',
  CONTENT_FATIGUE: 'Same message ignored twice in 60 days',
  NO_DELIVERABLE_CHANNEL: 'No consented, deliverable channel for this action',
  ACTION_CONTROL: "Randomly assigned to this action's control group",
  OUTRANKED: 'Eligible, but a higher-tier action was chosen',
  USAGE_STALE: 'Usage data older than 48 h; run-out prompt withheld',
  NO_ELIGIBLE_ACTION: 'Nothing useful to say at this point of the trip',
  CAPTURE_ONLY: 'Chosen for capture value: likely to buy anyway, and the click would otherwise go to a paid channel',
  // Additions used by this prototype to show the rule that fired:
  KILL_SWITCH: 'Action paused by an operator kill switch',
  DEMOTED: 'No revenue uplift in its control after 60 days; demoted to no message',
  CAPTURE_BUDGET_SPENT: 'Capture sub-budget (2 of the 8 sends) already used',
  NOT_AT_DECISION_POINT: 'Not at this action’s decision point',
  NOT_ELIGIBLE: 'Does not meet the eligibility rule',
  ACTION_SUPPRESSED: 'Action-specific suppression applies',
  OPERATOR_EXCLUDE: 'Removed from NBA marketing by an operator (nba_exclude)',
  MANUAL_ACTION: 'Forced by an operator (nba_manual_action); excluded from uplift training',
  FIXED_RULE: 'Fixed-rule send scheduled before ranking',
  SCORE_NOT_POSITIVE: 'Uplift ranking: S(a) is not above zero, so no message wins',
  DATA_GAP: 'Needs data that does not exist yet (see data gaps)',
};
