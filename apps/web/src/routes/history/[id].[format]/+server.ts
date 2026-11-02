import { error, redirect } from "@sveltejs/kit";
import * as v from "valibot";
import { m } from "#lib/paraglide/messages.js";
import {
  formatOf,
  FORMATS,
  historyEntries,
  historyMarkdown,
  twinResponse,
} from "#lib/server/contrib-twins.js";
import { Before, history } from "#lib/server/history.js";
import { PublicId } from "#lib/server/talk.js";
import type { RequestHandler } from "./$types";

export const GET: RequestHandler = async ({ params, url }) => {
  const format = formatOf(params.format, FORMATS);
  const id = v.safeParse(PublicId, params.id);
  if (!id.success) {
    error(404);
  }
  const before = url.searchParams.get("before");
  const found = await history(
    id.output,
    before != null && v.is(Before, before) ? before : null,
  );
  if (found.entity.public_id !== params.id) {
    redirect(301, `/history/${found.entity.public_id}.${format}${url.search}`);
  }
  return twinResponse({
    format,
    origin: url.origin,
    page: `/history/${found.entity.public_id}${url.search}`,
    title:
      format === "atom"
        ? m.history_feed_title({ name: found.entity.name })
        : m.history_page_title({ name: found.entity.name }),
    subtitle: m.history_lede(),
    markdown: () =>
      historyMarkdown(url.origin, found.entity, found.entries, found.next),
    json: () => found,
    entries: () => historyEntries(found.entity.public_id, found.entries),
  });
};
