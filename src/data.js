// Synthetic customer base for the prototype. Seeded, so every visitor sees the same numbers.

export function mulberry32(seed) {
  let a = seed;
  return function () {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const sigmoid = (x) => 1 / (1 + Math.exp(-x));

function weighted(rand, items) {
  const total = items.reduce((s, i) => s + i.w, 0);
  let r = rand() * total;
  for (const item of items) {
    r -= item.w;
    if (r <= 0) return item;
  }
  return items[items.length - 1];
}

export const SOURCES = [
  { key: 'Paid search', w: 0.3, paid: 0.6 },
  { key: 'Paid social', w: 0.22, paid: 0.5 },
  { key: 'Organic search', w: 0.2, paid: 0.25 },
  { key: 'Email signup', w: 0.14, paid: 0.12 },
  { key: 'Referral', w: 0.08, paid: 0.15 },
  { key: 'Direct', w: 0.06, paid: 0.2 },
];

const COUNTRIES = [
  { key: 'DE', w: 0.34 },
  { key: 'NL', w: 0.18 },
  { key: 'FR', w: 0.2 },
  { key: 'ES', w: 0.15 },
  { key: 'IT', w: 0.13 },
];

export const STAGES = ['New', 'Active', 'At-risk', 'Lapsed', 'Won back'];

export function generateCustomers(n = 8000, seed = 20261009) {
  const rand = mulberry32(seed);
  const normal = () => {
    const u = 1 - rand();
    const v = rand();
    return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
  };

  const out = [];
  for (let i = 0; i < n; i++) {
    const source = weighted(rand, SOURCES);
    const country = weighted(rand, COUNTRIES).key;
    const orders = Math.min(25, 1 + Math.floor(Math.exp(normal() * 0.8 + 0.4)));
    const aov = clamp(70 * Math.exp(normal() * 0.35), 25, 260);
    const marginRate = clamp(0.42 + normal() * 0.05, 0.28, 0.58);
    const returnRate = clamp(0.12 + normal() * 0.08, 0, 0.5);
    const interPurchase = orders > 1 ? clamp(55 * Math.exp(normal() * 0.45), 20, 240) : clamp(90 + normal() * 20, 45, 160);
    let daysSince = Math.round(clamp(interPurchase * Math.exp(normal() * 0.65), 1, 420));
    let tenure = Math.round(clamp(daysSince + (orders - 1) * interPurchase * (0.8 + rand() * 0.4), daysSince, 1400));
    if (orders === 1 && rand() < 0.45) {
      daysSince = Math.round(1 + rand() * 55);
      tenure = daysSince;
    }
    const priceSensitivity = clamp(Math.pow(rand(), 0.9) + (source.key === 'Paid social' ? 0.12 : 0), 0, 1);
    const fatigue = clamp(rand() * 0.65 + (orders > 5 ? 0.2 : 0) + (rand() < 0.08 ? 0.3 : 0), 0, 1);
    const consentEmail = rand() < 0.86;
    const consentSms = rand() < 0.38;
    const push = rand() < 0.29;
    const paidShare = clamp(source.paid + normal() * 0.12, 0.02, 0.9);
    const ratio = daysSince / interPurchase;
    const churn = clamp(sigmoid(-1.3 + 1.5 * (ratio - 1) - 0.25 * Math.log(orders) + 2.0 * returnRate + 0.3 * fatigue), 0.02, 0.98);
    const p0 = clamp((1 - churn) * Math.min(1, 30 / interPurchase) * 1.1, 0.01, 0.9);
    const orderProfit = aov * marginRate * (1 - returnRate);
    const value12m = (1 - churn * 0.7) * (365 / interPurchase) * orderProfit;
    const nextPurchaseIn = Math.round(interPurchase - daysSince);

    let stage;
    if (orders === 1 && tenure <= 60) stage = 'New';
    else if (orders > 1 && daysSince < 45 && rand() < 0.07) stage = 'Won back';
    else if (daysSince <= 60) stage = 'Active';
    else if (daysSince <= 150) stage = 'At-risk';
    else stage = 'Lapsed';

    out.push({
      id: `C-${String(10000 + i)}`,
      source: source.key,
      country,
      orders,
      aov,
      marginRate,
      returnRate,
      interPurchase,
      daysSince,
      tenure,
      priceSensitivity,
      fatigue,
      sends30d: Math.round(fatigue * 14),
      consentEmail,
      consentSms,
      push,
      paidShare,
      churn,
      p0,
      orderProfit,
      value12m,
      nextPurchaseIn,
      stage,
      holdoutRoll: rand() * 100,
      noise: rand(),
    });
  }
  return out;
}

export const CUSTOMERS = generateCustomers();

export const DEFAULT_ACTIONS = [
  { id: 'email', name: 'Reminder email', channel: 'Email', sendCost: 0.003, discountPct: 0, shipping: 0, baseLift: 0.05, offerLift: 0, capture: 0.15, enabled: true },
  { id: 'push', name: 'Push nudge', channel: 'Push', sendCost: 0.001, discountPct: 0, shipping: 0, baseLift: 0.035, offerLift: 0, capture: 0.12, enabled: true },
  { id: 'email10', name: 'Email + 10% offer', channel: 'Email', sendCost: 0.003, discountPct: 0.1, shipping: 0, baseLift: 0.06, offerLift: 0.25, capture: 0.2, enabled: true },
  { id: 'smsship', name: 'SMS + free shipping', channel: 'SMS', sendCost: 0.07, discountPct: 0, shipping: 4.9, baseLift: 0.08, offerLift: 0.18, capture: 0.25, enabled: true },
  { id: 'email20', name: 'Win-back email + 20% offer', channel: 'Email', sendCost: 0.003, discountPct: 0.2, shipping: 0, baseLift: 0.08, offerLift: 0.5, capture: 0.2, enabled: true },
];

export const DEFAULT_SETTINGS = {
  budget: 3000,
  holdoutPct: 10,
  fatigueCap: true,
  paidExclusions: true,
};

export const DEFAULT_INTEGRATIONS = [
  { id: 'shopify', name: 'Shopify', kind: 'Commerce', role: 'Orders, returns, margins', connected: true, lastSync: '06:00 today' },
  { id: 'hubspot', name: 'HubSpot', kind: 'CRM', role: 'Contacts, consent, deals', connected: true, lastSync: '06:05 today' },
  { id: 'klaviyo', name: 'Klaviyo', kind: 'Email & SMS', role: 'Receives audiences and offers', connected: true, lastSync: '06:10 today', destination: true },
  { id: 'braze', name: 'Braze', kind: 'Email, SMS & push', role: 'Receives audiences and offers', connected: false, lastSync: null, destination: true },
  { id: 'google', name: 'Google Ads', kind: 'Paid media', role: 'Spend, conversions, exclusions', connected: true, lastSync: '05:45 today', ads: true },
  { id: 'meta', name: 'Meta Ads', kind: 'Paid media', role: 'Spend, conversions, exclusions', connected: true, lastSync: '05:45 today', ads: true },
  { id: 'microsoft', name: 'Microsoft Ads', kind: 'Paid media', role: 'Spend, conversions, exclusions', connected: false, lastSync: null, ads: true },
  { id: 'bigquery', name: 'BigQuery', kind: 'Data warehouse', role: 'Raw events and model outputs', connected: false, lastSync: null },
];

export const DRIVERS = {
  churn: {
    label: 'Churn risk',
    note: 'What best predicts that a customer will not buy again in the next 90 days.',
    items: [
      { name: 'Days since last order vs usual gap', weight: 0.31, read: 'Customers past their usual buying gap are the strongest churn signal.' },
      { name: 'Return rate', weight: 0.17, read: 'Frequent returners lapse faster and are worth less when they do buy.' },
      { name: 'Number of orders', weight: 0.14, read: 'Each extra order makes the next one more likely.' },
      { name: 'Message fatigue', weight: 0.11, read: 'Customers getting many sends and opening few drift away.' },
      { name: 'Acquisition channel', weight: 0.09, read: 'Paid-social customers churn more than referral or email sign-ups.' },
      { name: 'Share of orders on discount', weight: 0.08, read: 'Discount-only buyers rarely come back at full price.' },
      { name: 'Average order value', weight: 0.05, read: 'Higher baskets churn slightly less.' },
      { name: 'Country', weight: 0.05, read: 'Small differences by market, mostly from delivery times.' },
    ],
  },
  value: {
    label: 'Customer value',
    note: 'What best predicts profit over the next 12 months, after returns and discounts.',
    items: [
      { name: 'Order frequency', weight: 0.29, read: 'How often they buy drives most of future profit.' },
      { name: 'Average order value', weight: 0.21, read: 'Bigger baskets, more profit per order.' },
      { name: 'Margin of products bought', weight: 0.16, read: 'Category mix sets the profit per euro of revenue.' },
      { name: 'Return rate', weight: 0.13, read: 'Returns erase margin and add handling cost.' },
      { name: 'Tenure', weight: 0.09, read: 'Long-standing customers are steadier.' },
      { name: 'Acquisition channel', weight: 0.07, read: 'Referral customers are worth more over time.' },
      { name: 'Country', weight: 0.05, read: 'Shipping cost differs by market.' },
    ],
  },
  uplift: {
    label: 'Uplift (who a message moves)',
    note: 'What best predicts that an action changes behavior, not just who will buy.',
    items: [
      { name: 'Churn risk in the middle range', weight: 0.26, read: 'Customers on the fence move most; sure things and lost causes barely move.' },
      { name: 'Price sensitivity', weight: 0.22, read: 'Only price-sensitive customers need an offer; for others it is lost margin.' },
      { name: 'Message fatigue', weight: 0.18, read: 'Heavily messaged customers can react badly: unsubscribes and lapses.' },
      { name: 'Days to predicted next purchase', weight: 0.14, read: 'A nudge just before the usual buying moment works best.' },
      { name: 'Channels with consent', weight: 0.1, read: 'SMS and push reach customers email misses.' },
      { name: 'Share of returns through paid ads', weight: 0.1, read: 'Customers who come back via ads are the cheapest to reach first through owned channels.' },
    ],
  },
};
