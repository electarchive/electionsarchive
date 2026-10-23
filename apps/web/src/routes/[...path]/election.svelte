<script lang="ts">
  import { resolve } from "$app/paths";
  import ChevronDownIcon from "@lucide/svelte/icons/chevron-down";
  import {
    createQueries,
    createQuery,
    useQueryClient,
  } from "@tanstack/svelte-query";
  import { tick, untrack } from "svelte";
  import { MediaQuery } from "svelte/reactivity";
  import { count, date, percent, points } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { getLocale } from "#lib/paraglide/runtime.js";
  import { swatch } from "#lib/party.js";
  import { orpc } from "#lib/rpc.js";
  import { cn } from "#lib/utils.js";
  import type { PageData } from "./$types";
  import Cycle from "./cycle.svelte";
  import Electors from "./electors.svelte";
  import Governments from "./governments.svelte";
  import PlaceMap from "./map.svelte";
  import Places from "./places.svelte";
  import Preview from "./preview.svelte";
  import Regions from "./regions.svelte";
  import Results, { MEASURES } from "./results.svelte";
  import Seated from "./seated.svelte";
  import Tiers from "./tiers.svelte";
  import Winners from "./winners.svelte";

  const {
    id,
    election,
    cycle,
    sections,
  }: {
    id: string;
    election: Extract<PageData, { kind: "election" }>["election"];
    cycle: Extract<PageData, { kind: "election" }>["cycle"];
    sections: Extract<PageData, { kind: "election" }>["sections"];
  } = $props();

  const monthDay = new Intl.DateTimeFormat(getLocale(), {
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  });

  const client = useQueryClient();
  untrack(() => {
    client.setQueryData(
      orpc.elections.governments.queryKey({ input: { id } }),
      sections.governments,
    );
    client.setQueryData(
      orpc.elections.regions.queryKey({ input: { id } }),
      sections.regions,
    );
    client.setQueryData(
      orpc.elections.tiers.queryKey({ input: { id } }),
      sections.tiers,
    );
  });

  const STATUSES: Record<string, () => string> = {
    scheduled: m.election_scheduled,
    postponed: m.election_postponed,
    canceled: m.election_canceled,
    annulled: m.election_annulled,
  };

  const ordered = $derived.by(() => {
    const decisive = election.races.findIndex(
      (race) =>
        race.path != null && race.path === election.races[0]?.runoff?.path,
    );
    const led =
      decisive < 1
        ? election.races
        : [
            election.races[decisive],
            ...election.races.filter((_, spot) => spot !== decisive),
          ].filter((race) => race != null);
    const grouped = Map.groupBy(led, (race) => race.scope)
      .values()
      .flatMap((group) => [
        ...group.filter((race) => race.results.length !== 1),
        ...group.filter((race) => race.results.length === 1),
      ])
      .toArray();
    const lead = grouped.find((race) => race.results.length !== 1);
    const sorted =
      lead == null
        ? grouped
        : [lead, ...grouped.filter((race) => race !== lead)];
    const tiers = Map.groupBy(
      sorted.filter(
        (race) =>
          race.body != null &&
          !race.isOffice &&
          race.size != null &&
          race.electors == null,
      ),
      (race) => race.body,
    )
      .values()
      .find((group) => {
        const size = group[0]?.size ?? 0;
        let seats = 0;
        for (const race of group) {
          for (const row of race.results) {
            seats += row.seats ?? 0;
          }
        }
        return (
          new Set(group.map((race) => race.kind)).size > 1 &&
          group.every(
            (race) => (race.round ?? 1) === 1 && race.size === size,
          ) &&
          sorted.every(
            (race) => race.body !== group[0]?.body || (race.round ?? 1) === 1,
          ) &&
          seats >= size * 0.95 &&
          seats <= size * 1.02
        );
      });
    const base =
      tiers?.find((race) => race.map != null) ??
      tiers?.find((race) => race.count === 1) ??
      tiers?.[0];
    if (
      tiers == null ||
      base == null ||
      tiers.some((race) =>
        race.results.some(
          (row) =>
            row.key.startsWith("alliance:") &&
            (row.seats ?? 0) > 0 &&
            election.coalitions.every((entry) => entry.name !== row.label),
        ),
      )
    ) {
      return sorted.map((race) => ({ ...race, isTotal: false }));
    }
    const allied = new Map(
      tiers.flatMap((race) =>
        race.results.flatMap((row) => {
          const coalition = row.key.startsWith("alliance:")
            ? election.coalitions.find((entry) => entry.name === row.label)
            : null;
          return coalition == null
            ? []
            : coalition.parties.map((party) => [`party:${party}`, row.key]);
        }),
      ),
    );
    const folded = Map.groupBy(
      tiers.flatMap((race) =>
        race.results.map((row) => ({ row, isBase: race === base })),
      ),
      (entry) => allied.get(entry.row.key) ?? entry.row.key,
    )
      .entries()
      .map(([key, entries]) => {
        const head =
          entries.find((entry) => entry.row.key === key)?.row ??
          entries[0]?.row;
        let [votes, share, seats] = [0, 0, null as number | null];
        for (const { row, isBase } of entries) {
          if (isBase) {
            votes += row.votes;
            share += row.share;
          }
          seats = row.seats == null ? seats : (seats ?? 0) + row.seats;
        }
        return head == null ? null : { ...head, key, votes, share, seats };
      })
      .filter((row) => row != null)
      .toArray()
      .toSorted((a, b) => (b.seats ?? 0) - (a.seats ?? 0) || b.votes - a.votes);
    return [
      {
        ...base,
        anchor: `${tiers[0]?.anchor ?? base.anchor}-total`,
        path: null,
        kind: "body_seats",
        count: 1,
        sample: null,
        since: null,
        before: null,
        holders: null,
        partners: [],
        runoff: null,
        results: folded,
        isTotal: true,
      },
      ...sorted.map((race) => ({
        ...race,
        ...(race === base ? { map: null } : {}),
        isTotal: false,
      })),
    ];
  });

  const stages = $derived(
    election.elections
      .filter((child) => child.isStage)
      .toSorted((a, b) => (a.date ?? "").localeCompare(b.date ?? "")),
  );
  const parts = $derived(
    election.elections
      .filter((child) => !child.isStage)
      .toSorted((a, b) =>
        (a.place ?? a.name).localeCompare(b.place ?? b.name, undefined, {
          numeric: true,
        }),
      ),
  );
  const isTiled = $derived(
    parts.length > 1 &&
      parts.every((child) => /^us\/[a-z]{2}\/[^/]+$/.test(child.path ?? "")),
  );
  const partNoun = $derived(
    parts.every((child) => child.noun != null && child.noun === parts[0]?.noun)
      ? parts[0]?.noun
      : null,
  );

  const unplaced = $derived.by(() => {
    const placed = new Set(
      ordered.flatMap((race) => [
        ...(race.places?.rows.flatMap((row) => row.path ?? []) ?? []),
        ...(race.path == null ? [] : [race.path]),
      ]),
    );
    return election.contests.flatMap((contest) =>
      contest.path == null || placed.has(contest.path)
        ? []
        : [{ name: contest.name, path: contest.path }],
    );
  });

  const governments = createQuery(() =>
    orpc.elections.governments.queryOptions({ input: { id } }),
  );
  const governing = $derived(
    (
      (governments.data ?? []).find(
        (government) => !government.isBefore && !government.isCaretaker,
      )?.parties ?? []
    ).flatMap((party) =>
      party.role === "support" ? [] : [`party:${party.id}`],
    ),
  );

  const bodies = $derived([
    ...new Set(ordered.flatMap((race) => race.body ?? [])),
  ]);
  const swings = createQueries(() => ({
    queries: ordered.map((race, index) => {
      const path = race.path ?? "";
      const by = race.places?.level;
      return {
        queryKey: ["swing", race.path ?? race.anchor, by],
        enabled:
          race.path != null &&
          race.count === 1 &&
          race.map != null &&
          race.places != null &&
          race.results.length !== 1 &&
          index < 4,
        staleTime: Infinity,
        queryFn: async () => {
          const found = await orpc.contests.locate.call({ path });
          const compared = await orpc.contests.get.call({
            id: found.id,
            vs: "previous",
            by,
          });
          const year = election.date?.slice(0, 4);
          return compared.places == null ||
            compared.target?.year == null ||
            year == null ||
            !compared.places.rows.some((row) => row.earlier != null)
            ? null
            : {
                places: compared.places,
                span: {
                  from: String(compared.target.year),
                  to: year,
                  forward: false,
                },
              };
        },
      };
    }),
  }));
  const regional = $state<Record<number, boolean>>({});
  const opened = $state<Record<number, boolean>>({});
  const wide = new MediaQuery("min-width: 40rem");
  let isHydrated = $state(false);
  const firstRound = $state<Record<number, boolean>>({});
  const regionals = createQueries(() => ({
    queries: ordered.map((race, index) => ({
      ...orpc.elections.regional.queryOptions({
        input: { id, contest: race.sample ?? race.anchor },
      }),
      enabled:
        race.sample != null &&
        race.map != null &&
        race.places != null &&
        index < 4,
      staleTime: Infinity,
      meta: { quiet: true },
    })),
  }));
  const sitting = createQueries(() => ({
    queries: bodies.map((body) =>
      orpc.bodies.get.queryOptions({ input: { id: body } }),
    ),
  }));
  const after = $derived(
    new Map(
      bodies.flatMap((body, index) => {
        const data = sitting[index]?.data;
        const held = Date.parse(election.date ?? "");
        const term = data?.makeup.find(
          (entry) =>
            Date.parse(entry.start) > held &&
            Date.parse(entry.start) - held < 1.2e10,
        );
        if (data == null || term == null) {
          return [];
        }
        const info = new Map(data.parties.map((party) => [party.id, party]));
        let total = 0;
        for (const party of term.parties) {
          total += party.seats;
        }
        const size =
          data.sizes.find(
            (entry) =>
              (entry.from == null || entry.from <= term.start) &&
              (entry.to == null || entry.to > term.start),
          )?.seats ?? total;
        return [
          [
            body,
            {
              total: Math.max(size, total),
              isWhole: total <= size,
              parts: term.parties
                .map((entry) => {
                  const party = info.get(entry.party ?? "");
                  return {
                    key: entry.party ?? "none",
                    name: party?.label ?? m.board_after_other(),
                    label:
                      party?.short ?? party?.label ?? m.board_after_other(),
                    color: swatch(
                      party?.color ?? null,
                      `party:${entry.party ?? ""}`,
                    ),
                    value: entry.seats,
                    lean: entry.party == null ? Infinity : (party?.lean ?? 1e9),
                  };
                })
                .toSorted((a, b) => a.lean - b.lean),
            },
          ],
        ];
      }),
    ),
  );

  const chamber = $derived.by(() => {
    const entries = ordered
      .filter((race) => !race.isOffice && race.size != null)
      .map((race) => {
        let filled = 0;
        for (const row of race.results) {
          filled += row.seats ?? 0;
        }
        return { size: race.size ?? 0, filled, rows: race.results };
      });
    const joined = Map.groupBy(entries, (entry) => entry.size)
      .entries()
      .filter(([, group]) => group.length > 1)
      .map(([size, group]) => {
        let filled = 0;
        for (const entry of group) {
          filled += entry.filled;
        }
        return { size, filled, rows: group.flatMap((entry) => entry.rows) };
      })
      .filter((entry) => entry.filled <= entry.size * 1.02)
      .toArray();
    const best = [
      ...entries.toSorted((a, b) => b.filled - a.filled),
      ...joined,
    ].find((entry) => entry.filled >= entry.size * 0.9);
    if (best == null) {
      return null;
    }
    const seats: Record<string, number> = {};
    for (const row of best.rows) {
      if (row.party != null && row.seats != null) {
        seats[row.party] = (seats[row.party] ?? 0) + row.seats;
      }
    }
    return { size: best.size, seats };
  });

  const named = (row: { label: string; short: string | null }) =>
    row.label.length > 28 ? (row.short ?? row.label) : row.label;
  const races = $derived(
    ordered.map((race) => {
      const winners = race.results.filter((row) => (row.seats ?? 0) > 0);
      let total = 0;
      for (const row of winners) {
        total += row.seats ?? 0;
      }
      const loose = winners.filter(
        (row) => row.party == null && row.key.startsWith("person:"),
      );
      let unaffiliated = 0;
      for (const row of loose) {
        unaffiliated += row.seats ?? 0;
      }
      const grouped =
        loose[0] == null || loose.length < 2
          ? winners
          : [
              ...winners.filter((row) => !loose.includes(row)),
              {
                ...loose[0],
                key: "independents",
                label: m.seats_independents(),
                short: null,
                color: null,
                lean: null,
                person: null,
                seats: unaffiliated,
              },
            ];
      const seated =
        race.count === 1 && total <= 1 && race.kind !== "elector_slate"
          ? []
          : grouped;
      const units = Map.groupBy(
        seated,
        (row) =>
          race.partners.find((pair) => pair.includes(row.key))?.[0] ?? row.key,
      )
        .values()
        .map((rows) => {
          const head = rows.toSorted(
            (a, b) => (b.seats ?? 0) - (a.seats ?? 0),
          )[0];
          let seats = 0;
          let share = 0;
          for (const row of rows) {
            seats += row.seats ?? 0;
            share += row.share;
          }
          return head == null
            ? null
            : rows.length === 1
              ? { ...head, keys: [head.key] }
              : {
                  ...head,
                  label: rows.map((row) => row.short ?? row.label).join("/"),
                  short: null,
                  seats,
                  share,
                  keys: rows.map((row) => row.key),
                };
        })
        .filter((row) => row != null)
        .toArray();
      const leader =
        units.toSorted((a, b) => (b.seats ?? 0) - (a.seats ?? 0))[0] ??
        race.results[0];
      const joined = units.find((unit) => unit === leader)?.keys ?? [];
      const runner = race.results.find(
        (row) => row.key !== leader?.key && !joined.includes(row.key),
      );
      const legend = race.places?.legend ?? [];
      const districts = race.places?.rows ?? [];
      const wins = districts.filter((row) =>
        joined.includes(legend[row.leader ?? -1]?.key ?? ""),
      ).length;
      const elector = race.electors?.rows[0];
      const second = race.electors?.rows[1];
      let appointed = race.electors?.appointed ?? 0;
      if (race.electors?.appointed == null) {
        for (const row of race.electors?.rows ?? []) {
          appointed += row.votes;
        }
      }
      const popular =
        race.measure === "electoral_votes"
          ? null
          : race.results.find(
              (row) => row.person != null && row.person === elector?.person,
            );
      const before = new Map(
        (race.before ?? []).map((entry) => [entry.key, entry.seats]),
      );
      const change =
        race.before == null ||
        seated.some((row) => row.key.startsWith("alliance:")) ||
        seated.every((row) => !before.has(row.key))
          ? null
          : Object.fromEntries(
              seated.flatMap((row) => {
                const earlier = before.get(row.key);
                return earlier == null
                  ? []
                  : [[row.key, (row.seats ?? 0) - earlier]];
              }),
            );
      const whole =
        race.body == null || race.electors != null
          ? null
          : after.get(race.body);
      const isPartial =
        whole != null && race.count > 1 && whole.total > total * 1.25;
      const top = whole?.parts.toSorted((a, b) => b.value - a.value) ?? [];
      const isRound =
        race.round != null &&
        ordered.some(
          (other) =>
            other !== race &&
            other.name === race.name &&
            other.round !== race.round,
        );
      const isSplit =
        race.count > 1 &&
        ordered.some(
          (other) =>
            other !== race && other.name === race.name && other.count === 1,
        );
      const isTallied = isSplit && districts.length === race.count;
      const noun = race.places?.levels[0]?.noun ?? "";
      let decided: {
        places: NonNullable<typeof race.places>;
        tones: Array<string>;
        titles: Array<string>;
      } | null = null;
      if (
        isRound &&
        race.round === 1 &&
        seated.length > 0 &&
        race.places != null
      ) {
        const level = race.places.level;
        const later = ordered
          .filter(
            (other) =>
              other.name === race.name &&
              (other.round ?? 1) > 1 &&
              other.places?.level === level,
          )
          .toSorted((a, b) => (a.round ?? 1) - (b.round ?? 1));
        const finals = new Map(
          later.flatMap((other) =>
            (other.places?.rows ?? []).map((row) => [
              row.k,
              { row, legend: other.places?.legend ?? [] },
            ]),
          ),
        );
        if (finals.size > 0) {
          const merged = [
            ...race.places.legend,
            ...Map.groupBy(
              later.flatMap((other) => other.places?.legend ?? []),
              (item) => item.key,
            )
              .values()
              .flatMap((items) =>
                items[0] == null ||
                race.places?.legend.some((item) => item.key === items[0]?.key)
                  ? []
                  : [items[0]],
              ),
          ];
          const at = new Map(merged.map((item, spot) => [item.key, spot]));
          decided = {
            places: {
              ...race.places,
              legend: merged,
              districts: undefined,
              rows: race.places.rows.map((row) => {
                const final = finals.get(row.k);
                if (final == null) {
                  return {
                    ...row,
                    shares: merged.map((_, spot) => row.shares[spot] ?? 0),
                  };
                }
                const shares = merged.map(() => 0);
                for (const [spot, item] of final.legend.entries()) {
                  shares[at.get(item.key) ?? 0] = final.row.shares[spot] ?? 0;
                }
                return {
                  ...row,
                  turnout: final.row.turnout,
                  valid: final.row.valid,
                  shares,
                  leader:
                    final.row.leader == null
                      ? null
                      : (at.get(final.legend[final.row.leader]?.key ?? "") ??
                        null),
                  margin: final.row.margin,
                  earlier: null,
                  prior: null,
                };
              }),
            },
            tones: merged.map((item) => swatch(item.color, item.key)),
            titles: merged.map((item) =>
              item.person == null ? (item.short ?? item.label) : item.label,
            ),
          };
        }
      }
      return {
        ...race,
        decided,
        change,
        whole: isPartial ? whole : null,
        sequel:
          !isPartial || top[0] == null || top[0].key === "none"
            ? null
            : top[1]?.value === top[0].value
              ? m.race_after_tied({
                  party: top[0].name,
                  runner: top[1].name,
                  seats: count(top[0].value),
                  total: count(whole.total),
                })
              : m.race_after({
                  party: top[0].name,
                  seats: count(top[0].value),
                  total: count(whole.total),
                }),
        tones: legend.map((item) => swatch(item.color, item.key)),
        titles: legend.map((item) =>
          item.person == null ? (item.short ?? item.label) : item.label,
        ),
        balance:
          race.electors != null && race.electors.rows.length > 0
            ? {
                total: appointed,
                parts: race.electors.rows
                  .toSorted(
                    (a, b) =>
                      (a.lean ?? 1e9) - (b.lean ?? 1e9) || b.votes - a.votes,
                  )
                  .map((row) => ({
                    key: row.key,
                    label:
                      row.person == null ? (row.short ?? row.label) : row.label,
                    color: swatch(row.color, row.key),
                    value: row.votes,
                    photo:
                      race.results.find((entry) => entry.key === row.key)
                        ?.photo ?? null,
                  })),
                isCount: true,
                isWhole: true,
              }
            : seated.length > 1 || (seated.length > 0 && race.count > 1)
              ? {
                  total: isTallied
                    ? race.count
                    : race.size != null && race.size <= total + 2
                      ? race.size
                      : total,
                  parts: units
                    .toSorted(
                      (a, b) =>
                        (a.lean ?? 1e9) - (b.lean ?? 1e9) ||
                        (b.seats ?? 0) - (a.seats ?? 0),
                    )
                    .map((row) => {
                      let delta: number | null = 0;
                      for (const key of row.keys) {
                        const step = change?.[key];
                        delta =
                          step == null || delta == null ? null : delta + step;
                      }
                      return {
                        key: row.key,
                        label: row.key.startsWith("alliance:")
                          ? row.label
                          : (row.short ?? row.label),
                        color: swatch(row.color, row.key),
                        value: isTallied
                          ? districts.filter((place) =>
                              row.keys.includes(
                                legend[place.leader ?? -1]?.key ?? "",
                              ),
                            ).length
                          : (row.seats ?? 0),
                        delta: change == null || isTallied ? undefined : delta,
                        photo: null,
                      };
                    }),
                  isCount: true,
                  isWhole:
                    isTallied || (race.size != null && race.size <= total + 2),
                }
              : {
                  total: 1,
                  parts: race.results
                    .slice(0, 2)
                    .toSorted((a, b) => (a.lean ?? 1e9) - (b.lean ?? 1e9))
                    .map((row) => ({
                      key: row.key,
                      label:
                        row.person == null
                          ? (row.short ?? row.label)
                          : row.label,
                      color: swatch(row.color, row.key),
                      value: row.share,
                      photo: row.photo,
                    })),
                  isCount: false,
                  isWhole: false,
                },
        isLater: isRound && (race.round ?? 1) > 1,
        name:
          isRound && decided == null
            ? m.race_round({ name: race.name, round: race.round ?? 1 })
            : isSplit
              ? m.race_split({ name: race.name, noun })
              : race.kind === "list_allocation" &&
                  ordered.some(
                    (other) => other.isTotal && other.body === race.body,
                  )
                ? m.race_list({ name: race.name })
                : race.isRemainder &&
                    ordered.some(
                      (other) => other !== race && other.name === race.name,
                    )
                  ? m.race_special({ name: race.name })
                  : race.name,
        votes:
          decided == null
            ? (MEASURES[race.measure]?.() ?? m.col_votes())
            : m.col_votes_first(),
        isSlate: race.kind === "elector_slate",
        seated: (seated.length === 0 &&
        race.kind === "body_seats" &&
        race.body != null &&
        (race.size ?? 0) > 1
          ? grouped
          : seated
        ).map((row) => ({
          ...row,
          seats: row.seats ?? 0,
          here: 0,
          members: race.holders?.seats[row.key] ?? [],
        })),
        holders: race.holders?.places ?? [],
        isLone: seated.length === 0,
        rows: race.results.map((row) => ({
          key: row.key,
          label: row.label,
          short: row.short,
          color: row.color,
          lean: row.lean,
          current: {
            label: row.label,
            person: row.person,
            party: row.party,
            votes: row.votes,
            share: row.share,
            outcome: null,
            seats: row.seats,
            lines: [],
          },
          earlier: null,
          delta: null,
          backed: null,
          origin: null,
          photo: row.photo,
        })),
        lede: race.isPrimaries
          ? m.race_lede_primaries({ count: count(race.count) })
          : elector != null
            ? second?.votes === elector.votes
              ? m.race_lede_electors_tied({
                  party: elector.label,
                  runner: second.label,
                  seats: count(elector.votes),
                  total: count(appointed),
                })
              : popular == null
                ? m.race_lede_electors_only({
                    party: elector.label,
                    seats: count(elector.votes),
                    total: count(appointed),
                  })
                : m.race_lede_electors({
                    party: elector.label,
                    seats: count(elector.votes),
                    total: count(appointed),
                    share: percent(popular.share),
                  })
            : leader == null
              ? null
              : race.kind === "elector_slate" && seated.length > 0
                ? m.race_lede_electors({
                    party: named(leader),
                    seats: count(leader.seats ?? 0),
                    total: count(total),
                    share: percent(leader.share),
                  })
                : seated.length > 0 &&
                    race.count > 1 &&
                    leader.key.startsWith("person:")
                  ? m.race_lede_districts({
                      seats: count(total),
                      count: count(race.count),
                    })
                  : seated.length > 0 && !race.isOffice && isSplit
                    ? m.race_lede_split({
                        party: named(leader),
                        seats: count(isTallied ? wins : (leader.seats ?? 0)),
                        total: count(race.count),
                        noun,
                        share: percent(leader.share),
                      })
                    : seated.length > 0 && race.isTotal && leader.share === 0
                      ? m.race_lede_seats_only({
                          party: named(leader),
                          seats: count(leader.seats ?? 0),
                          total: count(
                            race.size != null && race.size <= total + 2
                              ? race.size
                              : total,
                          ),
                        })
                      : seated.length > 0 && !race.isOffice
                        ? (race.kind === "body_seats" && race.body == null
                            ? m.race_lede_tally
                            : isRound && race.round === 1
                              ? m.race_lede_seats_first
                              : m.race_lede_seats)({
                            party: named(leader),
                            seats: count(leader.seats ?? 0),
                            total: count(
                              race.size != null && race.size <= total + 2
                                ? race.size
                                : total,
                            ),
                            share: percent(leader.share),
                          })
                        : race.kind === "ballot_question" &&
                            race.results[0]?.isLost === true
                          ? m.race_lede_lost({
                              answer: named(leader),
                              share: percent(leader.share),
                            })
                          : runner == null
                            ? null
                            : (seated.length > 0 ||
                                race.results[0]?.isWon === true
                                ? m.race_lede_won
                                : m.race_lede_votes)({
                                party: named(leader),
                                share: percent(leader.share),
                                margin: points(
                                  leader.share - runner.share,
                                ).replace(/^\+/u, ""),
                                runner: named(runner),
                              }),
        next:
          race.runoff?.runner == null
            ? null
            : (race.runoff.isWon
                ? m.race_lede_runoff_won
                : m.race_lede_runoff_led)({
                party: race.runoff.label,
                date: new Intl.DateTimeFormat(getLocale(), {
                  month: "long",
                  day: "numeric",
                  year:
                    race.runoff.date?.slice(0, 4) === election.date?.slice(0, 4)
                      ? undefined
                      : "numeric",
                  timeZone: "UTC",
                }).format(new Date(`${race.runoff.date ?? ""}T00:00:00Z`)),
                share: percent(race.runoff.share),
                margin: points(race.runoff.margin).replace(/^\+/u, ""),
                runner: race.runoff.runner,
              }),
      };
    }),
  );
  const isBallot = $derived(
    races.length > 2 && races.every((race) => race.kind === "ballot_question"),
  );
  const others = $derived(
    isBallot ? races : races.slice(1).filter((race) => !race.isLater),
  );
  const isFolding = $derived(isHydrated && !wide.current && races.length > 4);
  $effect(() => {
    isHydrated = true;
    const spot = untrack(() =>
      races.findIndex((race) => `#race-${race.anchor}` === location.hash),
    );
    if (spot > 0) {
      opened[spot] = true;
      void tick().then(() =>
        document.querySelector(location.hash)?.scrollIntoView(),
      );
    }
  });
