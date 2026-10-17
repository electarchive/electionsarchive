import type { Rules } from "./rules.ts";

type Referendum = Extract<Rules, { formula: "referendum" }>;
type Question = NonNullable<
  NonNullable<Referendum["doubleMajority"]>["types"]
>[number];

export function decide(
  rules: Referendum,
  ballot: {
    type: Question;
    yes: number;
    no: number;
    registered?: number;
    voters?: number;
    units?: Array<{ id: string; yes: number; no: number }>;
  },
) {
  if (rules.quorum != null) {
    if (ballot.registered == null || ballot.voters == null)
      throw new Error("A turnout quorum needs registered and voters");
    if (ballot.voters <= rules.quorum * ballot.registered)
      return { outcome: "quorum_failed" as const };
  }
  const share = rules.majority;
  const isPopular =
    share == null
      ? ballot.yes > ballot.no
      : share.strict
        ? ballot.yes > share.share * (ballot.yes + ballot.no)
        : ballot.yes >= share.share * (ballot.yes + ballot.no);
  const double = rules.doubleMajority;
  if (
    double == null ||
    (double.types != null && !double.types.includes(ballot.type))
  ) {
    return {
      outcome: isPopular ? ("adopted" as const) : ("rejected" as const),
    };
  }
  if (ballot.units == null)
    throw new Error(`A double majority needs ${double.scheme} units`);
  const units = { yes: 0, no: 0 };
  for (const { id, yes, no } of ballot.units) {
    units[yes > no ? "yes" : "no"] += double.weights?.[id] ?? 1;
  }
  return {
    outcome:
      isPopular && units.yes > units.no
        ? ("adopted" as const)
        : ("rejected" as const),
    units,
  };
}
