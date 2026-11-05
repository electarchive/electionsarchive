import { call, ORPCError } from "@orpc/server";
import { error } from "@sveltejs/kit";
import { placeScheme, placeSlug } from "#lib/live/format.js";
import { remember } from "#lib/server/cache.js";
import { control, geography, pieces, states } from "#lib/server/live-board.js";
import { router } from "#lib/server/rpc/router.js";

export async function night(path: string) {
  try {
    return await call(router.live.election, { path });
  } catch (error_) {
    if (error_ instanceof ORPCError && error_.code === "NOT_FOUND") {
      error(404);
    }
    throw error_;
  }
}

async function place(path: string, slug: string, at: string | null) {
  const found = await night(path);
  if (found.election.slug !== path || path.split("/").length !== 3) {
    error(404);
  }
  const { election } = found;
  const counties = new Map(
    election.races.flatMap((race) =>
      race.units.flatMap((unit) =>
        unit.scheme === placeScheme(race.state) && unit.areaId != null
          ? [[unit.id, { ...unit, areaId: unit.areaId }] as const]
          : [],
      ),
    ),
  );
  const unit = counties
    .values()
    .find((entry) => placeSlug(entry.name) === slug);
  if (unit == null) {
    error(404);
  }
  const map = await remember(`live:place:v3:${path}/${slug}`, async () =>
    geography(
      [...new Set([...counties.values()].map((entry) => entry.areaId))],
      election.date,
      [unit.areaId],
    ),
  );
  return {
    ...found,
    focus: null,
    isRace: false,
    nation: null,
    control: null,
    geography: map == null ? null : { ...map, frame: widen(map.frame) },
    place: { id: unit.id, areaId: unit.areaId, name: unit.name, slug },
    at,
  };
}

function widen(frame: {
  west: number;
  south: number;
  east: number;
  north: number;
}) {
  const x = (frame.east - frame.west) * 0.45;
  const y = (frame.north - frame.south) * 0.45;
  return {
    west: frame.west - x,
    south: frame.south - y,
    east: frame.east + x,
    north: frame.north + y,
  };
}

export async function livePage(path: string, at: string | null) {
  const matched = /^(.+)\/place\/([a-z0-9-]+)$/u.exec(path);
  if (matched?.[1] != null && matched[2] != null) {
    return place(matched[1], matched[2], at);
  }
  const first = await night(path);
  const isRace = first.election.slug !== path;
  const whole = isRace ? await night(first.election.slug) : first;
  const extra = isRace
    ? first.election.races.filter((race) =>
        whole.election.races.every((entry) => entry.id !== race.id),
      )
    : [];
  const found =
    extra.length === 0
      ? whole
      : {
          ...whole,
          election: {
            ...whole.election,
            races: [...whole.election.races, ...extra],
          },
          estimates: { ...whole.estimates, ...first.estimates },
        };
  const { election } = found;
  const isNation = election.slug.split("/").length === 2;
  const focus = isRace
    ? (first.election.races[0] ?? null)
    : (election.races.find((race) => race.office === "senate") ??
      election.races.find((race) => race.office === "governor") ??
      election.races[0] ??
      null);
  const units = focus?.units ?? [];
  const reported = new Set(focus?.counts.map((entry) => entry.unitId));
  const scheme =
    units.find(
      (unit) =>
        reported.has(unit.id) &&
        unit.scheme === placeScheme(focus?.state ?? ""),
    )?.scheme ??
    units.find((unit) => reported.has(unit.id))?.scheme ??
    units[0]?.scheme;
  const areas = [
    ...new Set(
      units.flatMap((unit) =>
        unit.areaId == null || unit.scheme !== scheme ? [] : [unit.areaId],
      ),
    ),
  ];
  const clip = focus?.area ?? null;
  const framed =
    (focus?.office === "other" || focus?.office === "house") && clip != null
      ? [clip]
      : areas;
  const [nation, standing, map] = await Promise.all([
    isNation
      ? remember(`live:nation:v2:${election.date}`, async () =>
          states(election.date),
        )
      : null,
    isNation
      ? remember(`live:control:v3:${election.date}`, async () =>
          control(election.races, election.date),
        )
      : null,
    isNation || focus == null
      ? null
      : remember(
          `live:geography:v9:${focus.slug}:${scheme ?? ""}`,
          async () => {
            const [found, cut] = await Promise.all([
              geography(areas, election.date, framed),
              clip == null || scheme == null
                ? null
                : remember(
                    `live:pieces:v5:${election.date}:${clip}:${scheme}`,
                    async () => pieces(clip, election.date, scheme),
                  ),
            ]);
            const kept = new Set(areas);
            const own =
              cut == null
                ? []
                : cut.pieces.filter((piece) => kept.has(piece.areaId));
            return found == null || cut == null || own.length === 0
              ? found
              : {
                  ...found,
                  places: own.map(({ areaId, k, at }) => ({ areaId, k, at })),
                  pieces: { ...cut, pieces: own },
                };
          },
        ),
  ]);
  return {
    ...found,
    focus: isNation ? null : (focus?.slug ?? null),
    isRace,
    nation,
    control: standing,
    geography: map,
    place: null,
    at,
  };
}

export type LivePage = Awaited<ReturnType<typeof livePage>>;
