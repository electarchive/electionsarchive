import type { Rules } from "./rules.ts";

type Contingent = Extract<Rules, { formula: "contingent" }>;

export function elect(
  rules: Contingent,
  ballot: {
    options: Array<{ id: string; votes: number }>;
    appointed?: number;
    districts?: Array<{ winner: string | null; weight?: number }>;
  },
) {
  const ranked = ballot.options.toSorted((a, b) => b.votes - a.votes);
  let total = ballot.appointed;
  if (rules.of === "votes") {
    total = 0;
    for (const option of ballot.options) total += option.votes;
  }
  if (total == null) throw new Error("A majority of electors needs appointed");
  const leader = ranked[0];
  let isMajority =
    leader != null &&
    leader.votes > rules.majority * total &&
    leader.votes !== ranked[1]?.votes;
  if (isMajority && rules.districts) {
    if (ballot.districts == null)
      throw new Error("A majority of districts needs the districts");
    let won = 0;
    let all = 0;
    for (const { winner, weight = 1 } of ballot.districts) {
      all += weight;
      if (winner === leader?.id) won += weight;
    }
    isMajority = won * 2 > all;
  }
  if (isMajority && leader != null)
    return { outcome: "elected" as const, winner: leader.id };
  const cut = ranked[rules.among - 1]?.votes ?? 0;
  return {
    outcome: "contingent" as const,
    chooser: rules.chooser,
    finalists: ranked
      .filter((option, k) => k < rules.among || option.votes === cut)
      .map((option) => option.id),
  };
}
