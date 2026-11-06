import type { Fetch, Part } from "svelte-islands";
import { peek } from "svelte-islands";
import { stream } from "svelte-islands/server";
import type { Primed } from "#lib/primed.js";
import { shapesOf } from "#lib/map/hexes.js";
import { orpc } from "#lib/rpc.js";
import { isCrawler } from "#lib/server/guard.js";
import { archive, call, capped, soon } from "#lib/server/pages.js";
import { memo, part, weigh } from "#lib/server/parts.js";
import { router } from "#lib/server/rpc/router.js";
import { talkLink } from "#lib/server/talk.js";
import type { PageServerLoad } from "./$types";
import { isLocalOf, sourceOf, upOf, UPPER } from "./polity-source.js";
import { tallied } from "./tally.js";

const BOARD = 49;
const FLOOR = 12;
const WEIGHT = 64_000;
const QUESTIONS = 7;
const PULL = { executive: 4, general: 1, special: 0.25 };
const CROWD = 40;
const TILED = /^us\/[a-z]{2}\/[^/]+$/u;
const LEGEND = 8;
const FOLD = 18;
const FOLDED = 20;
const ROSTER = 24_000;
const ROUND_CELLS = 120;
const PRIMING = 400;

type Election = Awaited<
  Extract<Awaited<ReturnType<typeof archive>>, { kind: "election" }>["body"]
>;
type Contest = Awaited<
  Extract<Awaited<ReturnType<typeof archive>>, { kind: "contest" }>["body"]
>["contest"];

function parted<T>(
  name: string,
  value: T | Promise<T>,
  fetch?: Fetch,
): Part<NonNullable<T>> | Promise<Part<NonNullable<T>> | null> | null {
  const wrap = (resolved: T) =>
    resolved == null ? null : part(name, resolved, { fetch });
  return value instanceof Promise ? value.then(wrap) : wrap(value);
}

const compared = memo((targets: Contest["targets"]) => {
  const others = targets.filter((target) => !target.series);
  return {
    near: targets.filter((target) => target.series || target.previous),
    others:
      others.length === 0
        ? null
        : part("contest.targets", others, { fetch: "lazy" }),
  };
});

const seated = memo((rows: NonNullable<Contest["places"]>["rows"]) =>
  weigh(rows) > ROSTER
    ? part("contest.places", rows, { fetch: "preload" })
    : null,
);

function isQuestion(race: { kind: string }) {
  return race.kind === "ballot_question";
}

const lay = memo((races: Election["election"]["races"]) => {
  const isDeferred = races.some((race) => race.deferred != null);
  const shaped = races.map((race) => {
    const loose = new Set(
      race.results.flatMap((row) => (row.party == null ? [row.key] : [])),
    );
    const staff =
      race.holders == null ? null : part("election.holders", race.holders);
    const wards = race.wards ?? [];
    const isFolded = wards.length > FOLDED;
    const staffed = {
      ...race,
      shapes: [] as ReturnType<typeof shapesOf>,
      wards: isFolded
        ? wards.map((row, at) =>
            at < FOLD ? row : { ...row, label: "", field: [] },
          )
        : race.wards,
      folded: isFolded
        ? part("election.wards", {
            at: FOLD,
            rows: wards
              .slice(FOLD)
              .map(({ label, field }) => ({ label, field })),
          })
        : null,
      holders:
        race.holders == null || staff?.isInline !== false
          ? race.holders
          : {
              places: race.holders.places.slice(0, 0),
              seats: Object.fromEntries(
                Object.entries(race.holders.seats).filter(([key]) =>
                  loose.has(key),
                ),
              ),
            },
      staff: staff?.isInline === false ? staff : null,
    };
    if (race.places == null) {
      return { ...staffed, places: null };
    }
    const { legend, rows } = race.places;
    const tally = tallied(race.places);
    const named = Math.max(
      tally.leads.findLastIndex((leads) => leads > 0) + 1,
      LEGEND,
    );
    return {
      ...staffed,
      places: {
        ...race.places,
        legend: legend.slice(0, named),
        rows: part("election.places", {
          rows,
          legend: legend.length > named ? legend : null,
        }),
        tally,
      },
    };
  });
  const ordered = isDeferred
    ? Map.groupBy(shaped, (race) => race.scope)
        .values()
        .flatMap((group) => [
          ...group.filter((race) => race.results.length !== 1),
          ...group.filter((race) => race.results.length === 1),
        ])
        .toArray()
    : shaped;
  const pick = (rows: typeof ordered) => {
    const picked: typeof ordered = [];
    let total = 0;
    for (const race of rows) {
      total += weigh(race);
      if (
        picked.length >= BOARD ||
        (picked.length >= FLOOR && total > WEIGHT)
      ) {
        break;
      }
      picked.push(race);
    }
    return picked;
  };
  const pull = (race: (typeof ordered)[number]) =>
    (race.valid ?? 0) * PULL[race.level];
  const ranked = ordered.toSorted(
    (a, b) =>
      Number(b.results.length !== 1) - Number(a.results.length !== 1) ||
      pull(b) - pull(a),
  );
  const board = new Set(
    isDeferred
      ? [
          ...pick(ranked.filter((race) => !isQuestion(race))),
          ...ranked.filter((race) => isQuestion(race)).slice(0, QUESTIONS),
        ]
      : pick(ordered),
  );
  return { isDeferred, shaped, ordered, board };
});

