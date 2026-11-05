// Share Indian-number grouping and unit precision across all operational panels.
export const number = (value, digits = 0) =>
  new Intl.NumberFormat('en-IN', {
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
  }).format(Number(value) || 0);
export const currency = (value) =>
  new Intl.NumberFormat('en-IN', {
    style: 'currency',
    currency: 'INR',
    maximumFractionDigits: 0,
  }).format(Number(value) || 0);
export const compactINR = (value) => {
  const n = Number(value) || 0;
  return n >= 1e7
    ? `₹${(n / 1e7).toFixed(2)} Cr`
    : n >= 1e5
      ? `₹${(n / 1e5).toFixed(2)} L`
      : `₹${number(n)}`;
};
export const holeLabel = (area) =>
  area < 3
    ? '≈ pinhole leak'
    : area < 15
      ? '≈ coin-sized crack'
      : area < 45
        ? '≈ palm-sized break'
        : '≈ mainline blowout';
export const lpm = (value) => `${number(value, 1)} LPM`;
