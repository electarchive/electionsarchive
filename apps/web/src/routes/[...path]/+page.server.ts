import { archive, call } from "#lib/server/pages.js";
import { router } from "#lib/server/rpc/router.js";
import { talkLink } from "#lib/server/talk.js";
import type { PageServerLoad } from "./$types";

async function seed(path: string) {
  const located = await call(router.elections.locate, { path });
  const [hub, hexes] = await Promise.all([
    call(router.elections.get, { id: located.id }),
    call(router.seats.hexes, { id: located.id }),
  ]);
  return { path, located, hub, hexes };
}

async function soon<T>(pending: Promise<T | null>) {
  const streamed = Promise.race([
    pending,
    new Promise<null>((done) => setTimeout(done, 30_000, null)),
  ]);
  return Promise.race([
    pending.then((value) => ({ value })),
    new Promise<{ value: typeof streamed }>((done) =>
      setTimeout(done, 150, { value: streamed }),
    ),
  ]);
}

export const load: PageServerLoad = async ({ params, url }) => {
  const data = await archive(params.path, url.searchParams);
  if (data.kind === "contest") {
    return { ...data, talk: await talkLink(data.id) };
  }
  if (data.kind !== "polity") {
    return data;
  }
  const { polities } = data.polity;
  const path = ["sovereign_state", "supranational"].includes(data.polity.kind)
    ? data.polity.latest?.path
    : null;
  const [{ value: scrubber }, { value: record }] = await Promise.all([
    path == null ? { value: null } : soon(seed(path).catch(() => null)),
    soon(call(router.polities.record, { id: data.id }).catch(() => null)),
  ]);
  const groups = Map.groupBy(polities, (child) => child.kind);
  const places = {
    total: polities.length,
    sizes: Object.fromEntries(
      groups.entries().map(([kind, rows]) => [kind, rows.length]),
    ),
    isPartial: polities.length > 200,
  };
  if (!places.isPartial) {
    return { ...data, polity: { ...data.polity, places, scrubber, record } };
  }
  const kept = new Set([
    ...groups.values().flatMap((rows) => rows.slice(0, 20)),
    ...polities
      .filter(
        (child) => child.kind === "municipality" && child.population != null,
      )
      .toSorted((a, b) => (b.population ?? 0) - (a.population ?? 0))
      .slice(0, 10),
  ]);
  return {
    ...data,
    polity: {
      ...data.polity,
      polities: polities.filter((child) => kept.has(child)),
      places,
      scrubber,
      record,
    },
  };
};
