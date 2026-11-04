import type { LiveElection, LiveEstimate } from "@ea/analytics/live";
import { part } from "svelte-islands/server";
import type { Changing, Head, Stable } from "#lib/live/board.js";
import type { Control, Trail } from "#lib/live/nation.js";
import type { LivePage } from "#lib/server/live-page.js";
import { holder } from "#lib/live/ballot.js";
import { split } from "#lib/live/board.js";
import { pulse, sides } from "#lib/live/nation.js";
import { liveBallot } from "#lib/server/live-ballot.js";
import { livePage } from "#lib/server/live-page.js";
import type { PageServerLoad } from "./$types";

const hubs = new WeakMap<
  LiveElection,
  {
    control: Control | null;
    estimates: Record<string, LiveEstimate>;
    trail: Trail;
    head: Head;
    stable: Stable;
    changing: Changing;
  }
>();

export const load: PageServerLoad = async ({ params, url }) => {
  const segments = params.path.split("/");
  const [page, ballot, state] = await Promise.all([
    livePage(params.path, url.searchParams.get("at")),
    liveBallot(params.path),
    segments.length === 4
      ? liveBallot(segments.slice(0, 3).join("/"))
      : Promise.resolve(null),
  ]);
  const found =
    page.nation != null
      ? hub(page)
      : {
          ...(page.isRace && page.place == null ? slim(page) : page),
          trail: null,
          board: null,
        };
  const contest =
    found.focus == null
      ? null
      : (state?.contests.find((entry) => entry.contest.slug === found.focus)
          ?.contest ?? null);
  return {
    ...found,
    ballot,
    seat:
      contest == null || contest.isMeasure
        ? null
        : {
            held: holder(state?.parties ?? {}, contest.held),
            families: state?.families ?? {},
          },
  };
};

function hub(page: LivePage) {
  const { election, estimates, control, ...rest } = page;
  let made = hubs.get(election);
  if (made?.control !== control || made.estimates !== estimates) {
    const pair = sides(control);
    const { races } = election;
    made = {
      control,
      estimates,
      trail:
        pair == null
          ? []
          : pulse(
              races.filter((race) => race.office === "house"),
              control,
              pair,
            ),
      ...split(
        {
          ...election,
          races: races.map((race) => ({
            ...race,
            frames: race.frames.slice(-1),
          })),
        },
        estimates,
        control,
      ),
    };
    hubs.set(election, made);
  }
  return {
    ...rest,
    trail: made.trail,
    board: {
      head: made.head,
      stable: part("live.races", made.stable, { fetch: "preload" }),
      changing: part("live.counts", made.changing, { fetch: "preload" }),
    },
  };
}

function slim(page: LivePage) {
  const { races } = page.election;
  const focus = races.find((race) => race.slug === page.focus) ?? null;
  const anchor =
    focus != null && focus.office !== "house"
      ? focus
      : (races.find((race) => race.office === "senate") ??
        races.find((race) => race.office === "governor") ??
        null);
  const full = new Set([focus?.id, anchor?.id]);
  const estimates = { ...page.estimates };
  for (const [id, estimate] of Object.entries(estimates)) {
    if (!full.has(id)) {
      estimates[id] = { ...estimate, units: [] };
    }
  }
  return {
    ...page,
    election: {
      ...page.election,
      races: races.map((race) =>
        full.has(race.id)
          ? race
          : {
              ...race,
              units: [],
              counts: [],
              baseline: [],
              frames: race.frames.slice(-1),
            },
      ),
    },
    estimates,
  };
}
