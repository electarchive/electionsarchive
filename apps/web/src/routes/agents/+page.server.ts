import { READ_ONLY } from "$app/env/public";
import { tools } from "#lib/server/mcp.js";
import type { PageServerLoad } from "./$types";

export const load: PageServerLoad = ({ url }) => ({
  origin: url.origin,
  tools: READ_ONLY ? tools.filter((tool) => !tool.isKeyed) : tools,
});
