import { READ_ONLY } from "$app/env/public";
import { ORPCError, os } from "@orpc/server";
import { label } from "#lib/server/cache.js";
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
import { bounties, claims } from "#lib/server/rpc/bounties.js";
import { contributors } from "#lib/server/rpc/contributors.js";
import { coverage } from "#lib/server/rpc/coverage.js";
import { admin } from "#lib/server/rpc/desk.js";
import { recentChanges, watches } from "#lib/server/rpc/feeds.js";
import { histories } from "#lib/server/rpc/history.js";
import { indices } from "#lib/server/rpc/indices.js";
import { live } from "#lib/server/rpc/live.js";
import { maps } from "#lib/server/rpc/maps.js";
import { places } from "#lib/server/rpc/places.js";
import { proposals } from "#lib/server/rpc/proposals.js";
import { seats } from "#lib/server/rpc/seats.js";
import { talk } from "#lib/server/rpc/talk.js";
import { tonight } from "#lib/server/rpc/tonight.js";
import { uploads } from "#lib/server/rpc/uploads.js";

export const READ_ONLY_MESSAGE =
  "This preview of electionsarchive.com is read-only, so it doesn't accept changes yet.";

export const contributions = {
  admin,
  agents,
  claims,
  contributors,
  proposals,
  talk,
  uploads,
  watches,
};

export const reads = {
  bodies,
  bounties,
  contests,
  coverage,
  elections,
  entities,
  histories,
  indices,
  live,
  maps,
  parties,
  people,
  polities: { ...polities, places },
  recentChanges,
  search,
  seats,
  tonight,
};

export const router = { ...reads, ...contributions };

label(router);

export type Router = typeof router;

const closed = os.$context<{ headers: Headers }>().use(() => {
  throw new ORPCError("FORBIDDEN", { message: READ_ONLY_MESSAGE });
});

export const served = READ_ONLY
  ? {
      ...reads,
      admin: closed.router(admin),
      agents: closed.router(agents),
      claims: closed.router(claims),
      contributors: closed.router(contributors),
      proposals: closed.router(proposals),
      talk: closed.router(talk),
      uploads: closed.router(uploads),
      watches: closed.router(watches),
    }
  : router;
