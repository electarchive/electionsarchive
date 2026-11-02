import { m } from "#lib/paraglide/messages.js";
import {
  feedMarkdown,
  formatOf,
  FORMATS,
  twinResponse,
} from "#lib/server/contrib-twins.js";
import {
  atomEntries,
  filtersFrom,
  recent,
  RecentFilters,
} from "#lib/server/feeds.js";
import type { RequestHandler } from "./$types";

export const GET: RequestHandler = async ({ params, url }) => {
  const format = formatOf(params.format, FORMATS);
  const found = await recent(filtersFrom(RecentFilters, url.searchParams));
  const title =
    found.polity == null
      ? m.recent_heading()
      : `${m.recent_heading()}: ${found.polity.name}`;
  return twinResponse({
    format,
    origin: url.origin,
    page: `/recent${url.search}`,
    title:
      format === "atom" && found.polity == null ? m.recent_feed_title() : title,
    subtitle: m.recent_lede(),
    markdown: () =>
      feedMarkdown(
        url.origin,
        title,
        m.recent_lede(),
        found.entries,
        found.next,
      ),
    json: () => found,
    entries: () => atomEntries(found.entries),
  });
};
