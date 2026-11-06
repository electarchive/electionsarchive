import type { orpc } from "#lib/rpc.js";

type Compared = Awaited<ReturnType<typeof orpc.contests.get.call>>;

export function swingKey(
  race: { path: string | null; anchor: string },
  by: string | undefined,
) {
  return ["swing", race.path ?? race.anchor, by] as const;
}

export function isSwung(race: {
  path: string | null;
  count: number;
  map: unknown;
  places: unknown;
  results: Array<unknown>;
}) {
  return (
    race.path != null &&
    race.count === 1 &&
    race.map != null &&
    race.places != null &&
    race.results.length !== 1
  );
}

export function swung(compared: Compared, year: string | undefined) {
  return compared.places == null ||
    compared.target?.year == null ||
    year == null ||
    compared.places.rows.every((row) => row.earlier == null)
    ? null
    : {
        places: compared.places,
        span: { from: String(compared.target.year), to: year, forward: false },
      };
}
