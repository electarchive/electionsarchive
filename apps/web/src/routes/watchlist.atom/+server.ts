import { error } from "@sveltejs/kit";
import { m } from "#lib/paraglide/messages.js";
import { atom } from "#lib/server/atom.js";
import { atomEntries, tokenOwner, watchlist } from "#lib/server/feeds.js";
import type { RequestHandler } from "./$types";

export const GET: RequestHandler = async ({ url }) => {
  const owner = await tokenOwner(url.searchParams.get("token") ?? "");
  if (owner == null) {
    error(404, "No watchlist has that feed link.");
  }
  const found = await watchlist(owner.user_id, null);
  const merged = new Map(
    [...found.mine, ...found.entries].map((entry) => [entry.id, entry]),
  );
  const response = atom({
    origin: url.origin,
    self: `/watchlist.atom?token=${url.searchParams.get("token") ?? ""}`,
    page: "/watchlist",
    title: m.watchlist_feed_title({ handle: owner.handle }),
    subtitle: m.watchlist_lede(),
    entries: atomEntries(
      [...merged.values()].toSorted((a, b) => b.at.localeCompare(a.at)),
    ),
    fallback: new Date().toISOString(),
  });
  response.headers.set("cache-control", "private, no-store");
  return response;
};
