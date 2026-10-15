import data from "./germany-2025.json";

export type Constituency = Omit<
  (typeof data.constituencies)[number],
  "first" | "second"
> & { first: Array<[string, number]>; second: Array<[string, number]> };

export const constituencies = data.constituencies as Array<Constituency>;
export const map = data.map;
export const laender: Record<string, string> = data.laender;
export const summary = data.summary;
export const totalSeats = data.seats;
export const majority = Math.floor(totalSeats / 2) + 1;

const partyKeys: Record<string, string> = {
  CDU: "cdu",
  CSU: "csu",
  AfD: "afd",
  SPD: "spd",
  GRÜNE: "gruene",
  "Die Linke": "linke",
  BSW: "bsw",
  FDP: "fdp",
  "FREIE WÄHLER": "fw",
  SSW: "ssw",
  Tierschutzpartei: "tier",
  Volt: "volt",
};

export function partyColor(code: string | null | undefined): string {
  return `var(--p-${(code != null ? partyKeys[code] : undefined) ?? "other"})`;
}

export const seatOrder = [
  "Die Linke",
  "SPD",
  "SSW",
  "GRÜNE",
  "CDU",
  "CSU",
  "AfD",
];

const threshold = summary.valid_second * 0.05;

export const parties = data.parties.map((party) => ({
  ...party,
  secondShare: party.second / summary.valid_second,
  firstShare: party.first / summary.valid_first,
  passed: party.second >= threshold,
}));

export const tableParties = parties.filter(
  (party) => party.secondShare >= 0.005 || party.seats > 0,
);

const shown = new Set(tableParties.map((party) => party.code));
export const otherParties = { second: 0, first: 0, won: 0 };
for (const party of parties) {
  if (shown.has(party.code)) {
    continue;
  }

  otherParties.second += party.second;
  otherParties.first += party.first;
  otherParties.won += party.won;
}

export const thresholdIndex = tableParties.findIndex((party) => !party.passed);

export const bswShortfall =
  Math.ceil(threshold) -
  (parties.find((party) => party.code === "BSW")?.second ?? 0);

export const unseatedCount = constituencies.filter(
  (entry) => !entry.seated,
).length;

export const turnout = summary.voters / summary.registered;

export const count = new Intl.NumberFormat("en-US");

export function percent(value: number, digits = 1): string {
  return `${(value * 100).toFixed(digits)}%`;
}

export function constituencyTurnout(entry: Constituency): number {
  return entry.voters / entry.registered;
}

export function lead(
  entry: Constituency,
  vote: "first" | "second",
): { party: string; margin: number } {
  const ranked = entry[vote];
  const valid = vote === "first" ? entry.valid_first : entry.valid_second;
  const [top, next] = ranked;
  return {
    party: top?.[0] ?? "",
    margin: ((top?.[1] ?? 0) - (next?.[1] ?? 0)) / valid,
  };
}

export const turnoutRange = (() => {
  let low = 1;
  let high = 0;
  for (const entry of constituencies) {
    const value = constituencyTurnout(entry);
    low = Math.min(low, value);
    high = Math.max(high, value);
  }
  return { low, high };
})();
