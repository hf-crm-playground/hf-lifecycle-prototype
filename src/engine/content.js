// Reference tables carried over from "Holafly NBA System Design" (23 Sep 2026) and trip_state_builder.

export const ROLLOUT = [
  {
    id: 0, name: 'Verify and build', weeks: [1, 2],
    scope: 'Confirm the "to verify" items; agree the transactional boundary and the net-revenue definition; build the Trip State Builder and decision log; create the NBA Decision dispatcher flow with one branch; write the holdout campaign-exclusion segment; pause overlapping flows for the pilot population.',
    experiments: 'None; backfill 90 days of trip states and check the 14-day rule against known trips.',
    gate: 'Trip state matches manual checks on 50 sampled customers; app–web identity match rate known; cap counter reconciles with Klaviyo events.',
  },
  {
    id: 1, name: 'In-trip pilot', weeks: [3, 6],
    scope: 'Policy v1 for P1, I1, I2, I3 and I4 in two markets (one Spanish-speaking, one English-speaking); 10% global holdout (20% if the eligible base is under 100,000); 10% action controls; email and push only.',
    experiments: 'E1 run-out top-up vs control · E2 activation-help timing · E3 run-out channel for app users',
    gate: 'E1 shows positive uplift with a 95% interval above zero, or the action is redesigned; no guardrail breached for two consecutive weeks.',
  },
  {
    id: 2, name: 'Full trip lifecycle', weeks: [7, 10],
    scope: 'Add T1–T4, B1, B2, I5, I7, P2, P3, plus F1 feedback, R1 review invitation and R2 rating prompt; all markets; WhatsApp for I1 only where senders and consent are confirmed; cap arms 6/8/10.',
    experiments: 'E4 app encouragement · E5 cap arms · E6 Always On claim reminders · E7 referral timing · E9 referral moment · E10 sequencing · review market on/off · E11 last-click capture',
    gate: 'Day-60 read: incremental 30-day revenue per eligible customer with intervals; cap arm chosen or 8 kept; uplift table populated for every action with at least 100 control outcomes.',
  },
  {
    id: 3, name: 'Consolidate and prepare v2', weeks: [11, 13],
    scope: 'Move remaining overlapping flows into the dispatcher; register the campaign calendar with the cap; replace tier tie-breaks with the uplift table; operator decision explorer live; weekly review loop; assess v2 data readiness; Jev shadow-mode pilot.',
    experiments: 'E8 uplift-table ranking vs fixed tier order (revenue and unsubscribes at 30 days)',
    gate: 'Day-90 read published: 90-day incremental revenue, repeat purchase, app adoption, Always On activation and claim rates, guardrails; go or no-go on v2 modelling per action.',
  },
];

export const EXPERIMENTS = [
  { id: 'E1', phase: 1, test: 'Run-out top-up (I1) vs action control', metric: 'Top-up within 48 h' },
  { id: 'E2', phase: 1, test: 'Activation-help timing: 60th-percentile lead time vs 24 h after purchase', metric: 'Install within 72 h' },
  { id: 'E3', phase: 1, test: 'Run-out channel for app users: push-first vs email-first', metric: 'Top-up within 48 h' },
  { id: 'E4', phase: 2, test: 'App encouragement (P2 + T3) vs none, first-time web buyers', metric: 'first_open 14 d; in-app purchase and revenue 90 d; 12-month revenue (sets w_app)' },
  { id: 'E5', phase: 2, test: 'Cap arms: 6 vs 8 vs 10 sends per 30 days', metric: 'Revenue, unsubscribes, complaints' },
  { id: 'E6', phase: 2, test: 'Always On claim reminders: one vs two per month', metric: 'Claim rate, opt-outs' },
  { id: 'E7', phase: 2, test: 'Referral timing: in-trip vs post-trip', metric: 'Referee first orders at 30 days' },
  { id: 'E8', phase: 3, test: 'Uplift-table ranking vs fixed tier order', metric: 'Revenue and unsubscribes at 30 days' },
  { id: 'E9', phase: 2, test: 'Referral moment: success-moment trigger vs day-5 timer', metric: 'Referee net revenue at 30 and 90 days' },
  { id: 'E10', phase: 2, test: 'Sequencing: F1 then X1 for promoters vs X1 alone', metric: 'Share rate, feedback response, referee net revenue' },
  { id: 'E11', phase: 2, test: 'Last-click capture: capture message vs none', metric: 'Paid last-click share, avoided acquisition cost, perk cost, purchase rate' },
];

