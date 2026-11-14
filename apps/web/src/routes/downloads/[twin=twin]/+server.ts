import { downloadsTwin } from "#lib/server/downloads.js";
import type { RequestHandler } from "./$types";

export const GET: RequestHandler = async ({ params, url }) => {
  const dot = params.twin.lastIndexOf(".");
  return downloadsTwin(
    params.twin.slice(dot + 1) === "md" ? "md" : "json",
    url.origin,
    params.twin.slice(0, dot),
  );
};
