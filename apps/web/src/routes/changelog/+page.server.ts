import { changelog } from "#lib/server/site.js";
import type { PageServerLoad } from "./$types";

export const load: PageServerLoad = async () => ({
  versions: await changelog(),
});
