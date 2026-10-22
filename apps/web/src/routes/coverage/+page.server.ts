import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { error } from "@sveltejs/kit";
import { call } from "@orpc/server";
import { sql } from "drizzle-orm";
import { db } from "#lib/server/db/index.js";
import { router } from "#lib/server/rpc/router.js";
import type { Actions, PageServerLoad } from "./$types";

const dev = import.meta.env.DEV;

export const load: PageServerLoad = async () => {
  const building = await db.execute<{ building: boolean }>(sql`
    select built_at is null or started_at > built_at as building from coverage_build
  `);
  return {
    ...(await call(router.coverage.rows, {})),
    dev,
    building: building.rows[0]?.building ?? false,
  };
};

export const actions: Actions = {
  refresh: () => {
    if (!dev) {
      error(404);
    }
    const pipelines = fileURLToPath(new URL("../../../../../pipelines", import.meta.url));
    spawn(`${pipelines}/.venv/bin/python`, ["-c", "from ea.cli import main; main()", "coverage", "refresh"], {
      cwd: pipelines,
      detached: true,
      stdio: "ignore",
    }).unref();
    return { requested: true };
  },
};
