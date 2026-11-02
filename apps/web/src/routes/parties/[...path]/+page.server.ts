import { party } from "#lib/server/pages.js";
import { talkLink } from "#lib/server/talk.js";
import type { PageServerLoad } from "./$types";

export const load: PageServerLoad = async ({ params, url }) => {
  const page = await party(params.path, url.searchParams.get("in"));
  return { ...page, talk: await talkLink(page.id) };
};
