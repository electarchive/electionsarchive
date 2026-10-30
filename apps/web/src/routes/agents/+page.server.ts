import { tools } from "#lib/server/mcp.js";
import { WRITES } from "#lib/server/rpc/keyed.js";
import type { PageServerLoad } from "./$types";

export const load: PageServerLoad = ({ url }) => ({
  origin: url.origin,
  tools,
  writes: WRITES,
});
