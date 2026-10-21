import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { error } from "@sveltejs/kit";
import { call } from "@orpc/server";
import { router } from "#lib/server/rpc/router.js";
import type { Actions, PageServerLoad } from "./$types";

const dev = import.meta.env.DEV;

export const load: PageServerLoad = async () => ({
  ...(await call(router.coverage.rows, {})),
  dev,
});

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
