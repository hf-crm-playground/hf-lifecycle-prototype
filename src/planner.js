// Diminishing-returns curves for the next-euro planner (gross profit per month from monthly spend).
export const DEFAULT_PLANNER = { total: 50000, retention: 8000 };

const R_MAX = 46700;
const R_K = 10000;
const A_MAX = 169100;
const A_K = 60000;

export const retentionProfit = (s) => R_MAX * (1 - Math.exp(-s / R_K));
export const acquisitionProfit = (s) => A_MAX * (1 - Math.exp(-s / A_K));
export const retentionMarginal = (s) => (R_MAX / R_K) * Math.exp(-s / R_K);
export const acquisitionMarginal = (s) => (A_MAX / A_K) * Math.exp(-s / A_K);

export function totalProfit(total, retention) {
  return retentionProfit(retention) + acquisitionProfit(total - retention) - total;
}

export function bestSplit(total, step = 250) {
  let best = { retention: 0, profit: -Infinity };
  for (let r = 0; r <= total; r += step) {
    const p = totalProfit(total, r);
    if (p > best.profit) best = { retention: r, profit: p };
  }
  return best;
}
