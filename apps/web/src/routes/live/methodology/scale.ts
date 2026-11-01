import { lead } from "#lib/live/format.js";

export function signed(value: number, even: string) {
  if (Math.abs(value) < 0.0005) {
    return even;
  }
  return `${value > 0 ? "D" : "R"}+${lead(value)}`;
}
