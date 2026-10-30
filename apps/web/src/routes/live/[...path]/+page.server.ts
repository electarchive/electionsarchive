import { dev } from "$app/env";
import { call, ORPCError } from "@orpc/server";
import { error } from "@sveltejs/kit";
import { NIGHT } from "#lib/server/rpc/rehearsal.js";
import { router } from "#lib/server/rpc/router.js";
import type { PageServerLoad } from "./$types";

const play = { started: 0 };

export const load: PageServerLoad = async ({ params, url }) => {
  const asked = dev ? url.searchParams.get("rehearsal") : null;
  const now = Date.now();
  if (asked === "play" && now - play.started > NIGHT * 30_000 + 300_000) {
    play.started = now;
  }
  const played = Math.floor((now - play.started) / 30_000) + 1;
  const shown: Record<string, number> = {
    play: played,
    night: NIGHT,
    final: NIGHT,
  };
  try {
    return {
      night: await call(router.live.get, {
        path: params.path,
        rehearsal:
          asked == null
            ? undefined
            : {
                started: asked === "play" ? play.started : null,
                shown: shown[asked] ?? Number(asked),
                isFinal:
                  asked === "final" || (asked === "play" && played > NIGHT + 1),
              },
      }),
      at: url.searchParams.get("at"),
    };
  } catch (error_) {
    if (error_ instanceof ORPCError && error_.code === "NOT_FOUND") {
      error(404);
    }
    throw error_;
  }
};
