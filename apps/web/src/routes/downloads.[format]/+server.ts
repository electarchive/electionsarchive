import { formatOf } from "#lib/server/contrib-twins.js";
import { downloadsTwin } from "#lib/server/downloads.js";
import type { RequestHandler } from "./$types";

export const GET: RequestHandler = async ({ params, url }) =>
  downloadsTwin(formatOf(params.format, ["md", "json"]), url.origin, null);
