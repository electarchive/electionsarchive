import { call, ORPCError } from "@orpc/server";
import { error } from "@sveltejs/kit";
import { remember } from "#lib/server/cache.js";
import { geography, states } from "#lib/server/live-board.js";
import { router } from "#lib/server/rpc/router.js";
import type { PageServerLoad } from "./$types";

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

export const load: PageServerLoad = async ({ params, url }) => {
  const first = await night(params.path);
  const isRace = first.election.slug !== params.path;
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
      ? await remember(`live:nation:${election.date}`, async () =>
          states(election.date),
        )
      : null,
    geography:
      isNation || focus == null
        ? null
        : await remember(`live:geography:${focus.slug}`, async () =>
            geography(areas, election.date),
          ),
    at: url.searchParams.get("at"),
  };
};