const split = memo((laid: ReturnType<typeof lay>, asked: string) => {
  const { shaped, ordered, board } = laid;
  const kept = new Set([
    ...ordered.filter((race) => race.anchor === asked),
    ...board,
  ]);
  if (kept.size === ordered.length) {
    return { races: shaped, rest: null };
  }
  const left = ordered.filter((race) => !kept.has(race));
  return {
    races: ordered.filter((race) => kept.has(race)),
    rest: {
      races: left.filter((race) => !isQuestion(race)).length,
      questions: left.filter((race) => isQuestion(race)).length,
      part: part("election.races", {
        at: ordered.flatMap((race, at) => (kept.has(race) ? [] : [at])),
        races: left,
      }),
    },
  };
});

const unplace = memo(
  (
    races: Election["election"]["races"],
    contests: Election["election"]["contests"],
  ) => {
    const placed = new Set(
      races.flatMap((race) => [
        ...(race.places?.rows.flatMap((row) => row.path ?? []) ?? []),
        ...(race.path == null ? [] : [race.path]),
      ]),
    );
    return contests.flatMap((contest) =>
      contest.path == null || placed.has(contest.path)
        ? []
        : [{ name: contest.name, path: contest.path }],
    );
  },
);

const crowd = memo(
  (
    elections: Election["election"]["elections"],
    ledger: Election["ledger"],
  ) => {
    const rounds = new Set(
      ledger?.kind === "rounds"
        ? ledger.rounds.flatMap((round) => round.path ?? [])
        : [],
    );
    const children = elections.filter(
      (child) => !child.isStage && !rounds.has(child.path ?? ""),
    );
    const isCrowded =
      children.length >= CROWD &&
      children.some((child) => !TILED.test(child.path ?? ""));
    return {
      isCrowded,
      elections: isCrowded
        ? elections.filter((child) => child.isStage)
        : elections,
      children: isCrowded ? children : null,
    };
  },
);

const scrub = memo(
  (
    located: Awaited<ReturnType<typeof seed>>["located"],
    hub: Awaited<ReturnType<typeof seed>>["hub"],
    hexes: Awaited<ReturnType<typeof seed>>["hexes"],
    path: string,
  ) => ({ path, located, hub, hexes }),
);

async function within<T>(promise: Promise<Array<T>>) {
  return Promise.race([
    promise,
    new Promise<Array<T>>((done) => {
      setTimeout(done, PRIMING, []);
    }),
  ]);
}

async function drawn(polity: Parameters<typeof sourceOf>[0]) {
  const source = sourceOf(polity);
  if (source == null) {
    return [];
  }
  const up = upOf(source.unit);
  const place = async (unit: string | undefined, isWider: boolean) => {
    const input = { id: source.id, unit };
    const contest = await call(router.contests.get, input);
    if (isWider && contest.places == null) {
      return [{ key: orpc.contests.get.queryKey({ input }), data: contest }];
    }
    const bounded = { ...input, level: contest.places?.level };
    const bounds = await call(router.contests.boundaries, bounded);
    return [
      { key: orpc.contests.get.queryKey({ input }), data: contest },
      {
        key: orpc.contests.boundaries.queryKey({ input: bounded }),
        data: bounds,
      },
    ];
  };
  const found = await Promise.all([
    place(source.unit, false),
    source.unit == null ? [] : place(up, true),
  ]);
  return found.flat();
}

