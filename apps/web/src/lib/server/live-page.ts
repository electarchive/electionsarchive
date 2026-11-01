import { call, ORPCError } from "@orpc/server";
import { error } from "@sveltejs/kit";
import { placeSlug } from "#lib/live/format.js";
import { remember } from "#lib/server/cache.js";
import { geography, house, states } from "#lib/server/live-board.js";
import { router } from "#lib/server/rpc/router.js";

async function night(path: string) {
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
        unit.scheme === "us.county" && unit.areaId != null
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
  const map = await remember(`live:place:v2:${path}/${slug}`, async () =>
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
    house: null,
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
  const found = isRace ? await night(first.election.slug) : first;
  const { election } = found;
  const isNation = election.slug.split("/").length === 2;
  const focus = isRace
    ? (first.election.races[0] ?? null)
    : (election.races.find((race) => race.office === "senate") ??
      election.races.find((race) => race.office === "governor") ??
      election.races[0] ??
      null);
  const areas = [
    ...new Set(
      (focus?.units ?? []).flatMap((unit) =>
        unit.areaId == null ? [] : [unit.areaId],
      ),
    ),
  ];
  return {
    ...found,
    focus: isNation ? null : (focus?.slug ?? null),
    isRace,
    nation: isNation
      ? await remember(`live:nation:v2:${election.date}`, async () =>
          states(election.date),
        )
      : null,
    house: isNation
      ? await remember(`live:house:v2:${election.date}`, async () =>
          house(election.id, election.date),
        )
      : null,
    geography:
      isNation || focus == null
        ? null
        : await remember(`live:geography:v2:${focus.slug}`, async () =>
            geography(areas, election.date),
          ),
    place: null,
    at,
  };
}

export type LivePage = Awaited<ReturnType<typeof livePage>>;
