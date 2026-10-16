import {
  contests,
  elections,
  entities,
  parties,
  people,
  polities,
  search,
} from "#lib/server/rpc/archive.js";

export const router = {
  contests,
  elections,
  entities,
  parties,
  people,
  polities,
  search,
};

export type Router = typeof router;
