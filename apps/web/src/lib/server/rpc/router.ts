import { contests, elections, polities } from "#lib/server/rpc/archive.js";

export const router = { contests, elections, polities };

export type Router = typeof router;
