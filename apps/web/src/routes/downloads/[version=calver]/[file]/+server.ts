import { error, redirect } from "@sveltejs/kit";
import { githubUrl } from "#lib/downloads.js";
import { hasFile } from "#lib/server/downloads.js";
import type { RequestHandler } from "./$types";

export const GET: RequestHandler = async ({ params, setHeaders }) => {
  if (!(await hasFile(params.version, params.file))) {
    error(404);
  }
  setHeaders({ "cache-control": "public, max-age=86400" });
  redirect(302, githubUrl(params.version, params.file), {
    external: ["https://github.com"],
  });
};