async function reads(id: string, polity: Parameters<typeof sourceOf>[0]) {
  const found = await Promise.all([
    within(drawn(polity).catch(() => [])),
    UPPER.includes(polity.kind)
      ? []
      : within(
          call(router.polities.holders, { id })
            .then((data) => [
              { key: orpc.polities.holders.queryKey({ input: { id } }), data },
            ])
            .catch(() => []),
        ),
    isLocalOf(polity)
      ? within(
          (async () => {
            const input = { a: id, b: polity.parent ?? id };
            const data = await call(router.polities.compare, input);
            return [{ key: orpc.polities.compare.queryKey({ input }), data }];
          })().catch(() => []),
        )
      : [],
  ]);
  return found.flat();
}

async function contested(
  id: string,
  unit: string,
  match: string,
  search: URLSearchParams,
  contest: Contest,
): Promise<Primed> {
  const { places } = contest;
  const isLedgered =
    places != null &&
    !places.level.startsWith("unit:") &&
    !(places.rows.length > 0 && places.rows.every((row) => row.valid === 0));
  const ledger = {
    id,
    unit: unit === "" ? undefined : unit,
    level: places?.level ?? undefined,
    match: match === "person" || match === "family" ? match : "party",
    group: search.get("group") ?? undefined,
    round: Math.max(0, Math.trunc(Number(search.get("round"))) || 0),
  } as const;
  const found = await Promise.all([
    within(
      call(router.coverage.known, { id })
        .then((data) => [
          { key: orpc.coverage.known.queryKey({ input: { id } }), data },
        ])
        .catch(() => []),
    ),
    isLedgered
      ? within(
          call(router.contests.ledger, ledger)
            .then((data) => [
              { key: orpc.contests.ledger.queryKey({ input: ledger }), data },
            ])
            .catch(() => []),
        )
      : [],
  ]);
  return found.flat();
}

async function seed(path: string) {
  const located = await call(router.elections.locate, { path });
  const [hub, hexes] = await Promise.all([
    call(router.elections.get, { id: located.id }),
    call(router.seats.hexes, { id: located.id }),
  ]);
  return { path, located, hub, hexes };
}

