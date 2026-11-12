export const eur = (n) => `€${Number(n).toFixed(2)}`;
export const pct = (fraction, digits = 1) => {
  const v = fraction * 100;
  return `${v > 0 ? "+" : ""}${v.toFixed(digits)}%`;
};

export const TIER_LABEL = { high: "High", medium: "Medium", low: "Low" };
export const TIER_RANK = { high: 0, medium: 1, low: 2 };
export const TIER_ACTION = {
  high: "Move to the best price",
  medium: "Test price (half-way, max 5%)",
  low: "Hold the usual price",
};

export function boundNote(row) {
  if (!row.bound || Math.abs(row.move_pct) < 0.005) return ""; // no note when the price does not really move
  return `Capped at the ${row.bound} tested price`;
}
