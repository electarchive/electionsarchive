import { chamber } from "#lib/server/pages.js";
import { memo, part } from "#lib/server/parts.js";
import { fold } from "#lib/steps.js";
import type { PageServerLoad } from "./$types";

const shape = memo(
  ({
    terms,
    members,
    logos,
    ...page
  }: Awaited<ReturnType<typeof chamber>>) => ({
    ...page,
    members:
      members == null
        ? null
        : { ...members, members: part("chamber.members", members.members) },
    terms: terms.map(({ steps, ...term }) => term),
    steps: part(
      "chamber.steps",
      terms.map((term) => fold(term.steps)),
      { fetch: "preload" },
    ),
    logos: part("chamber.logos", logos),
  }),
);

export const load: PageServerLoad = async ({ params }) => ({
  ...shape(await chamber(params.path)),
});
