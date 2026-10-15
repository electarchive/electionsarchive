import { call } from "@orpc/server";
import { router } from "#lib/server/rpc/router.js";
import type { PageServerLoad } from "./$types";

export const load: PageServerLoad = async () => {
  const polities = await call(router.polities.list, undefined);
  return {
    polities: polities.filter(
      (polity) =>
        polity.kind === "sovereign_state" || polity.kind === "supranational",
    ),
  };
};
