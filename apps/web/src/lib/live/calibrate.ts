import type { LiveEstimate } from "@ea/analytics/live";

const Z = 1.2816;
const LEAN = 0.12;
const FLOOR = 0.0015;

export function phi(value: number) {
  const t = 1 / (1 + 0.2316419 * Math.abs(value));
  const d = 0.3989423 * Math.exp((-value * value) / 2);
  const tail =
    d *
    t *
    (0.3193815 +
      t * (-0.3565638 + t * (1.781478 + t * (-1.821256 + t * 1.330274))));
  return value >= 0 ? 1 - tail : tail;
}

export function calibrate(estimate: LiveEstimate): LiveEstimate {
  const { leader, runnerUp } = estimate;
  if (estimate.status !== "estimating" || leader == null || runnerUp == null) {
    return estimate;
  }
  const { low, mid, high } = estimate.margin;
  const left = Math.max(0, 1 - Math.min(1, estimate.shareIn.mid));
  const model = Math.max(0, high - low) / (2 * Z);
  const spread = Math.hypot(model, left * LEAN, FLOOR);
  const chance = phi(mid / spread);
  return {
    ...estimate,
    margin: {
      low: Math.min(low, mid - Z * spread),
      mid,
      high: Math.max(high, mid + Z * spread),
    },
    winProbability: {
      ...estimate.winProbability,
      [leader]: chance,
      [runnerUp]: 1 - chance,
    },
  };
}