export const ASSUMPTIONS = [
  { id: 'A1', text: 'Holafly’s backend already sends trip-relevant events to Klaviyo (activation_event, hly_consumption_start, hly_always_on_activated, entitlement_end_date_event, …); payloads, coverage and latency not yet verified.' },
  { id: 'A2', text: 'Web and app identities resolve to one Klaviyo profile (shared email or external_id). If not, app-adoption actions cannot be measured.' },
  { id: 'A3', text: 'Daily data usage per eSIM is available from the connectivity backend; the 14-day trip-end rule must be computed outside Klaviyo.' },
  { id: 'A4', text: 'The €93.60 vs €51.00 12-month LTV gap between in-app and non-app buyers is a correlation; the causal effect is untested until E4.' },
  { id: 'A5', text: 'About eight marketing sends per customer per month across all channels is a starting guardrail, not a validated optimum; Phase 2 tests it.' },
  { id: 'A6', text: 'WhatsApp and SMS are optional channels; sender status and per-market consent must be verified in the Klaviyo UI.' },
];

export const GAPS = [
  { n: 1, gap: 'One customer identity across web, app and Klaviyo (external_id)', why: 'Without it, app adoption cannot be measured and app-install prompts go to app users', owner: 'Data engineering + app team', by: 0 },
  { n: 2, gap: 'Daily data usage per eSIM in the warehouse, with network country and its latency', why: 'Defines trip end, run-out prompts and connectivity-gap days', owner: 'Connectivity backend', by: 0 },
  { n: 3, gap: 'Transactional boundary: which messages are transactional, which system sends them, how they are flagged', why: 'Transactional sends are excluded from the cap and must not be duplicated by NBA prompts', owner: 'CRM + product', by: 0 },
  { n: 4, gap: 'Net revenue per order: margin by plan and destination, data cost, refunds, per-message cost', why: 'The primary metric and the value scale for uplift', owner: 'Finance + CRM analytics', by: 0 },
  { n: 5, gap: 'Always On rules as data: eligibility, claim window, claim API, activation event', why: 'T1 and B1 cannot run without them', owner: 'Always On product owner', by: 1 },
  { n: 6, gap: 'Home-country definition (billing country, first-order market, or profile country)', why: 'Drives the trip-start confidence rule', owner: 'CRM analytics', by: 0 },
  { n: 7, gap: 'Holdout size and duration versus revenue foregone', why: '10% for a quarter is the default; a smaller base needs 20%', owner: 'CRM lead + finance', by: 0 },
  { n: 8, gap: 'The cap counts campaigns', why: 'Eight sends per month is meaningless if campaigns sit outside it', owner: 'CRM lead', by: 1 },
  { n: 9, gap: 'Channel status: WhatsApp senders, markets and consent; whether SMS is still used; push opt-in rate by OS', why: 'Decides the channel lists in the decision table', owner: 'CRM ops', by: 1 },
  { n: 10, gap: 'Advanced KDP licence or nightly Events API pull', why: 'Determines how message outcomes return to the cap counter and how fast', owner: 'CRM ops + data engineering', by: 0 },
  { n: 11, gap: 'Ownership of overlapping flows, including third-party Loiale re-engagement flows', why: 'They must be paused for the NBA population or they contaminate the holdout and break the cap', owner: 'CRM lead', by: 0 },
  { n: 12, gap: 'Plans (subscription) customers: inside the NBA or a separate programme', why: 'Their lifecycle is subscription-based; v1 treats them as a suppression only', owner: 'Product + CRM', by: 1 },
  { n: 13, gap: 'Privacy review for usage-based marketing and in-app targeting', why: 'Usage data is telecoms data in several markets; confirm lawful basis and no device-location use', owner: 'Legal + DPO', by: 0 },
  { n: 14, gap: 'Model ownership for v2: who retrains, monitors and approves promotion', why: 'Without an owner the uplift models decay silently', owner: 'Data science lead', by: 3 },
  { n: 15, gap: 'Operator tooling: which BI tool hosts the decision explorer and the weekly review', why: 'Explainability is only real if operators can see it', owner: 'CRM ops', by: 2 },
  { n: 16, gap: 'Multi-destination coverage events (network country outside plan coverage) for I6', why: 'Enables the add-destination action; otherwise I6 stays out of scope', owner: 'Connectivity backend', by: 3 },
  { n: 17, gap: 'Review invitation ownership: Trustpilot’s invitation service or Klaviyo, never both', why: 'One invitation per experience is a platform rule', owner: 'CRM ops', by: 2 },
  { n: 18, gap: 'Referral economics: reward cost, referee margin, fraud rules, disclosure wording', why: 'Decides whether referral has positive incremental net revenue at all', owner: 'Growth + finance', by: 2 },
  { n: 19, gap: 'Native app-store rating prompt with a rating_prompt_requested event and per-version counter', why: 'R2 cannot run without it; quotas are enforced by the app', owner: 'App team', by: 2 },
  { n: 20, gap: 'Legal sign-off on review solicitation and on routing feedback detractors to care', why: 'Trustpilot guideline and FTC consumer-review rule exposure', owner: 'Legal', by: 2 },
  { n: 21, gap: 'Order-level last-click channel and paid cost per order in the warehouse (Triple Whale or GA4)', why: 'Capture value cannot be computed or measured without it', owner: 'Performance marketing + data engineering', by: 1 },
  { n: 22, gap: 'Perk policy for capture messages: points first, codes only for known coupon users and only when cheaper', why: 'Protects margin; codes given freely teach customers to wait', owner: 'Growth + finance', by: 2 },
  { n: 23, gap: 'Which attribution model the business reports on, and agreement that attributed CRM revenue is reporting, not impact', why: 'Attributed revenue rises by construction once capture runs', owner: 'CRM lead + finance', by: 1 },
  { n: 24, gap: 'Jev preconditions: terms and SLA, DPA, hosting region, retention, limits, 500-case labelled set', why: 'Without them customer text cannot be sent and answers cannot change decisions', owner: 'Data science lead + legal', by: 3 },
];

