import { contributors, HANDLE } from "#lib/server/contributors.js";
import type { PageServerLoad } from "./$types";

export const load: PageServerLoad = async ({ url, setHeaders }) => {
  setHeaders({ "cache-control": "no-store" });
  const q = url.searchParams.get("q")?.trim().slice(0, 60) ?? "";
  const after = url.searchParams.get("after");
  return {
    q,
    ...(await contributors({
      q: q === "" ? null : q,
      after: after != null && HANDLE.test(after) ? after : null,
    })),
  };
};