export const load: PageServerLoad = async ({
  params,
  url,
  request,
  setHeaders,
  isDataRequest,
  locals,
}) => {
  const data = await archive(params.path, url.searchParams);
  locals.view = data.kind;
  const budget = isCrawler(request.headers.get("user-agent"))
    ? Infinity
    : isDataRequest
      ? 0
      : 150;
  if (data.kind === "contest") {
    const body = data.body.then(({ contest, ...rest }) => {
      const { near, others } = compared(contest.targets);
      const chosen = contest.targets.find(
        (target) =>
          !target.series &&
          !target.previous &&
          target.value === contest.target?.path,
      );
      const { places, count } = contest;
      const roster = places == null ? null : seated(places.rows);
      return {
        ...rest,
        roster,
        contest: {
          ...contest,
          places:
            places == null || roster == null
              ? places
              : { ...places, rows: places.rows.slice(0, 0) },
          targets: chosen == null ? near : [...near, chosen],
          others,
          table:
            count != null && count.rows.length * count.last >= ROUND_CELLS
              ? part("contest.rounds", { id: data.id, round: count.round })
              : null,
        },
      };
    });
    const primed =
      budget === Infinity
        ? null
        : parted(
            "contest.primed",
            data.body
              .then(async ({ contest }) =>
                contested(
                  data.id,
                  data.unit,
                  data.match,
                  url.searchParams,
                  contest,
                ),
              )
              .then((found) => (found.length === 0 ? null : found))
              .catch(() => null),
            "preload",
          );
    const [streamed, talk] = await Promise.all([
      stream(
        {
          body,
          primed,
          ballot: data.ballot,
          standing: data.standing,
          crowned: data.crowned,
        },
        { budget },
      ),
      talkLink(data.id),
    ]);
    if (Object.values(streamed).some((value) => value instanceof Promise)) {
      setHeaders({ "cache-control": "private, no-cache" });
    }
    return { ...data, ...streamed, talk };
  }
  if (data.kind === "election") {
    const asked = params.path
      .slice(data.path.length + 1)
      .replace(/-total$/u, "");
    const body = data.body.then(async (opened) => {
      const { races, contests, elections } = opened.election;
      const laid = lay(races);
      const { races: boarded, rest } = split(
        laid,
        laid.ordered.some(
          (race) => race.anchor === asked && !laid.board.has(race),
        )
          ? asked
          : "",
      );
      const { value: hexes } = await soon(
        boarded.some((race) => race.map != null && race.places != null)
          ? call(router.seats.hexes, {
              id: data.id,
              within: data.scope?.id,
            }).catch(() => null)
          : Promise.resolve(null),
      );
      const crowded = crowd(elections, opened.ledger);
      return {
        ...opened,
        election: {
          ...opened.election,
          elections: crowded.elections,
          children:
            crowded.children == null
              ? null
              : part("election.children", crowded.children),
          contests:
            contests.length === 0 ? null : part("election.contests", contests),
          logos: part("election.logos", opened.election.logos),
          isCrowded: crowded.isCrowded,
          unplaced: laid.isDeferred ? [] : unplace(races, contests),
          races:
            hexes == null || hexes instanceof Promise
              ? boarded
              : boarded.map((race) => {
                  if (race.places == null) {
                    return race;
                  }
                  const rows = peek(race.places.rows)?.value.rows ?? [];
                  const keys = new Set(
                    [...rows, ...(race.places.districts ?? [])].map(
                      (row) => row.k,
                    ),
                  );
                  return {
                    ...race,
                    shapes: shapesOf(hexes, {
                      has: (k) => keys.has(k),
                      size: rows.length,
                    }),
                  };
                }),
          rest,
        },
      };
    });
    const streamed = await stream(
      { body },
      { budget: data.race == null ? budget : Infinity },
    );
    if (streamed.body instanceof Promise) {
      setHeaders({ "cache-control": "private, no-cache" });
    }
    const { sections } = data;
    const { winners } = sections;
    const isSweep =
      sections.mayoral ||
      (!(winners instanceof Promise) && (winners?.tiers.length ?? 0) > 0);
    return {
      ...data,
      ...streamed,
      sections: {
        ...sections,
        tiers: parted("election.tiers", sections.tiers),
        regions:
          Array.isArray(sections.regions) && sections.regions.length > 0
            ? part("election.regions", sections.regions)
            : sections.regions,
        counties: parted("election.counties", sections.counties),
        winners: isSweep
          ? parted("election.sweep", winners, "preload")
          : parted("election.winners", winners),
      },
    };
  }
  if (data.kind !== "polity") {
    return data;
  }
  const { polities, elections } = data.polity;
  const path = ["sovereign_state", "supranational"].includes(data.polity.kind)
    ? data.polity.latest?.path
    : null;
  const [{ value: scrubber }, { value: record }, map] = await Promise.all([
    path == null
      ? { value: null }
      : soon(
          capped(
            seed(path)
              .then((found) =>
                part(
                  "polity.scrubber",
                  scrub(found.located, found.hub, found.hexes, found.path),
                  { fetch: "preload" },
                ),
              )
              .catch(() => null),
          ),
        ),
    soon(
      capped(call(router.polities.record, { id: data.id }).catch(() => null)),
    ),
    budget === Infinity
      ? []
      : reads(data.id, {
          ...data.polity,
          calendar: { count: elections.length },
        }),
  ]);
  const primed: Primed = [
    ...(record == null || record instanceof Promise
      ? []
      : [
          {
            key: orpc.polities.record.queryKey({ input: { id: data.id } }),
            data: record,
          },
        ]),
    ...map,
  ];
  const groups = Map.groupBy(polities, (child) => child.kind);
  const places = {
    total: polities.length,
    sizes: Object.fromEntries(
      groups.entries().map(([kind, rows]) => [kind, rows.length]),
    ),
    isPartial: polities.length > 200,
  };
  const dated = elections.flatMap((election) => election.year ?? []);
  const calendar = {
    count: elections.length,
    first: dated.length === 0 ? null : Math.min(...dated),
    last: dated.length === 0 ? null : Math.max(...dated),
  };
  const polity = {
    ...data.polity,
    elections: part("polity.elections", elections),
    calendar,
    places,
    scrubber,
    primed: parted(
      "polity.primed",
      primed.length === 0 ? null : primed,
      "preload",
    ),
    record: parted(
      "polity.record",
      record == null ||
        record instanceof Promise ||
        record.series.length > 0 ||
        record.governments.length > 0
        ? record
        : null,
      path == null ? undefined : "preload",
    ),
  };
  if (!places.isPartial) {
    return { ...data, polity };
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
      ...polity,
      polities: polities.filter((child) => kept.has(child)),
    },
  };
};
