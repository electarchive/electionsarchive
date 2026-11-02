import { error, redirect } from "@sveltejs/kit";
import * as v from "valibot";
import { m } from "#lib/paraglide/messages.js";
import {
  changeMarkdown,
  formatOf,
  twinResponse,
} from "#lib/server/contrib-twins.js";
import { change } from "#lib/server/history.js";
import { PublicId } from "#lib/server/talk.js";
import type { RequestHandler } from "./$types";

export const GET: RequestHandler = async ({ params, url }) => {
  const format = formatOf(params.format, ["md", "json"]);
  const id = v.safeParse(PublicId, params.id);
  if (!id.success || !v.is(v.pipe(v.string(), v.uuid()), params.changeset)) {
    error(404);
  }
  const found = await change(id.output, params.changeset);
  if (found.entity.public_id !== params.id) {
    redirect(
      301,
      `/history/${found.entity.public_id}/${params.changeset}.${format}`,
    );
  }
  return twinResponse({
    format,
    origin: url.origin,
    page: `/history/${found.entity.public_id}/${params.changeset}`,
    title: m.history_change_title({
      summary: found.entry.title ?? found.entry.summary,
      name: found.entity.name,
    }),
    subtitle: m.history_lede(),
    markdown: () => changeMarkdown(url.origin, found),
    json: () => found,
  });
};
