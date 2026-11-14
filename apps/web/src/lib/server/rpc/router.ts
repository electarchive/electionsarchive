import type { Read } from "#lib/rpc-reads.js";
import { label } from "#lib/server/cache.js";
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
import { cycle } from "#lib/server/rpc/cycle.js";
import { recentChanges } from "#lib/server/rpc/feeds.js";
import { find } from "#lib/server/rpc/find.js";
import { histories } from "#lib/server/rpc/history.js";
import { indices } from "#lib/server/rpc/indices.js";
import { live } from "#lib/server/rpc/live.js";
import { maps } from "#lib/server/rpc/maps.js";
import { nation } from "#lib/server/rpc/nation.js";
import { places } from "#lib/server/rpc/places.js";
import { seats } from "#lib/server/rpc/seats.js";
import { tonight } from "#lib/server/rpc/tonight.js";

export const READ_ONLY_MESSAGE =
  "This preview of electionsarchive.com is read-only, so it doesn't accept changes yet.";

export const router = {
  bodies,
  contests,
  coverage,
  elections,
  entities,
  histories,
  indices,
  live: { ...live, cycle, find, nation },
  maps,
  parties,
  people,
  polities: { ...polities, places },
  recentChanges,
  search,
  seats,
  tonight,
} satisfies Record<Read, unknown>;

label(router);

export type Router = typeof router;
