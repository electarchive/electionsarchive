import { part } from "svelte-islands/server";
import { archive, call, capped, soon } from "#lib/server/pages.js";
import { router } from "#lib/server/rpc/router.js";
import { talkLink } from "#lib/server/talk.js";
import type { PageServerLoad } from "./$types";
import { tallied } from "./tally.js";

async function seed(path: string) {
  const located = await call(router.elections.locate, { path });
  const [hub, hexes] = await Promise.all([
    call(router.elections.get, { id: located.id }),
    call(router.seats.hexes, { id: located.id }),
  ]);
  return { path, located, hub, hexes };
}

export const load: PageServerLoad = async ({ params, url }) => {
  const data = await archive(params.path, url.searchParams);
  if (data.kind === "contest") {
    return { ...data, talk: await talkLink(data.id) };
  }
  if (data.kind === "election") {
    const { races, contests } = data.election;
    const placed = new Set(
      races.flatMap((race) => [
        ...(race.places?.rows.flatMap((row) => row.path ?? []) ?? []),
        ...(race.path == null ? [] : [race.path]),
      ]),
    );
    return {
      ...data,
      election: {
        ...data.election,
        unplaced: races.some((race) => race.deferred != null)
          ? []
          : contests.flatMap((contest) =>
              contest.path == null || placed.has(contest.path)
                ? []
                : [{ name: contest.name, path: contest.path }],
            ),
        races: races.map((race) => ({
          ...race,
          places:
            race.places == null
              ? null
              : {
                  ...race.places,
                  rows: part("election.places", race.places.rows),
                  tally: tallied(race.places),
                },
        })),
      },
    };
  }
  if (data.kind !== "polity") {
    return data;
  }
  const { polities } = data.polity;
  const path = ["sovereign_state", "supranational"].includes(data.polity.kind)
    ? data.polity.latest?.path
    : null;
  const [{ value: scrubber }, { value: record }] = await Promise.all([
    path == null ? { value: null } : soon(capped(seed(path).catch(() => null))),
    soon(
      capped(call(router.polities.record, { id: data.id }).catch(() => null)),
    ),
  ]);
  const groups = Map.groupBy(polities, (child) => child.kind);
  const places = {
    total: polities.length,
    sizes: Object.fromEntries(
      groups.entries().map(([kind, rows]) => [kind, rows.length]),
    ),
    isPartial: polities.length > 200,
  };
  if (!places.isPartial) {
    return { ...data, polity: { ...data.polity, places, scrubber, record } };
  }
  const kept = new Set([
    ...groups.values().flatMap((rows) => rows.slice(0, 20)),
    ...polities
      .filter(
        (child) => child.kind === "municipality" && child.population != null,
      )
      .toSorted((a, b) => (b.population ?? 0) - (a.population ?? 0))
      .slice(0, 10),
  ]);
  return {
    ...data,
    polity: {
      ...data.polity,
      polities: polities.filter((child) => kept.has(child)),
      places,
      scrubber,
      record,
    },
  };
};