export const EVENTS = [
  { event: 'Order placed (web)', source: 'Shopify', metric: 'Placed Order', status: 'verify' },
  { event: 'Order confirmed (backend)', source: 'Holafly backend', metric: 'hly_order_confirmed, hly_order_created', status: 'verify' },
  { event: 'Top-up order', source: 'Shopify', metric: 'Top-up order created', status: 'verify' },
  { event: 'App purchase', source: 'App', metric: 'purchase, Purchase', status: 'verify' },
  { event: 'eSIM install started / completed', source: 'App or web', metric: 'esim_installation_start, esim_installation_complete', status: 'verify' },
  { event: 'eSIM activated (trip start)', source: 'Connectivity backend', metric: 'activation_event, hly_consumption_start', status: 'verify' },
  { event: 'Daily data usage', source: 'Connectivity backend', metric: '—', status: 'required' },
  { event: 'Data threshold reached', source: 'Connectivity backend', metric: 'hly_always_on_consumption_threshold', status: 'verify' },
  { event: 'Plan or entitlement ending', source: 'Holafly backend', metric: 'entitlement_end_date_event, expiration_date_eSIM_v2', status: 'verify' },
  { event: 'Trip ended (14 days no usage)', source: 'Trip State Builder', metric: '—', status: 'required' },
  { event: 'App first open and sessions', source: 'App SDK', metric: 'first_open, session_start, screen_view', status: 'verify' },
  { event: 'Companion feature used', source: 'App SDK', metric: 'virtual_location_toggle, safe_browsing_toggle, …', status: 'verify' },
  { event: 'Always On activated', source: 'Always On service', metric: 'hly_always_on_activated', status: 'verify' },
  { event: 'Always On eligibility and monthly claim', source: 'Always On service', metric: '—', status: 'required' },
  { event: 'Referral action', source: 'App and web', metric: 'referral_link_shared, Swell Referral Completed', status: 'verify' },
  { event: 'Plans subscription lifecycle', source: 'Holafly backend', metric: 'hly_plan_created, hly_plan_renewed, hly_plan_cancelled', status: 'verify' },
  { event: 'Care conversation', source: 'Intercom', metric: 'Created Conversation, Resolved Ticket', status: 'verify' },
  { event: 'Consent and message outcomes', source: 'Klaviyo', metric: 'Subscribed to … Marketing, Received / Opened / Clicked, Skipped Send', status: 'verify' },
  { event: 'NBA Decision', source: 'Trip Decision Service', metric: '—', status: 'required' },
  { event: 'Trip feedback score', source: 'Holafly survey', metric: 'Submitted NPS Score', status: 'verify' },
  { event: 'Review invited / submitted', source: 'Trustpilot or Klaviyo', metric: '—', status: 'required' },
  { event: 'Rating prompt requested', source: 'App SDK', metric: '—', status: 'required' },
  { event: 'Order attribution (last click, paid cost)', source: 'Triple Whale or GA4 into the warehouse', metric: 'pixel metrics only', status: 'required' },
];

