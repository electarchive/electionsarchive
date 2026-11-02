import { stateLabel, tierLabel } from "#lib/contrib.js";
import { m } from "#lib/paraglide/messages.js";
import {
  formatOf,
  FORMATS,
  proposalsMarkdown,
  twinResponse,
} from "#lib/server/contrib-twins.js";
import {
  filtersFrom,
  ProposalFilters,
  proposalList,
} from "#lib/server/feeds.js";
import type { RequestHandler } from "./$types";

export const GET: RequestHandler = async ({ params, url }) => {
  const format = formatOf(params.format, FORMATS);
  const query = filtersFrom(ProposalFilters, url.searchParams);
  const found = await proposalList(query);
  const title = [
    m.proposals_heading(),
    query.state == null ? null : stateLabel[query.state](),
    query.tier == null ? null : tierLabel[Number(query.tier)]?.(),
    found.polity?.name,
    found.entity?.name,
    query.author == null ? null : `@${query.author}`,
  ]
    .filter((part) => part != null)
    .join(", ");
  return twinResponse({
    format,
    origin: url.origin,
    page: `/proposals${url.search}`,
    title,
    subtitle: m.proposals_lede(),
    markdown: () =>
      proposalsMarkdown(url.origin, title, found.proposals, found.next),
    json: () => found,
    entries: () =>
      found.proposals.map((item) => ({
        id: `proposal/${item.number}`,
        title: m.proposals_feed_entry({
          number: String(item.number),
          title: item.title,
          state: stateLabel[item.state](),
        }),
        updated: item.decided_at ?? item.submitted_at,
        author: item.author,
        path: `/proposals/${item.number}`,
      })),
  });
};
