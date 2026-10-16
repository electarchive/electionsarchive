import { comparison } from "#lib/server/pages.js";
import type { PageServerLoad } from "./$types";

export const load: PageServerLoad = async ({ url }) =>
  comparison(
    [url.searchParams.get("a"), url.searchParams.get("b")].filter(
      (id) => id != null,
    ),
  );
