import { call } from "@orpc/server";
import { error } from "@sveltejs/kit";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { isGoal } from "#lib/coverage.js";
import { router } from "#lib/server/rpc/router.js";
import type { Actions, PageServerLoad } from "./$types";

const isDev = import.meta.env.DEV;

export const load: PageServerLoad = async ({ url }) => {
  const current = url.searchParams.get("current") === "1";
  const [goals, gaps] = await Promise.all([
    call(router.coverage.goals, { polity: "us", current }),
    call(router.coverage.gaps, { polity: "us", goal: "county", current }),
  ]);
  return {
    ...goals,
    goals: goals.goals.flatMap((row) =>
      isGoal(row.goal) ? [{ ...row, goal: row.goal }] : [],
    ),
    gaps,
    current,
    dev: isDev,
  };
};

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
