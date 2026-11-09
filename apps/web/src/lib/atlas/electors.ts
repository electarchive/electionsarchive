import type { Electors } from "#lib/atlas/data.js";

export function tally(electors: Electors, k: number) {
  const place =
    k === electors.k ? null : electors.places.find((entry) => entry.k === k);
  if (place === undefined) {
    return null;
  }
  const rows = electors.rows
    .map((row, index) => ({
      ...row,
      votes: place == null ? row.votes : (place.votes[index] ?? 0),
      pledged: place == null ? row.pledged : null,
    }))
    .filter((row) => row.votes > 0)
    .toSorted((a, b) => b.votes - a.votes);
  const appointed = place == null ? electors.appointed : (place.appointed ?? 0);
  let cast = 0;
  for (const row of rows) {
    cast += row.votes;
  }
  const absent =
    appointed === 0
      ? 0
      : Math.max(0, Math.round(appointed - cast / electors.ballots));
  const isPaired = electors.ballots === 2;
  const president =
    electors.house?.person ?? rows.find((row) => row.isElected)?.person ?? null;
  const vice = isPaired
    ? (rows.find((row) => row.isElected && row.person !== president)?.person ??
      null)
    : null;
  let kept = 0;
  for (const row of rows) {
    kept += Math.min(row.votes, row.pledged ?? 0);
  }
  const faithless = rows.some((row) => row.pledged == null)
    ? 0
    : Math.max(0, appointed - absent - kept);
  const [first, second] = rows.filter((row) => row.isTied);
  const house =
    place == null && electors.house != null
      ? {
          ...electors.house,
          tie:
            first != null && second != null
              ? { first: first.label, second: second.label }
              : null,
        }
      : null;
  return {
    isNational: place == null,
    rows: rows.map((row) => ({
      ...row,
      role:
        row.person == null
          ? null
          : isPaired
            ? row.person === president
              ? ("president" as const)
              : row.person === vice
                ? ("vice" as const)
                : null
            : place == null && row.person === president
              ? ("elected" as const)
              : null,
    })),
    appointed,
    absent,
    isPaired,
    faithless,
    house,
  };
}
