import {
  contests,
  elections,
  parties,
  people,
  polities,
} from "#lib/server/rpc/archive.js";

export const router = { contests, elections, parties, people, polities };

export type Router = typeof router;
