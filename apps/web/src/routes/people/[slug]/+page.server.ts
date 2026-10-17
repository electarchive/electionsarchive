import { call } from "@orpc/server";
import { redirect } from "@sveltejs/kit";
import { person } from "#lib/server/pages.js";
import { router } from "#lib/server/rpc/router.js";
import type { PageServerLoad } from "./$types";

export const load: PageServerLoad = async ({ params }) => {
  const page = await person(params.slug);
  const { path } = await call(router.entities.path, { id: page.id });
  if (path != null && path !== `people/${params.slug}`) {
    redirect(301, `/${path}`);
  }
  return page;
};
