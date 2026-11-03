import type { Part } from "svelte-islands";
import { part } from "svelte-islands/server";
import { archive, call, capped, soon } from "#lib/server/pages.js";
import { router } from "#lib/server/rpc/router.js";
import { talkLink } from "#lib/server/talk.js";
import type { PageServerLoad } from "./$types";
import { tallied } from "./tally.js";

const BOARD = 49;
const QUESTIONS = 7;
const CROWD = 200;
const LEGEND = 8;

function parted<T>(
  name: string,
  value: T | Promise<T>,
): T | Promise<T> | Part<NonNullable<T>> {
  return value instanceof Promise || value == null ? value : part(name, value);
}

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
    const { races, contests, elections } = data.election;
    const placed = new Set(
      races.flatMap((race) => [
        ...(race.places?.rows.flatMap((row) => row.path ?? []) ?? []),
        ...(race.path == null ? [] : [race.path]),
      ]),
    );
    const isDeferred = races.some((race) => race.deferred != null);
    const shaped = races.map((race) => {
      const loose = new Set(
        race.results.flatMap((row) => (row.party == null ? [row.key] : [])),
      );
      const staff =
        race.holders == null ? null : part("election.holders", race.holders);
      const staffed = {
        ...race,
        holders:
          race.holders == null || staff?.isInline !== false
            ? race.holders
            : {
                places: race.holders.places.slice(0, 0),
                seats: Object.fromEntries(
                  Object.entries(race.holders.seats).filter(([key]) =>
                    loose.has(key),
                  ),
                ),
              },
        staff: staff?.isInline === false ? staff : null,
      };
      if (race.places == null) {
        return { ...staffed, places: null };
      }
      const { legend, rows } = race.places;
      const tally = tallied(race.places);
      const named = Math.max(
        tally.leads.findLastIndex((leads) => leads > 0) + 1,
        LEGEND,
      );
      return {
        ...staffed,
        places: {
          ...race.places,
          legend: legend.slice(0, named),
          rows: part("election.places", {
            rows,
            legend: legend.length > named ? legend : null,
          }),
          tally,
        },
      };
    });
    const ordered = Map.groupBy(shaped, (race) => race.scope)
      .values()
      .flatMap((group) => [
        ...group.filter((race) => race.results.length !== 1),
        ...group.filter((race) => race.results.length === 1),
      ])
      .toArray();
    const isQuestion = (race: (typeof ordered)[number]) =>
      race.kind === "ballot_question";
    const asked = params.path
      .slice(data.path.length + 1)
      .replace(/-total$/u, "");
    const kept = new Set([
      ...ordered.filter((race) => race.anchor === asked),
      ...ordered.filter((race) => !isQuestion(race)).slice(0, BOARD),
      ...ordered.filter((race) => isQuestion(race)).slice(0, QUESTIONS),
    ]);
    const isSplit = isDeferred && kept.size < ordered.length;
    const rest = ordered.flatMap((race, at) => (kept.has(race) ? [] : [at]));
    const { sections } = data;
    const { winners } = sections;
    const isSweep =
      sections.mayoral ||
      (!(winners instanceof Promise) && (winners?.tiers.length ?? 0) > 0);
    const children = elections.filter((child) => !child.isStage);
    const isParted =
      !isSweep && winners != null && !(winners instanceof Promise);
    const isCrowded = isParted && children.length > CROWD;
    return {
      ...data,
      sections: {
        ...sections,
        tiers: parted("election.tiers", sections.tiers),
        regions:
          Array.isArray(sections.regions) && sections.regions.length > 0
            ? part("election.regions", sections.regions)
            : sections.regions,
        counties: parted("election.counties", sections.counties),
        winners: isParted
          ? part("election.winners", {
              ...winners,
              children: isCrowded ? children : null,
            })
          : winners,
      },
      election: {
        ...data.election,
        elections: isCrowded
          ? elections.filter((child) => child.isStage)
          : elections,
        contests:
          contests.length === 0 ? null : part("election.contests", contests),
        isCrowded,
        unplaced: isDeferred
          ? []
          : contests.flatMap((contest) =>
              contest.path == null || placed.has(contest.path)
                ? []
                : [{ name: contest.name, path: contest.path }],
            ),
        races: isSplit ? ordered.filter((race) => kept.has(race)) : shaped,
        rest: isSplit
          ? {
              races: ordered.filter(
                (race) => !kept.has(race) && !isQuestion(race),
              ).length,
              questions: ordered.filter(
                (race) => !kept.has(race) && isQuestion(race),
              ).length,
              part: part("election.races", {
                at: rest,
                races: ordered.filter((race) => !kept.has(race)),
              }),
            }
          : null,
      },
    };
  }
  if (data.kind !== "polity") {
    return data;
  }
  const { polities, elections } = data.polity;
  const path = ["sovereign_state", "supranational"].includes(data.polity.kind)
    ? data.polity.latest?.path
    : null;
  const [{ value: scrubber }, { value: record }] = await Promise.all([
    path == null
      ? { value: null }
      : soon(
          capped(
            seed(path)
              .then((found) =>
                part("polity.scrubber", found, { fetch: "preload" }),
              )
              .catch(() => null),
          ),
        ),
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
  const dated = elections.flatMap((election) => election.year ?? []);
  const calendar = {
    count: elections.length,
    first: dated.length === 0 ? null : Math.min(...dated),
    last: dated.length === 0 ? null : Math.max(...dated),
  };
  const polity = {
    ...data.polity,
    elections: part("polity.elections", elections),
    calendar,
    places,
    scrubber,
    record,
  };
  if (!places.isPartial) {
    return { ...data, polity };
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
      ...polity,
      polities: polities.filter((child) => kept.has(child)),
    },
  };
};
