import { call } from "@orpc/server";
import { router } from "#lib/server/rpc/router.js";
import type { PageServerLoad } from "./$types";

export const load: PageServerLoad = async () => call(router.coverage.rows, {});
