import type { Rules } from "./rules.ts";

type TwoRound = Extract<Rules, { formula: "two_round" }>;

export function advance(
  rules: TwoRound,
  round: {
    entries: Array<{ id: string; votes: number }>;
    registered?: number;
    units?: Array<{
      id: string;
      entries: Array<{ id: string; votes: number }>;
    }>;
  },
) {
  let valid = 0;
  for (const { votes } of round.entries) valid += votes;
  const base = (of: "registered" | "valid") => {
    if (of === "valid") return valid;
    if (round.registered == null)
      throw new Error("A share of registered voters needs registered");
    return round.registered;
  };
  const ranked = round.entries.toSorted((a, b) => b.votes - a.votes);
  const top = ranked[0];
  if (
    top != null &&
    top.votes !== ranked[1]?.votes &&
    top.votes > rules.majority * valid &&
    (rules.minimum == null ||
      top.votes >= rules.minimum.share * base(rules.minimum.of)) &&
    isSpread(rules, top.id, round.units)
  )
    return { elected: top.id };
  const { qualify } = rules;
  const qualified =
    qualify == null
      ? []
      : ranked.filter(({ votes }) => votes >= qualify.share * base(qualify.of));
  if (qualified.length >= rules.advance)
    return { advancing: qualified.map(({ id }) => id) };
  const last = ranked[rules.advance - 1];
  const above = ranked.filter(
    ({ votes }) => last != null && votes > last.votes,
  );
  const tied = ranked.filter(({ votes }) => votes === last?.votes);
  const isTied = above.length + tied.length > rules.advance;
  return {
    advancing: (isTied ? above : ranked.slice(0, rules.advance)).map(
      ({ id }) => id,
    ),
    tie: isTied
      ? { ids: tied.map(({ id }) => id), seats: rules.advance - above.length }
      : undefined,
  };
}

function isSpread(
  rules: TwoRound,
  id: string,
  units?: Array<{ id: string; entries: Array<{ id: string; votes: number }> }>,
) {
  const { spread } = rules;
  if (spread == null) return true;
  if (units == null) throw new Error(`A spread needs ${spread.scheme} units`);
  let reached = 0;
  for (const unit of units) {
    let valid = 0;
    let votes = 0;
    for (const entry of unit.entries) {
      valid += entry.votes;
      if (entry.id === id) votes += entry.votes;
    }
    if (valid > 0 && votes >= spread.share * valid) reached += 1;
  }
  const needed = spread.units.share * units.length;
  return spread.units.strict ? reached > needed : reached >= needed;
}
