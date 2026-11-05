import type { LiveOption, LiveRace } from "@ea/analytics/live";
import { openapi } from "@orpc/openapi";
import { call, ORPCError, os } from "@orpc/server";
import { sql } from "drizzle-orm";
import * as v from "valibot";
import type { Family, Seat } from "#lib/live/nation.js";
import type { Summary } from "#lib/live/summary.js";
import { chamber, seat, sides, STATES } from "#lib/live/nation.js";
import { summarize } from "#lib/live/summary.js";
import { remember } from "#lib/server/cache.js";
import { liveDb as db } from "#lib/server/db/index.js";
import { control } from "#lib/server/live-board.js";
import { live } from "#lib/server/rpc/live.js";

const OFFICES = ["senate", "house", "governor", "other"] as const;
const STATUSES = [
  "won",
  "called",
  "unopposed",
  "counting",
  "waiting",
  "no_feed",
] as const;

type Status = (typeof STATUSES)[number];

const Count = v.optional(
  v.pipe(
    v.union([v.number(), v.pipe(v.string(), v.toNumber())]),
    v.integer(),
    v.minValue(1),
    v.maxValue(200),
  ),
);

function listOf<const T extends string>(values: ReadonlyArray<T>) {
  return v.optional(
    v.pipe(
      v.union([v.string(), v.array(v.string())]),
      v.transform((value) =>
        (Array.isArray(value) ? value : value.split(",")).map((entry) =>
          entry.trim().toLowerCase(),
        ),
      ),
      v.array(v.picklist(values)),
    ),
  );
}

const round = (value: number, places = 4) =>
  Math.round(value * 10 ** places) / 10 ** places;

async function nearest() {
  const found = await db.execute<{ path: string }>(sql`
    select s.path from slug s join election e on e.id = s.entity_id
    where s.is_current and s.path ~ '^us/[0-9]{4}-[0-9]{2}-[0-9]{2}-general$'
    order by abs(e.date_from - current_date), e.date_from desc limit 1
  `);
  const path = found.rows[0]?.path;
  if (path == null) {
    throw new ORPCError("NOT_FOUND");
  }
  return path;
}

async function night(path: string) {
  try {
    return await call(live.election, { path });
  } catch (error_) {
    if (error_ instanceof ORPCError && error_.code === "NOT_FOUND") {
      throw new ORPCError("NOT_FOUND", {
        message: `No live results at ${path}. Pass an election path such as us/2026-11-03-general or us/ga/2026-11-03-general, or a race path from get_live_results.`,
      });
    }
    throw error_;
  }
}

function status(summary: Summary): Status {
  if (summary.winner != null) {
    return "won";
  }
  if (summary.projected != null) {
    return "called";
  }
  if (summary.unopposed != null) {
    return "unopposed";
  }
  if (summary.total > 0) {
    return "counting";
  }
  return summary.later == null ? "waiting" : "no_feed";
}

function party(option: LiveOption | null | undefined) {
  return option?.party?.short ?? option?.party?.name ?? null;
}

function closes(
  election: { date: string; pollClose: Array<{ state: string; at: string }> },
  race: LiveRace,
) {
  return (
    election.pollClose.find((entry) => entry.state === race.state)?.at ?? null
  );
}

function describe(
  summary: Summary,
  placed: Seat | null,
  pairing: [Family, Family] | null,
) {
  const { race, totals, total } = summary;
  const ranked = race.options
    .map((option, index) => ({ option, votes: totals[index] ?? 0 }))
    .filter((entry) => !entry.option.isWriteIn)
    .toSorted((a, b) => b.votes - a.votes);
  const name = (id: string | null) =>
    id == null ? null : (pairing?.find((side) => side.id === id)?.short ?? id);
  return {
    path: race.slug,
    title: race.title,
    office: race.office,
    state: race.state,
    district: race.district,
    status: status(summary),
    rating:
      placed == null ||
      placed.rating === "waiting" ||
      placed.rating === "later" ||
      placed.rating === "counting"
        ? null
        : placed.rating,
    votes: total,
    counted: summary.counted == null ? null : round(summary.counted, 3),
    candidates: ranked
      .slice(0, race.isMeasure === true ? 3 : 2)
      .map(({ option, votes }) => ({
        name: option.label,
        party: party(option),
        votes,
        share: total > 0 ? round(votes / total) : null,
        isIncumbent: option.isIncumbent,
      })),
    margin: total > 0 ? round(summary.margin) : null,
    winner: summary.settled?.label ?? null,
    calledAt: summary.winner == null ? summary.projectedAt : null,
    favorite: summary.favorite?.label ?? null,
    chance: summary.chance == null ? null : round(summary.chance, 3),
    heldBy: name(placed?.held ?? null),
    isFlip: placed?.isFlip ?? false,
    updated: summary.at,
  };
}

