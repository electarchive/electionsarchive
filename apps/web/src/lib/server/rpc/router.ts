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
};

export type Router = typeof router;
