import { error } from "@sveltejs/kit";
import { get } from "#lib/server/bounties.js";
import {
  bountyMarkdown,
  formatOf,
  twinResponse,
} from "#lib/server/contrib-twins.js";
import type { RequestHandler } from "./$types";

export const GET: RequestHandler = async ({ params, url }) => {
  const format = formatOf(params.format, ["md", "json"]);
  const number = Number(params.number);
  if (!Number.isSafeInteger(number) || number < 1) {
    error(404);
  }
  const found = await get(number);
  if (found == null) {
    error(404, "There is no bounty with that number.");
  }
  return twinResponse({
    format,
    origin: url.origin,
    page: `/bounties/${number}`,
    title: found.label,
    subtitle: "",
    markdown: () => bountyMarkdown(url.origin, found),
    json: () => found,
  });
};
