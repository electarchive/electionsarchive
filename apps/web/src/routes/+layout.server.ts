import { READ_ONLY } from "$app/env/public";
import { unreadCount } from "#lib/server/notifications.js";
import { site } from "#lib/server/site.js";
import type { LayoutServerLoad } from "./$types";

export const load: LayoutServerLoad = async ({ locals, depends }) => {
  depends("ea:unread");
  const [unread, stamp] = await Promise.all([
    locals.user == null || READ_ONLY ? 0 : unreadCount(locals.user.id),
    site(),
  ]);
  return {
    user:
      locals.user == null
        ? null
        : { name: locals.user.name, email: locals.user.email },
    unread,
    site: stamp,
  };
};