</script>

<header class="col-span-12 grid gap-1.5 bg-card px-3.5 pbs-4 pbe-3.5">
  <h1 class="title">
    {election.name}
  </h1>
  <p
    class="flex flex-wrap items-baseline gap-x-3 gap-y-1 text-[0.9375rem] text-muted-foreground"
  >
    <time datetime={election.date}>{date(election.date)}</time>
    {#if STATUSES[election.status] != null}
      <span class="highlighter px-1 font-semibold text-foreground"
        >{STATUSES[election.status]?.()}</span
      >
    {/if}
  </p>
  {#if election.coverage != null}
    <p class="text-[0.9375rem] font-semibold">
      {m.election_partial({
        held: count(election.coverage.held),
        of: count(election.coverage.of),
        noun: election.coverage.noun,
      })}
    </p>
  {/if}
  {#if election.turnout != null}
    {const turnout = $derived(election.turnout)}
    {const share = $derived(turnout.voters / turnout.registered)}
    <p
      class="flex flex-wrap items-baseline gap-x-2.5 gap-y-0.5 text-[0.9375rem]"
    >
      <span class="font-figures tabular-nums"
        >{m.election_turnout({
          voters: count(turnout.voters),
          registered: count(turnout.registered),
          share: percent(share),
        })}</span
      >
      {#if turnout.earlier != null}
        <span class="font-figures font-semibold tabular-nums"
          >{m.turnout_since({
            points: points(
              share - turnout.earlier.voters / turnout.earlier.registered,
            ),
            year: String(turnout.earlier.year),
          })}</span
        >
      {/if}
      {#if turnout.reporting < turnout.of}
        <span class="text-[0.8125rem] text-muted-foreground"
          >{m.turnout_partial({
            reporting: String(turnout.reporting),
            of: String(turnout.of),
          })}</span
        >
      {/if}
    </p>
  {/if}
</header>

{#if races.length === 0 && election.contests.length > 0}
  <section class="col-span-12 bg-card px-3.5 py-3">
    <h2 class="mbe-1.5 text-[0.8125rem] font-bold">{m.election_contests()}</h2>
    <ul class="grid text-sm">
      {#each election.contests as contest, index (index)}
        <li
          class="flex items-baseline justify-between gap-4 border-be border-border/60 py-1.5 last:border-be-0"
        >
          {#if contest.path == null}
            <span class="font-semibold">{contest.name}</span>
          {:else}
            <Preview class="font-semibold hover:highlighter" path={contest.path}
              >{contest.name}</Preview
            >
          {/if}
          {#if contest.children > 0}
            <span class="shrink-0 text-[0.8125rem] text-muted-foreground"
              >{m.election_parts({ count: contest.children })}</span
            >
          {/if}
        </li>
      {/each}
    </ul>
  </section>
{/if}

{#each races as race, index (index)}
  {#if index === 0 && isBallot}
    {@render board()}
  {/if}
  {#if index === 0 && !isBallot && race.results.length > 0 && (others.length > 0 || race.balance.parts.some((part) => part.value > 0))}
    <section
      id="race-{race.anchor}"
      class="col-span-12 grid scroll-mbs-4 gap-x-10 gap-y-3 bg-card px-3.5 pbs-4 pbe-3 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]"
    >
      <div class="grid content-start gap-1.5">
        <h2
          class="text-[clamp(1.5rem,2.4vw,2rem)]/tight font-[760] tracking-[-0.015em] text-balance"
        >
          {race.name}
        </h2>
        {#if race.lede != null}
          {@render lede(race, true)}
        {/if}
        {@render meta(race)}
      </div>
      {@render tally(race, true)}
    </section>
  {:else if index === 0 || race.results.length !== 1}
    <section
      id="race-{race.anchor}"
      class="col-span-12 grid scroll-mbs-4 gap-1.5 bg-card px-3.5 pbs-4 pbe-1"
    >
      <div
        class="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1"
      >
        <h2
          class={cn("font-[760] tracking-[-0.015em] text-balance", {
            "text-[clamp(1.5rem,2.4vw,2rem)]/tight": index === 0,
            "text-2xl/tight": index > 0,
          })}
        >
          {race.name}
        </h2>
        {@render meta(race)}
      </div>
      {#if race.lede != null && (index === 0 || race.isLater)}
        <p
          class={cn("text-pretty max-inline-[64ch]", {
            "text-[1.0625rem]": index === 0,
            "text-[0.9375rem]": index > 0,
          })}
        >
          {race.lede}
        </p>
      {/if}
      {#if race.results.length === 0 && !race.isPrimaries}
        <p class="text-[0.9375rem] text-muted-foreground">{m.no_results()}</p>
      {:else if isFolding && index > 0}
        <button
          type="button"
          class="mbs-1.5 mbe-2 flex items-center justify-center gap-1.5 rounded-sm border border-input bg-card text-[0.8125rem] font-semibold keycap block-10 hover:bg-secondary"
          aria-expanded={opened[index] === true}
          onclick={() => (opened[index] = opened[index] !== true)}
          >{opened[index] === true
            ? m.race_hide()
            : m.race_show()}<ChevronDownIcon
            class={cn("transition-transform block-4 inline-4", {
              "rotate-180": opened[index] === true,
            })}
            aria-hidden="true"
          /></button
        >
      {/if}
    </section>
  {/if}
  {#if (race.results.length > 1 || race.isPrimaries || (index === 0 && race.results.length > 0)) && (!isFolding || index === 0 || opened[index] === true)}
    {#if race.electors != null && race.electors.rows.length > 0 && races.length === 1}
      <Electors electors={race.electors} />
    {/if}
    {const isSeated = $derived(
      race.seated.length > 0 &&
        (!race.isLone || election.parent != null) &&
        race.electors == null &&
        !race.isOffice &&
        !race.isSlate &&
        (race.kind !== "body_seats" || race.body != null),
    )}
    {const isMapped = $derived(
      race.map != null &&
        race.places != null &&
        (index < 4 || race.body != null),
    )}
    {#if isMapped && race.map != null && race.places != null}
      <div
        class={cn(
          "col-span-12 self-stretch bg-card px-3.5 pbs-3 pbe-3.5 min-inline-0",
          {
            "lg:col-span-8": isSeated,
            "lg:col-span-7 xl:col-span-8": !isSeated,
          },
        )}
      >
        {const option = $derived(regionals[index]?.data ?? null)}
        {const region = $derived(regional[index] === true ? option : null)}
        {const swing = $derived(swings[index]?.data ?? null)}
        {const final = $derived(
          region == null && firstRound[index] !== true ? race.decided : null,
        )}
        {const places = $derived(
          region?.places ?? swing?.places ?? final?.places ?? race.places,
        )}
        {const shape = $derived(region?.map ?? race.map)}
        <div class="grid gap-2 lg:sticky lg:inset-bs-3">
          {#if race.decided != null && region == null}
            <div
              class="flex self-start"
              role="group"
              aria-label={m.map_round_label()}
            >
              {#each [false, true] as isFirst (isFirst)}
                <button
                  type="button"
                  class={cn(
                    "-ms-px flex items-center border border-input bg-card px-2.5 text-[0.8125rem] font-semibold whitespace-nowrap keycap block-7 first:ms-0 first:rounded-s-sm last:rounded-e-sm hover:bg-secondary pointer-coarse:block-10",
                    {
                      "relative z-10 bg-primary text-primary-foreground hover:bg-primary":
                        (firstRound[index] === true) === isFirst,
                    },
                  )}
                  aria-pressed={(firstRound[index] === true) === isFirst}
                  onclick={() => (firstRound[index] = isFirst)}
                  >{isFirst ? m.round_first() : m.map_round_seats()}</button
                >
              {/each}
            </div>
          {/if}
          {#key places.level}
            <PlaceMap
              {places}
              colors={swing == null && region == null
                ? (final?.tones ?? race.tones)
                : places.legend.map((item) => swatch(item.color, item.key))}
              tiles={shape.tiles}
              own={shape.own}
              nested={shape.nested}
              frames={shape.frames}
              blank={shape.blank ?? null}
              isApproximate={shape.isApproximate}
              sources={shape.credits}
              noun={places.levels[0]?.noun ?? ""}
              votes={final == null ? race.votes : m.col_votes()}
              names={swing == null && region == null
                ? (final?.titles ?? race.titles)
                : places.legend.map((item) =>
                    item.person == null
                      ? (item.short ?? item.label)
                      : item.label,
                  )}
              levels={option?.map == null
                ? []
                : [race.places, option.places].map((entry) => {
                    const noun = entry.levels[0]?.noun ?? "";
                    return {
                      code: entry.level,
                      label: noun.charAt(0).toUpperCase() + noun.slice(1),
                      count: entry.rows.length,
                      path: "",
                      query: "",
                      isCurrent: entry.level === places.level,
                    };
                  })}
              pick={(code) => (regional[index] = code === option?.places.level)}
              span={swing?.span ??
                (region?.since != null
                  ? {
                      from: region.since,
                      to: election.date?.slice(0, 4) ?? "",
                      forward: false,
                    }
                  : race.count > 1 &&
                      race.since != null &&
                      race.places.rows.some((row) => row.prior != null)
                    ? {
                        from: race.since,
                        to: election.date?.slice(0, 4) ?? "",
                        forward: false,
                      }
                    : null)}
            />
          {/key}
          {const districts = $derived(
            new Set(places.rows.map((row) => row.k)).size,
          )}
          {#if shape.shaped != null && shape.shaped < districts}
            <p class="px-1 pbs-1 text-xs text-pretty text-muted-foreground">
              {m.map_shaped({
                shaped: count(shape.shaped),
                count: count(districts),
              })}
            </p>
          {/if}
        </div>
      </div>
    {/if}
    {#if isSeated}
      <Seated
        parties={race.seated}
        places={race.holders}
        body={race.body}
        size={race.size}
        date={election.date}
        place={race.name}
        region={election.parent == null
          ? null
          : (election.breadcrumbs
              .filter((crumb) => crumb.path !== election.parent?.path)
              .at(-1)?.name ?? null)}
        change={race.change}
        isComplete={race.size != null || race.count === 1}
        partners={race.partners}
        governing={race.size === chamber?.size ? governing : []}
      />
    {/if}
    {#if !race.isPrimaries}
      <div
        class={cn(
          "@container col-span-12 grid content-start gap-1 bg-card px-1.5 pbs-2 pbe-2.5",
          {
            "lg:col-span-5 xl:col-span-4": isMapped && !isSeated,
            "lg:col-span-8": isSeated && !isMapped,
          },
        )}
      >
        <Results
          rows={race.rows}
          trace={race.count === 1 ? race.path : null}
          span={null}
          votes={race.votes}
          seats={race.isLone || (race.isOffice && race.electors == null)
            ? null
            : race.electors == null && !race.isSlate
              ? m.col_seats()
              : m.col_electors()}
        />
      </div>
    {/if}
    {#if race.places != null && !race.isTotal}
      {const shown = $derived(
        regional[index] === true
          ? (regionals[index]?.data?.places ?? race.places)
          : firstRound[index] === true
            ? race.places
            : (race.decided?.places ?? race.places),
      )}
      <Places
        places={{
          ...shown,
          levels: shown.levels.filter((level) => level.code === shown.level),
        }}
        span={null}
        trace={race.count === 1 ? race.path : null}
        publishers={[]}
        votes={shown === race.decided?.places ? m.col_votes() : race.votes}
        {isMapped}
        limit={isBallot ? 5 : 10}
      />
    {/if}
  {/if}
  {#if index === 0 && others.length > 0 && !isBallot}
    {@render board()}
  {/if}
{/each}

{#snippet board()}
  <section class="col-span-12 bg-card px-3.5" aria-labelledby="board-title">
    <h2 id="board-title" class="sr-only">
      {m.board_title({ count: races.length })}
    </h2>
    <ol
      class="grid gap-x-10 lg:grid-cols-[repeat(auto-fit,minmax(min(100%,22rem),1fr))]"
    >
      {#each others as other (other.anchor)}
        <li
          class="grid content-start gap-3 border-bs border-border py-4 lg:border-bs-0 max-lg:first:border-bs-0"
        >
          <div class="grid content-start gap-1">
            <h3
              class="text-lg/tight font-[740] tracking-[-0.012em] text-balance"
            >
              {#if other.results.length !== 1}
                <a
                  class="underline decoration-transparent underline-offset-4 hover:decoration-foreground"
                  href="#race-{other.anchor}"
                  onclick={() => (opened[races.indexOf(other)] = true)}
                  >{other.name}</a
                >
              {:else if other.path == null}
                {other.name}
              {:else}
                <Preview
                  class="underline decoration-transparent underline-offset-4 hover:decoration-foreground"
                  path={other.path}>{other.name}</Preview
                >
              {/if}
            </h3>
            {#if other.lede != null}
              <div
                class={cn({
                  "max-sm:hidden":
                    races.length > 4 &&
                    other.next == null &&
                    !other.isPrimaries,
                })}
              >
                {@render lede(other, false)}
              </div>
            {/if}
          </div>
          {#if other.results.length > 0}
            {@render tally(other, false)}
          {:else if !other.isPrimaries}
            <p class="text-[0.9375rem] text-muted-foreground">
              {m.no_results()}
            </p>
          {/if}
        </li>
      {/each}
    </ol>
  </section>
{/snippet}

{#snippet lede(race: (typeof races)[number], isLead: boolean)}
  <p
    class={cn("font-serif text-pretty max-inline-[52ch]", {
      "text-[clamp(1.0625rem,1.5vw,1.25rem)]/snug": isLead,
      "text-[1.0625rem]/snug": !isLead,
    })}
  >
    {race.sequel ?? race.lede}
    {#if race.next != null}
      {#if race.runoff?.path == null}
        {race.next}
      {:else}
        <a
          class="underline decoration-border underline-offset-4 hover:decoration-foreground"
          href={resolve("/[...path]", { path: race.runoff.path })}
          >{race.next}</a
        >
      {/if}
    {/if}
  </p>
{/snippet}

{#snippet meta(race: (typeof races)[number])}
  <p class="flex items-baseline gap-3 text-[0.8125rem] text-muted-foreground">
    {#if race.count > 1}
      <span
        >{race.nonvoting > 0
          ? m.race_contests_nonvoting({
              count: count(race.count),
              nonvoting: count(race.nonvoting),
            })
          : m.race_contests({ count: count(race.count) })}</span
      >
    {/if}
    {#if race.path != null}
      <Preview
        class="font-semibold text-foreground underline decoration-border underline-offset-4 hover:decoration-foreground"
        path={race.path}>{m.race_open()}</Preview
      >
    {/if}
  </p>
{/snippet}

{#snippet tally(race: (typeof races)[number], isLead: boolean)}
  <div class="grid content-end gap-3">
    {#if race.whole == null}
      {@render balance(race.balance, isLead)}
    {:else}
      <div class="grid gap-1">
        <p class="text-[0.8125rem] text-muted-foreground">
          {m.board_won()}
        </p>
        {@render balance(race.balance, isLead)}
      </div>
      <div class="grid gap-1">
        <p class="text-[0.8125rem] text-muted-foreground">
          {m.board_after()}
        </p>
        {@render balance({ ...race.whole, isCount: true }, false)}
      </div>
    {/if}
  </div>
{/snippet}

{#if unplaced.length > 0}
  <section class="col-span-12 bg-card px-3.5 py-3">
    <h2 class="text-[0.8125rem] font-bold">{m.election_unplaced()}</h2>
    <p class="mbe-1.5 text-[0.8125rem] text-muted-foreground">
      {m.election_unplaced_detail()}
    </p>
    <ul class="grid gap-x-6 text-sm sm:grid-cols-2 lg:grid-cols-3">
      {#each unplaced as contest, index (index)}
        <li class="border-be border-border/60 py-1.5 last:border-be-0">
          <Preview class="font-semibold hover:highlighter" path={contest.path}
            >{contest.name}</Preview
          >
        </li>
      {/each}
    </ul>
  </section>
{/if}

{#if stages.length > 0}
  <section class="col-span-12 bg-card px-3.5 py-3">
    <h2 class="mbe-1.5 text-[0.8125rem] font-bold">{m.election_stages()}</h2>
    <ul class="grid text-sm">
      {#each stages as child, index (index)}
        <li
          class="flex items-baseline justify-between gap-4 border-be border-border/60 py-1.5 last:border-be-0"
        >
          {#if child.path == null}
            <span>{child.name}</span>
          {:else}
            <Preview class="hover:highlighter" path={child.path}
              >{child.name}</Preview
            >
          {/if}
          <time
            class="shrink-0 font-figures text-xs text-muted-foreground"
            datetime={child.date}>{date(child.date)}</time
          >
        </li>
      {/each}
    </ul>
  </section>
{/if}

<Cycle
  primaries={cycle.primaries.map((primary) => ({
    ...primary,
    key: primary.id,
    label:
      primary.party == null
        ? primary.name
        : primary.until == null
          ? m.cycle_primary({ party: primary.party.label })
          : m.cycle_primaries({ party: primary.party.label }),
    color: primary.party?.color ?? null,
    runoff: null,
    local: null,
  }))}
  general={cycle.general == null
    ? null
    : { ...cycle.general, winners: [], local: null }}
  others={cycle.parts.map((part) => ({
    key: part.place,
    label: part.place,
    color: null,
    races: part.races.map((race) => ({
      ...race,
      label: [
        race.date == null
          ? null
          : monthDay.format(new Date(`${race.date}T00:00:00Z`)),
        race.party?.short ?? race.party?.label ?? null,
      ]
        .filter((part) => part != null)
        .join(", "),
    })),
  }))}
  othersTitle={m.cycle_parts()}
/>

<Governments {id} {chamber} />
<Regions {id} />

{#if isTiled}
  <Tiers
    {id}
    {parts}
    noun={partNoun ?? ""}
    race={ordered.find((race) => race.electors != null)?.name ?? null}
  />
{:else if parts.length >= 40}
  <Winners {id} {parts} />
{:else if parts.length > 0}
  <section class="col-span-12 bg-card px-3.5 py-3">
    <h2 class="mbe-2 text-[0.8125rem] font-bold">
      {partNoun == null
        ? m.election_children()
        : m.election_places({ noun: partNoun })}
    </h2>
    <ul class="grid grid-cols-2 gap-x-6 text-sm sm:grid-cols-3 lg:grid-cols-4">
      {#each parts as child, index (index)}
        <li
          class="flex items-baseline justify-between gap-3 border-be border-border/60 py-1.5 min-inline-0"
        >
          {#if child.path == null}
            <span class="truncate">{child.place ?? child.name}</span>
          {:else}
            <Preview class="truncate hover:highlighter" path={child.path}
              >{child.place ?? child.name}</Preview
            >
          {/if}
          {#if child.date !== election.date}
            <time
              class="shrink-0 font-figures text-xs text-muted-foreground"
              datetime={child.date}>{date(child.date)}</time
            >
          {/if}
        </li>
      {/each}
    </ul>
  </section>
{/if}

{#if election.notes.length > 0}
  <section class="col-span-12 bg-card px-3.5 py-3">
    <h2 class="mbe-1.5 text-[0.8125rem] font-bold">{m.notes_title()}</h2>
    <ul class="grid gap-1.5 text-sm/snug text-muted-foreground">
      {#each election.notes as note, index (index)}
        <li class="max-inline-[72ch]">{note.body}</li>
      {/each}
    </ul>
  </section>
{/if}

{#snippet balance(
  bar: {
    total: number;
    parts: Array<{
      key: string;
      label: string;
      color: string;
      value: number;
      delta?: number | null;
      photo?: {
        src: string;
        license: string;
        artist: string | null;
        source: string;
      } | null;
    }>;
    isCount: boolean;
    isWhole: boolean;
  },
  isLead: boolean,
)}
  {const shown = $derived(
    bar.parts
      .filter((part) => part.value > 0)
      .toSorted((a, b) => b.value - a.value)
      .slice(0, 2)
      .toSorted((a, b) => bar.parts.indexOf(a) - bar.parts.indexOf(b)),
  )}
  {const isCrowded = $derived(
    bar.isCount && bar.parts.filter((part) => part.value > 0).length > 2,
  )}
  {#if !bar.isCount && shown.length === 1}
    <p class="flex items-baseline gap-1.5 text-[0.9375rem] font-semibold">
      <span
        class="shrink-0 self-center rounded-xs block-2.5 inline-2.5"
        style:background-color={shown[0]?.color}
      ></span>
      <span class="truncate">{shown[0]?.label}</span>
      <span class="shrink-0 text-[0.8125rem] font-normal text-muted-foreground"
        >{m.map_unopposed()}</span
      >
    </p>
  {:else}
    <div class="grid gap-1.5">
      <div class="flex items-end justify-between gap-4">
        {#each shown as part, at (part.key)}
          <p
            class={cn("grid min-inline-0", {
              "justify-items-end text-end": at === 1,
              "gap-x-3": part.photo != null,
              "grid-cols-[auto_minmax(0,1fr)]": part.photo != null && at === 0,
              "grid-cols-[minmax(0,1fr)_auto]": part.photo != null && at === 1,
            })}
          >
            {#if part.photo != null}
              <img
                class={cn(
                  "row-span-2 self-end rounded-full bg-muted object-cover object-top ring-2 ring-offset-2 ring-offset-card",
                  {
                    "block-11 inline-11 sm:block-14 sm:inline-14": isLead,
                    "block-11 inline-11": !isLead,
                  },
                  { "col-start-2": at === 1 },
                )}
                style:--tw-ring-color={part.color}
                src={part.photo.src}
                alt=""
                width="56"
                height="56"
              />
            {/if}
            <span
              class={cn(
                "font-figures leading-none font-[780] tracking-[-0.02em]",
                {
                  "text-[2rem] sm:text-[2.5rem]": isLead,
                  "text-[1.75rem]": !isLead,
                },
              )}>{bar.isCount ? count(part.value) : percent(part.value)}</span
            >
            <span
              class="flex items-baseline gap-1.5 text-[0.9375rem] font-semibold min-inline-0"
            >
              {#if isCrowded}
                <span
                  class="shrink-0 self-center rounded-xs block-2.5 inline-2.5"
                  style:background-color={part.color}
                ></span>
              {/if}
              <span
                class={cn({
                  truncate: !isLead || part.photo == null,
                  "line-clamp-2 leading-tight text-balance":
                    isLead && part.photo != null,
                })}>{part.label}</span
              >
              {#if part.delta !== undefined}
                <span
                  class={cn(
                    "shrink-0 font-figures text-[0.8125rem] font-normal",
                    {
                      "text-muted-foreground":
                        part.delta == null || part.delta === 0,
                    },
                  )}
                  >{part.delta == null
                    ? m.seats_change_new()
                    : part.delta === 0
                      ? "±0"
                      : `${part.delta > 0 ? "+" : "−"}${count(Math.abs(part.delta))}`}</span
                >
              {/if}
            </span>
          </p>
        {/each}
      </div>
      <div
        class={cn(
          "relative flex animate-sweep overflow-hidden bg-muted motion-reduce:animate-none",
          { "block-4": isLead, "block-3": !isLead },
        )}
        aria-hidden="true"
      >
        {#each bar.isCount ? [shown[0], ...bar.parts.filter((part) => !shown.includes(part)), shown[1]].filter((part) => part != null) : shown as part, at (part.key)}
          <span
            class={cn("block block-full", {
              "ms-auto": !bar.isCount && at === 1,
            })}
            style:inline-size="{(part.value / Math.max(bar.total, 0.0001)) *
              100}%"
            style:background-color={part.color}
          ></span>
        {/each}
        {#if bar.isWhole && bar.total > 1}
          <span
            class="absolute -inset-y-px bg-foreground inline-0.5"
            style:inset-inline-start="{((Math.floor(bar.total / 2) + 1) /
              bar.total) *
              100}%"
          ></span>
        {/if}
      </div>
    </div>
  {/if}
{/snippet}
