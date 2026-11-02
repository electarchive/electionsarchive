import { error } from "@sveltejs/kit";
import { sql } from "drizzle-orm";
import { m } from "#lib/paraglide/messages.js";
import { atom } from "#lib/server/atom.js";
import {
  activityEntries,
  contributor,
  HANDLE,
} from "#lib/server/contributors.js";
import type { RequestHandler } from "./$types";

export const GET: RequestHandler = async ({ params, url }) => {
  if (!HANDLE.test(params.handle)) {
    error(404, "No contributor has that handle.");
  }
  const { userId, profile } = await contributor(params.handle);
  const name = profile.display_name ?? profile.handle;
  return atom({
    origin: url.origin,
    self: `/contributors/${profile.handle}.atom`,
    page: `/contributors/${profile.handle}`,
    title: m.contributor_feed_title({ name }),
    subtitle: m.contributor_feed_subtitle({ handle: profile.handle }),
    entries: await activityEntries(
      sql`a.actor_user_id = ${userId}::uuid or (p.author_user_id = ${userId}::uuid and a.kind in ('changes_requested', 'proposal_rejected', 'proposal_published', 'proposal_reverted'))`,
    ),
    fallback: profile.member_since,
  });
};
