import { agents } from "#lib/server/rpc/agents.js";
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
import { proposals } from "#lib/server/rpc/proposals.js";
import { seats } from "#lib/server/rpc/seats.js";
import { uploads } from "#lib/server/rpc/uploads.js";

export const router = {
  admin,
  agents,
  bodies,
  contests,
  coverage,
  elections,
  entities,
  live,
  parties,
  people,
  polities,
  proposals,
  search,
  seats,
  uploads,
};

export type Router = typeof router;
