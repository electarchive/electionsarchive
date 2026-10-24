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
import { seats } from "#lib/server/rpc/seats.js";

export const router = {
  bodies,
  contests,
  coverage,
  elections,
  entities,
  parties,
  people,
  polities,
  search,
  seats,
};

export type Router = typeof router;
