import { error, redirect } from "@sveltejs/kit";
import { githubUrl } from "#lib/downloads.js";
import { hasFile, latestCalver } from "#lib/server/downloads.js";
import type { RequestHandler } from "./$types";

export const GET: RequestHandler = async ({ params, setHeaders }) => {
  const calver = await latestCalver();
  if (calver == null || !(await hasFile(calver, params.file))) {
    error(404);
  }
  setHeaders({ "cache-control": "public, max-age=300" });
  redirect(302, githubUrl(calver, params.file), {
    external: ["https://github.com"],
  });
};
