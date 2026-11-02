import { READ_ONLY } from "$app/env/public";
import { unreadCount } from "#lib/server/notifications.js";
import type { LayoutServerLoad } from "./$types";

export const load: LayoutServerLoad = async ({ locals, depends }) => {
  depends("ea:unread");
  return {
    user:
      locals.user == null
        ? null
        : { name: locals.user.name, email: locals.user.email },
    unread:
      locals.user == null || READ_ONLY ? 0 : await unreadCount(locals.user.id),
  };
};
