export const UPPER = ["sovereign_state", "supranational", "federal_unit"];

const NATIONAL = ["sovereign_state", "supranational"];

type Polity = {
  kind: string;
  parent: string | null;
  local: string | null;
  mapped: string | null;
  calendar: { count: number };
  results: Array<{
    races: Array<{ id: string; path: string; unit: string | null }>;
  }>;
  latest: {
    scope: string | null;
    races: Array<{ contest: string | null }>;
  } | null;
};

export function isNationalOf(polity: Pick<Polity, "kind">) {
  return NATIONAL.includes(polity.kind);
}

export function isLocalOf(polity: Polity) {
  return (
    polity.parent != null &&
    polity.results.length > 0 &&
    (polity.calendar.count === 0 || !UPPER.includes(polity.kind)) &&
    !(
      polity.kind === "region" &&
      polity.latest != null &&
      polity.latest.scope == null
    )
  );
}

export function sourceOf(polity: Polity) {
  const isNational = isNationalOf(polity);
  const race = isLocalOf(polity)
    ? (polity.results
        .flatMap((entry) => entry.races)
        .find((entry) => entry.id === polity.local) ??
      polity.results[0]?.races[0])
    : undefined;
  if (race != null) {
    return {
      key: `${race.id}/${race.path}`,
      id: race.id,
      unit: race.unit ?? undefined,
    };
  }
  const headline = isNational
    ? null
    : (polity.latest?.races.find((entry) => entry.contest != null) ?? null);
  const contest = isNational ? null : (polity.mapped ?? headline?.contest);
  return contest == null
    ? null
    : { key: contest, id: contest, unit: undefined };
}

export function upOf(unit: string | undefined) {
  const steps = unit?.split("/") ?? [];
  return steps.length > 1 ? steps.slice(0, -1).join("/") : undefined;
}
