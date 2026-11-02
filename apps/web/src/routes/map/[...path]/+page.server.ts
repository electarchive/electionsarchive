import { shell } from "#lib/atlas/data.js";
import { atlasPage } from "#lib/server/pages.js";
import type { PageServerLoad } from "./$types";

export const load: PageServerLoad = async ({ params, url }) => {
  const search = new URLSearchParams(url.searchParams);
  search.delete("level");
  const { contest, whole, path, atlas } = await atlasPage(params.path, search);
  return { contest, whole, path, shell: shell(atlas) };
};
