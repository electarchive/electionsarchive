import { error } from "@sveltejs/kit";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { coverage } from "#lib/server/pages.js";
import type { Actions, PageServerLoad } from "./$types";

const isDev = import.meta.env.DEV;

export const load: PageServerLoad = async ({ params, url }) => ({
  ...(await coverage(params.cc, url.searchParams)),
  dev: isDev,
});

export const actions: Actions = {
  refresh: () => {
    if (!isDev) {
      error(404);
    }
    const pipelines = fileURLToPath(
      new URL("../../../../../../pipelines", import.meta.url),
    );
    spawn(
      `${pipelines}/.venv/bin/python`,
      ["-c", "from ea.cli import main; main()", "coverage", "refresh"],
      { cwd: pipelines, detached: true, stdio: "ignore" },
    ).unref();
    return { requested: true };
  },
};
