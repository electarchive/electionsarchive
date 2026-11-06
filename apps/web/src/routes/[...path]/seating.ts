export function seatingOf<T extends { start: string }>(
  makeup: Array<T>,
  date: string | null | undefined,
) {
  const held = Date.parse(date ?? "");
  const term = makeup.find(
    (entry) =>
      Date.parse(entry.start) >= held &&
      Date.parse(entry.start) - held < 1.2e10,
  );
  if (term == null) {
    return null;
  }
  let next: string | undefined;
  for (const entry of makeup) {
    if (entry.start > term.start && (next == null || entry.start < next)) {
      next = entry.start;
    }
  }
  return { term, next };
}

export function isCaucused(
  term: { parties: Array<{ caucus: unknown }> },
  body: string,
  races: Array<{
    body: string | null;
    results: Array<{
      seats?: number | null;
      party: unknown;
      isIndependent?: boolean | null;
    }>;
  }>,
) {
  return (
    term.parties.some((entry) => entry.caucus != null) &&
    races.some(
      (race) =>
        race.body === body &&
        race.results.some(
          (row) =>
            (row.seats ?? 0) > 0 &&
            row.party == null &&
            row.isIndependent === true,
        ),
    )
  );
}
