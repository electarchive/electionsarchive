import { error } from "@sveltejs/kit";
import {
  formatOf,
  proposalMarkdown,
  twinResponse,
} from "#lib/server/contrib-twins.js";
import { view } from "#lib/server/rpc/proposals.js";
import type { RequestHandler } from "./$types";

export const GET: RequestHandler = async ({ params, url }) => {
  const format = formatOf(params.format, ["md", "json"]);
  const number = Number(params.number);
  if (!Number.isSafeInteger(number) || number < 1) {
    error(404);
  }
  const found = await view(number, null);
  return twinResponse({
    format,
    origin: url.origin,
    page: `/proposals/${number}`,
    title: found.title,
    subtitle: "",
    markdown: () => proposalMarkdown(url.origin, found),
    json: () => found,
  });
};
