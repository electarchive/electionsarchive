import {
  bodies,
  contests,
  elections,
  entities,
  parties,
  people,
  polities,
  search,
} from "#lib/server/rpc/archive.js";
import { coverage } from "#lib/server/rpc/coverage.js";
import { admin } from "#lib/server/rpc/desk.js";
import { live } from "#lib/server/rpc/live.js";
import { seats } from "#lib/server/rpc/seats.js";

export const router = {
  admin,
  bodies,
  contests,
  coverage,
  elections,
  entities,
  live,
  parties,
  people,
  polities,
  search,
  seats,
};

export type Router = typeof router;
