import type { LiveEstimate, LiveRace } from "@ea/analytics/live";
import { call, os } from "@orpc/server";
import * as v from "valibot";
import { live } from "#lib/server/rpc/live.js";

export const nation = os
  .input(
    v.object({
      path: v.pipe(v.string(), v.regex(/^us\/\d{4}-\d{2}-\d{2}-[a-z-]+$/u)),
    }),
  )
  .handler(
    async ({
      input,
    }): Promise<{
      races: Array<LiveRace>;
      estimates: Record<string, LiveEstimate>;
    }> => {
      const { election, estimates } = await call(live.election, input);
      return {
        races: election.races.map((race) => ({
          ...race,
          options: race.options.map((option) => ({
            ...option,
            person: null,
            photo: null,
          })),
          units: [],
          counts: [],
          baseline: [],
          frames: race.frames.slice(-1),
        })),
        estimates: Object.fromEntries(
          Object.entries(estimates).map(([id, estimate]) => [
            id,
            { ...estimate, units: [] },
          ]),
        ),
      };
    },
  );
