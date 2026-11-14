import { redirect } from "@sveltejs/kit";
import { latestCalver } from "#lib/server/downloads.js";
import type { RequestHandler } from "./$types";

export const GET: RequestHandler = async ({ setHeaders }) => {
  const calver = await latestCalver();
  setHeaders({ "cache-control": "public, max-age=300" });
  redirect(302, calver == null ? "/downloads" : `/downloads/${calver}`);
};
