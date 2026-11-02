import { error, redirect } from "@sveltejs/kit";
import { contributor, HANDLE } from "#lib/server/contributors.js";
import { watching } from "#lib/server/feeds.js";
import type { PageServerLoad } from "./$types";

export const load: PageServerLoad = async ({ params, locals, setHeaders }) => {
  const handle = params.handle.toLowerCase();
  if (!HANDLE.test(handle)) {
    error(404, "No contributor has that handle.");
  }
  if (handle !== params.handle) {
    redirect(301, `/contributors/${handle}`);
  }
  setHeaders({ "cache-control": "no-store" });
  const { userId, ...found } = await contributor(handle);
  const mine = locals.user?.id === userId;
  return {
    ...found,
    mine,
    watch:
      locals.user == null || mine
        ? null
        : {
            id: userId,
            watching: await watching(locals.user.id, "user", userId),
          },
  };
};
