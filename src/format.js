export function eur(v, opts = {}) {
  if (Math.abs(v) < 0.005 && !opts.exact) return '€0';
  const sign = v < 0 ? '−' : '';
  const a = Math.abs(v);
  if (opts.exact || a < 1000) {
    const digits = opts.digits ?? (a < 10 ? 2 : 0);
    return `${sign}€${a.toLocaleString('en-GB', { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
  }
  if (a < 1_000_000) return `${sign}€${(a / 1000).toFixed(a < 10_000 ? 1 : 0)}k`;
  return `${sign}€${(a / 1_000_000).toFixed(1)}M`;
}

export const int = (v) => Math.round(v).toLocaleString('en-GB');
export const pct = (v, d = 0) => `${(v * 100).toFixed(d)}%`;
