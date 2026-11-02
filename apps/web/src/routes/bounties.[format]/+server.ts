import { m } from "#lib/paraglide/messages.js";
import { filters, list } from "#lib/server/bounties.js";
import {
  bountiesMarkdown,
  formatOf,
  twinResponse,
} from "#lib/server/contrib-twins.js";
import type { RequestHandler } from "./$types";

export const GET: RequestHandler = async ({ params, url }) => {
  const format = formatOf(params.format, ["md", "json"]);
  const found = await list(filters(url.searchParams));
  return twinResponse({
    format,
    origin: url.origin,
    page: `/bounties${url.search}`,
    title: m.bounties_heading(),
    subtitle: m.bounties_lede(),
    markdown: () => bountiesMarkdown(url.origin, found.bounties, found.next),
    json: () => found,
  });
};
