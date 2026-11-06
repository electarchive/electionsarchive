export const READS = [
  "bodies",
  "bounties",
  "contests",
  "coverage",
  "elections",
  "entities",
  "histories",
  "indices",
  "live",
  "maps",
  "parties",
  "people",
  "polities",
  "recentChanges",
  "search",
  "seats",
  "tonight",
] as const;

export type Read = (typeof READS)[number];

const names: ReadonlySet<string> = new Set(READS);

export function isRead(name: string | undefined) {
  return name != null && names.has(name);
}