export async function holders(
  races: Array<LiveRace>,
  date: string,
  key: string,
  isNation: boolean,
) {
  return remember(
    isNation ? `live:control:v3:${date}` : `live:holders:v3:${key}`,
    async () => control(races, date),
  );
}

export const tonight = {
  results: os
    .meta(
      openapi({
        method: "GET",
        path: "/live/results",
        summary:
          "Election night results, compact: for a national election, who controls the Senate, House, and governorships (seats won, called, and ahead for each side, seats not up, and the chance of control), then each race's two leading candidates with votes and shares, the share of the expected vote counted, the call or projection, the favorite's win chance, the party holding the seat, and whether it flips. Leave election empty for the nearest US general election, or pass an election path such as us/2026-11-03-general or a state's, such as us/ga/2026-11-03-general. Narrow races with office, state, and status, sort them, and page with limit and cursor; get_live_race has one race in full",
      }),
    )
    .input(
      v.optional(
        v.object({
          election: v.optional(v.string()),
          office: listOf(OFFICES),
          state: v.optional(
            v.pipe(v.string(), v.toLowerCase(), v.regex(/^[a-z]{2}$/u)),
          ),
          status: listOf(STATUSES),
          sort: v.optional(v.picklist(["path", "closest", "updated"]), "path"),
          limit: Count,
          cursor: v.optional(v.string()),
        }),
      ),
    )
    .handler(async ({ input }) => {
      const path = input?.election ?? (await nearest());
      const { election, estimates, shown } = await night(path);
      const isNation = election.slug.split("/").length === 2;
      const summaries = election.races.map((race) =>
        summarize(race, shown.forecasts ? (estimates[race.id] ?? null) : null),
      );
      const held = await holders(
        election.races,
        election.date,
        election.slug,
        isNation,
      );
      const pairing = sides(held);
      const seats = new Map(
        pairing == null
          ? []
          : summaries
              .filter((summary) => summary.race.office !== "other")
              .map((summary) => [
                summary.race.id,
                seat(summary, held, pairing),
              ]),
      );
      const chambers =
        !isNation || pairing == null
          ? null
          : Object.fromEntries(
              (["senate", "house", "governor"] as const).map((office) => {
                const picked = [...seats.values()].filter(
                  (entry) =>
                    entry.summary.race.office === office &&
                    STATES.has(entry.summary.race.state),
                );
                const view = chamber(
                  picked,
                  office,
                  office === "house" ? {} : held.holdovers[office],
                  pairing,
                );
                const tally = (
                  side: "left" | "right" | "other",
                  name: string,
                ) => ({
                  party: name,
                  won: view.tallies[side].won,
                  called: view.tallies[side].projected,
                  unopposed: view.tallies[side].unopposed,
                  ahead: view.tallies[side].ahead,
                  notUp: view.tallies[side].holdover,
                  gains: view.tallies[side].gains,
                  losses: view.tallies[side].losses,
                });
                return [
                  office,
                  {
                    seats: view.size,
                    majority: view.majority,
                    up: picked.length,
                    waiting: view.waiting,
                    noLiveFeed: view.later,
                    sides: [
                      tally("left", pairing[0].short),
                      tally("right", pairing[1].short),
                      tally("other", "Other"),
                    ],
                    control: shown.forecasts
                      ? {
                          [pairing[0].short]: round(view.control.left, 3),
                          [pairing[1].short]: round(view.control.right, 3),
                          none: round(view.control.none, 3),
                        }
                      : null,
                    expected: shown.forecasts
                      ? {
                          [pairing[0].short]: Math.round(view.expected.left),
                          [pairing[1].short]: Math.round(view.expected.right),
                        }
                      : null,
                  },
                ];
              }),
            );
      const offices = input?.office;
      const statuses = input?.status;
      const state = input?.state;
      const rows = summaries
        .map((summary) =>
          describe(summary, seats.get(summary.race.id) ?? null, pairing),
        )
        .filter(
          (row) =>
            (offices == null || offices.includes(row.office)) &&
            (state == null || row.state === state) &&
            (statuses == null || statuses.includes(row.status)),
        );
      const sort = input?.sort ?? "path";
      const ordered = rows.toSorted(
        sort === "closest"
          ? (a, b) =>
              (a.margin ?? Infinity) - (b.margin ?? Infinity) ||
              a.path.localeCompare(b.path)
          : sort === "updated"
            ? (a, b) =>
                (b.updated ?? "").localeCompare(a.updated ?? "") ||
                a.path.localeCompare(b.path)
            : (a, b) => a.path.localeCompare(b.path),
      );
      const limit = input?.limit ?? 50;
      const offset = Math.max(0, Math.trunc(Number(input?.cursor ?? "0")) || 0);
      const page = ordered.slice(offset, offset + limit);
      const counts = new Map<Status, number>();
      for (const row of rows) {
        counts.set(row.status, (counts.get(row.status) ?? 0) + 1);
      }
      return {
        election: {
          path: election.slug,
          title: election.title,
          date: election.date,
          updated: election.newest,
          isCertified: election.isCertified,
          areResultsShown: shown.results,
          areForecastsShown: shown.forecasts,
          pollsClose: Object.fromEntries(
            election.pollClose.map((entry) => [entry.state, entry.at]),
          ),
        },
        chambers,
        matched: rows.length,
        statuses: Object.fromEntries(counts),
        races: page,
        next: offset + limit < ordered.length ? String(offset + limit) : null,
      };
    }),
  race: os
    .meta(
      openapi({
        method: "GET",
        path: "/live/race",
        summary:
          "One race on election night in full: every candidate's votes, share, and win chance, the share of the expected vote counted, precincts reporting, the call or projection, the model's range for the expected vote and the margin, the party holding the seat, and the feed it comes from. Pass places=true for each county's (or town's) latest count, largest first. race is a path from get_live_results, such as us/ga/2026-11-03-general/senate",
      }),
    )
    .input(
      v.object({
        race: v.pipe(v.string(), v.minLength(3)),
        places: v.optional(
          v.pipe(
            v.union([v.boolean(), v.picklist(["true", "false"])]),
            v.transform((value) => value === true || value === "true"),
          ),
          false,
        ),
        limit: Count,
      }),
    )
    .handler(async ({ input }) => {
      const { election, estimates, shown } = await night(input.race);
      const race = election.races.find(
        (entry) => entry.slug === input.race.replace(/\/+$/u, ""),
      );
      if (race == null) {
        throw new ORPCError("NOT_FOUND", {
          message: `${input.race} is an election, not a race. Call get_live_results with election=${input.race} to list its races.`,
        });
      }
      const estimate = shown.forecasts ? (estimates[race.id] ?? null) : null;
      const summary = summarize(race, estimate);
      const held = await holders([race], election.date, race.slug, false);
      const pairing = sides(held);
      const placed = pairing == null ? null : seat(summary, held, pairing);
      const frame = race.frames.at(-1) ?? null;
      const brief = describe(summary, placed, pairing);
      const latest = new Map<string, (typeof race.counts)[number]>();
      for (const count of race.counts) {
        const before = latest.get(count.unitId);
        if (before == null || before.at <= count.at) {
          latest.set(count.unitId, count);
        }
      }
      const named = new Map(race.units.map((unit) => [unit.id, unit.name]));
      const band = estimate?.margin;
      const flip = summary.favorite?.id === estimate?.leader ? 1 : -1;
      return {
        ...brief,
        election: { path: election.slug, title: election.title },
        pollsClose: closes(election, race),
        holder: placed?.holder === "" ? null : (placed?.holder ?? null),
        reporting: frame?.reporting ?? null,
        expected: frame?.expected ?? null,
        candidates: race.options.map((option, index) => {
          const votes = summary.totals[index] ?? 0;
          return {
            name: option.label,
            party: party(option),
            votes,
            share: summary.total > 0 ? round(votes / summary.total) : null,
            isIncumbent: option.isIncumbent,
            isWriteIn: option.isWriteIn,
            isWinner: summary.winner?.id === option.id,
            isCalled: summary.projected?.id === option.id,
            chance:
              estimate?.status === "estimating"
                ? round(estimate.winProbability[option.id] ?? 0, 3)
                : null,
          };
        }),
        estimate:
          estimate == null || estimate.status === "waiting"
            ? null
            : {
                status: estimate.status,
                expectedVotes: {
                  low: Math.round(estimate.expectedTotal.low),
                  mid: Math.round(estimate.expectedTotal.mid),
                  high: Math.round(estimate.expectedTotal.high),
                },
                counted: {
                  low: round(Math.min(1, estimate.shareIn.low), 3),
                  mid: round(Math.min(1, estimate.shareIn.mid), 3),
                  high: round(Math.min(1, estimate.shareIn.high), 3),
                },
                favoriteMargin:
                  band == null || summary.favorite == null
                    ? null
                    : {
                        low: round(Math.min(band.low * flip, band.high * flip)),
                        mid: round(band.mid * flip),
                        high: round(
                          Math.max(band.low * flip, band.high * flip),
                        ),
                      },
              },
        source: race.source.publisher === "" ? null : race.source,
        places: input.places
          ? {
              options: race.options.map((option) => option.label),
              rows: [...latest.values()]
                .map((count) => {
                  let total = 0;
                  for (const value of count.votes) {
                    total += value;
                  }
                  return {
                    name: named.get(count.unitId) ?? count.unitId,
                    votes: count.votes,
                    total,
                    counted:
                      count.reporting != null &&
                      count.expected != null &&
                      count.expected > 0
                        ? round(
                            Math.min(1, count.reporting / count.expected),
                            3,
                          )
                        : null,
                    updated: count.at,
                  };
                })
                .toSorted((a, b) => b.total - a.total)
                .slice(0, input.limit ?? 200),
            }
          : null,
        page: `/live/${race.slug}`,
      };
    }),
};