export const BUILDER_CONFIG = [
  { key: 'trip_gap_days', value: '14', meaning: 'A trip ends after 14 days without usage' },
  { key: 'trip_end_confirm_days', value: '16', meaning: 'End is provisional at 14 days, confirmed at 16 (absorbs 24–48 h usage latency)' },
  { key: 'post_trip_days', value: '30', meaning: 'Post-trip for 30 days after the end day, then between trips' },
  { key: 'usage_stale_hours', value: '48', meaning: 'Older usage cannot support run-out decisions' },
  { key: 'test_install_max_mb', value: '50', meaning: 'Activation at home under 50 MB is a test install, not a trip' },
  { key: 'high_confidence_day_mb', value: '50', meaning: 'Country unknown: a day at or above 50 MB counts as real travel' },
  { key: 'activation_to_usage_max_days', value: '3', meaning: 'Trip starts at activation if within 3 days of first usage' },
  { key: 'long_trip_days', value: '30', meaning: 'After 30 days only run-out, plan-end and Always On actions stay eligible' },
  { key: 'runout_projection_days', value: '3', meaning: 'Burn rate = mean of the last 3 usage days' },
  { key: 'plan_end_soon_hours', value: '48', meaning: '"Plan ending soon" horizon' },
  { key: 'low_data_pct', value: '20', meaning: 'Below 20% remaining the run-out action becomes eligible' },
];

export const KLAVIYO_NATIVE = [
  { cap: 'Metric-triggered flows with filters and re-entry', use: 'The NBA Dispatcher: one flow on NBA Decision, re-entry allowed' },
  { cap: 'Multi-branch and conditional splits', use: 'Route by action and channel; consent check per branch' },
  { cap: 'Flow A/B tests', use: 'Creative variants inside an action; never customer-level holdouts' },
  { cap: 'Create Event API (350/s burst, 3,500/min)', use: 'The activation contract from the Decision Service' },
  { cap: 'Update profile property action', use: 'Mark nba_delivered_at after a send' },
  { cap: 'Segment-triggered flows', use: 'In-app placements via a segment on nba_inapp_action' },
  { cap: 'Smart Sending (per channel)', use: 'Per-channel backstop only; the cross-channel cap is external' },
  { cap: 'Mobile push and in-app forms', use: 'Push for in-trip actions; in-app channel (no inbox)' },
  { cap: 'WhatsApp', use: 'Optional third channel for run-out where consented; never US numbers' },
  { cap: 'Reporting API', use: 'Engagement and guardrail metrics per dispatcher branch' },
];

export const KLAVIYO_EXTERNAL = [
  { need: 'Trip state and the 14-day no-usage rule', why: 'No daily-usage data model or rolling computation', where: 'Trip State Builder' },
  { need: 'Cross-channel frequency cap, reserve and recency', why: 'Smart Sending windows are per channel and independent', where: 'Decision Service cap counter, fed by Klaviyo message events' },
  { need: 'Arbitration between actions', why: 'Flows run independently with no cross-flow priority', where: 'Decision Service: one event per decision point' },
  { need: 'Value of no message and incremental ranking', why: 'Predictive analytics are propensity-style and order-based', where: 'Uplift tables in v1, uplift models in v2' },
  { need: 'Stable customer-level holdout', why: 'Random-sample splits are per path and per flow', where: 'Service-assigned, stored on the profile, excluded from campaigns' },
  { need: 'Acting on a profile property change', why: 'Flow webhooks cannot fire on property changes', where: 'The service emits an event instead' },
  { need: 'Decision log and explanations', why: 'No record of candidates that were not chosen', where: 'Warehouse log plus nba_last_reason on the profile' },
];
