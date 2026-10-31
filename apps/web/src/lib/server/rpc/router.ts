import { READ_ONLY } from "$app/env/public";
import { ORPCError, os } from "@orpc/server";
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

export const READ_ONLY_MESSAGE =
  "This preview of electionsarchive.com is read-only, so it doesn't accept changes yet.";

export const contributions = { admin, agents, proposals, uploads };

export const reads = {
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

export const router = { ...reads, ...contributions };

export type Router = typeof router;

const closed = os.$context<{ headers: Headers }>().use(() => {
  throw new ORPCError("FORBIDDEN", { message: READ_ONLY_MESSAGE });
});

export const served = READ_ONLY
  ? {
      ...reads,
      admin: closed.router(admin),
      agents: closed.router(agents),
      proposals: closed.router(proposals),
      uploads: closed.router(uploads),
    }
  : router;
