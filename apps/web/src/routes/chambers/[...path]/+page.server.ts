import { part } from "svelte-islands/server";
import { chamber } from "#lib/server/pages.js";
import type { PageServerLoad } from "./$types";

export const load: PageServerLoad = async ({ params }) => {
  const { terms, members, ...page } = await chamber(params.path);
  return {
    ...page,
    members:
      members == null
        ? null
        : { ...members, members: part("chamber.members", members.members) },
    terms: terms.map(({ steps, ...term }) => term),
    steps: part(
      "chamber.steps",
      terms.map((term) => term.steps),
    ),
  };
};
