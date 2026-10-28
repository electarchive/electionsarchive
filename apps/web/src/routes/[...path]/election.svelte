<script lang="ts">
  import { resolve } from "$app/paths";
  import { page } from "$app/state";
  import ChevronDownIcon from "@lucide/svelte/icons/chevron-down";
  import {
    createQueries,
    createQuery,
    useQueryClient,
  } from "@tanstack/svelte-query";
  import { tick, untrack } from "svelte";
  import { MediaQuery, SvelteSet } from "svelte/reactivity";
  import {
    count,
    date,
    definite,
    percent,
    plural,
    points,
  } from "#lib/format.js";
  import Name from "#lib/components/name.svelte";
  import { MEASURES } from "#lib/outcomes.js";
  import { m } from "#lib/paraglide/messages.js";
  import { getLocale } from "#lib/paraglide/runtime.js";
  import { independent, swatch } from "#lib/party.js";
  import { orpc } from "#lib/rpc.js";
  import { cn } from "#lib/utils.js";
  import type { PageData } from "./$types";
  import Alongside from "./alongside.svelte";
  import Balance from "./balance.svelte";
  import ByElections from "./byelections.svelte";
  import Electors from "./electors.svelte";
  import Gains from "./gains.svelte";
  import Councils from "./councils.svelte";
  import Counties from "./counties.svelte";
  import Governments from "./governments.svelte";
  import Lead from "./lead.svelte";
  import Local from "./local.svelte";
  import PlaceMap from "./map.svelte";
  import Places from "./places.svelte";
  import Preview from "./preview.svelte";
  import Regions from "./regions.svelte";
  import Results from "./results.svelte";
  import Road from "./road.svelte";
  import Seated from "./seated.svelte";
  import Tiers from "./tiers.svelte";
  import Winners from "./winners.svelte";

  const {
    id,
    scope,
    election,
    cycle,
    sections,
  }: {
    id: string;
    scope: { id: string; path: string } | null;
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
    client.setQueryData(
      orpc.elections.alongside.queryKey({ input: { id } }),
      sections.alongside,
    );
  });

  const STATUSES: Record<string, () => string> = {
    scheduled: m.election_scheduled,
    postponed: m.election_postponed,
    canceled: m.election_canceled,
    annulled: m.election_annulled,
  };

  const ordered = $derived.by(() => {
    const grouped = Map.groupBy(election.races, (race) => race.scope)
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
    const groups = Map.groupBy(
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
      .filter((group) => {
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
      })
      .toArray();
    const totals = groups.flatMap((tiers) => {
      const base =
        tiers.find((race) => race.map != null) ??
        tiers.find((race) => race.count === 1) ??
        tiers[0];
      if (base == null) {
        return [];
      }
      const isCredited = tiers.some((race) => (race.credits ?? []).length > 0);
      const counted = isCredited
        ? (tiers.find((race) => race.kind === "list_allocation") ?? base)
        : base;
      const rows = tiers.flatMap((race) =>
        race.results.flatMap((row) => {
          const credits = row.key.startsWith("alliance:")
            ? (race.credits ?? []).filter(
                (credit) => credit.alliance === row.label,
              )
            : [];
          let left = row.seats;
          for (const credit of credits) {
            left = (left ?? 0) - credit.seats;
          }
          return [
            { row: { ...row, seats: left }, isBase: race === counted },
            ...credits.map((credit) => ({
              row: {
                ...row,
                key: credit.key,
                label: credit.label,
                short: credit.short,
                color: null,
                lean: null,
                person: null,
                party: credit.key.slice(6),
                seats: credit.seats,
              },
              isBase: false,
            })),
          ];
        }),
      );
      if (
        rows.some(
          ({ row }) =>
            row.key.startsWith("alliance:") &&
            (row.seats ?? 0) > 0 &&
            election.coalitions.every((entry) => entry.name !== row.label),
        )
      ) {
        return [];
      }
      const allied = new Map(
        rows.flatMap(({ row }) => {
          if (isCredited) {
            return [];
          }
          const coalition =
            row.key.startsWith("alliance:") && (row.seats ?? 0) > 0
              ? election.coalitions.find((entry) => entry.name === row.label)
              : null;
          return coalition == null
            ? []
            : coalition.parties.map((party) => [`party:${party}`, row.key]);
        }),
      );
      const folded = Map.groupBy(
        rows,
        (entry) => allied.get(entry.row.key) ?? entry.row.key,
      )
        .entries()
        .map(([key, entries]) => {
          const head =
            entries.find(
              (entry) =>
                entry.isBase &&
                entry.row.key === key &&
                entry.row.color != null,
            )?.row ??
            entries.find(
              (entry) => entry.row.key === key && entry.row.color != null,
            )?.row ??
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
        .filter((row) => row.votes > 0 || (row.seats ?? 0) > 0)
        .toArray()
        .toSorted(
          (a, b) => (b.seats ?? 0) - (a.seats ?? 0) || b.votes - a.votes,
        );
      return [
        {
          tiers,
          base,
          total: {
            ...base,
            anchor: `${tiers[0]?.anchor ?? base.anchor}-total`,
            drawn: base.anchor,
            path: null,
            kind: "body_seats",
            count: 1,
            sample: null,
            before: base.overall,
            holders: null,
            partners: [],
            runoff: null,
            results: folded,
            isTotal: true,
          },
        },
      ];
    });
    const lists = totals.flatMap(({ tiers }) =>
      tiers.filter((race) => race.kind === "list_allocation"),
    );
    const rest = sorted.filter((race) => !lists.includes(race));
    return rest.flatMap((race) => {
      const entry = totals.find(({ tiers }) => tiers.includes(race));
      const plain = {
        ...race,
        ...(race === entry?.base ? { map: null } : {}),
        isTotal: false,
      };
      if (entry == null) {
        return [plain];
      }
      const isFirst =
        rest.find((other) => entry.tiers.includes(other)) === race;
      const isLast =
        rest.findLast((other) => entry.tiers.includes(other)) === race;
      return [
        ...(isFirst ? [entry.total] : []),
        plain,
        ...(isLast
          ? entry.tiers
              .filter((other) => other.kind === "list_allocation")
              .map((other) => ({
                ...other,
                ...(other === entry.base ? { map: null } : {}),
                isTotal: false,
              }))
          : []),
      ];
    });
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

  const hexes = createQuery(() =>
    orpc.seats.hexes.queryOptions({ input: { id, within: scope?.id } }),
  );
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
  const regional = $state<Record<number, string>>({});
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
        scope == null &&
        race.sample != null &&
        race.map != null &&
        race.places != null &&
        index < 4,
      staleTime: Infinity,
      meta: { quiet: true },
    })),
  }));
  const worlds = createQueries(() => ({
    queries: ordered.map((race, index) => ({
      ...orpc.elections.regional.queryOptions({
        input: {
          id,
          contest: race.sample ?? race.anchor,
          level: "world.country",
        },
      }),
      enabled: regional[index] === "world.country",
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
            Date.parse(entry.start) >= held &&
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
                  const isAppointed = entry.category === "appointed";
                  const group =
                    entry.category === "elected"
                      ? null
                      : isAppointed
                        ? m.seats_appointed()
                        : m.seats_ex_officio();
                  return {
                    key:
                      group == null
                        ? (entry.party ?? "none")
                        : `category:${entry.category}`,
                    name: group ?? party?.label ?? m.board_after_other(),
                    label:
                      group ??
                      party?.short ??
                      party?.label ??
                      m.board_after_other(),
                    color: swatch(
                      group == null
                        ? (party?.color ?? null)
                        : independent(isAppointed ? 1 : 2),
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

  const named = (row: {
    label: string;
    short: string | null;
    person?: string | null;
  }) =>
    row.label.length > 28 && row.person == null
      ? (row.short ?? row.label)
      : row.label;
  const unfolded = new SvelteSet<string>();

  const races = $derived(
    ordered.map((race) => {
      const winners = race.results.filter((row) => (row.seats ?? 0) > 0);
      let total = 0;
      for (const row of winners) {
        total += row.seats ?? 0;
      }
      const loose = winners.filter(
        (row) =>
          row.party == null &&
          row.isIndependent === true &&
          row.key.startsWith("person:"),
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
        seated.some(
          (row) => row.key.startsWith("alliance:") && (row.seats ?? 0) > 0,
        ) ||
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
      const isTier =
        !race.isTotal &&
        ordered.some((other) => {
          if (other.body !== race.body) {
            return false;
          }
          if (other.isTotal) {
            return true;
          }
          if (
            other.kind === race.kind ||
            (other.round ?? 1) !== 1 ||
            other.size == null
          ) {
            return false;
          }
          let seats = 0;
          for (const row of other.results) {
            seats += row.seats ?? 0;
          }
          return seats >= other.size * 0.95 && seats <= other.size * 1.02;
        });
      const isPartial =
        !isTier &&
        whole != null &&
        race.count > 1 &&
        whole.total > total * 1.25;
      const top = whole?.parts.toSorted((a, b) => b.value - a.value) ?? [];
      const isRound =
        race.round != null &&
        race.count > 1 &&
        ordered.some(
          (other) =>
            other !== race &&
            other.name === race.name &&
            other.round !== race.round,
        );
      const isSplit =
        race.kind !== "list_allocation" &&
        race.count > 1 &&
        ordered.some(
          (other) =>
            other !== race && other.name === race.name && other.count === 1,
        );
      const isTallied = isSplit && districts.length === race.count;
      const noun = race.places?.levels[0]?.noun ?? "";
      const wards = race.wards ?? [];
      const sent = wards.filter((row) => row.runoff != null);
      const won = wards.filter((row) => row.runoff == null && row.isWon).length;
      const first = sent[0]?.runoff;
      const outright =
        wards.length === 0
          ? undefined
          : first == null
            ? won === wards.length
              ? m.wards_lede_all({ count: count(wards.length) })
              : null
            : m.wards_lede({
                won: count(won),
                count: count(wards.length),
                sent: count(sent.length),
                round: (first.isGeneral
                  ? m.wards_round_general
                  : m.wards_round_runoff)({
                  date: new Intl.DateTimeFormat(getLocale(), {
                    month: "long",
                    day: "numeric",
                    timeZone: "UTC",
                  }).format(new Date(`${first.date}T00:00:00Z`)),
                }),
              });
      const isPending = wards.every(
        (row) => row.share == null && !row.isWon && row.runoff == null,
      );
      const kinds = (
        isPending
          ? [
              {
                key: "ward:scheduled",
                label: m.map_ward_scheduled(),
                color: "#5f6b7a",
              },
            ]
          : [
              {
                key: "ward:outright",
                label: m.map_ward_outright(),
                color: "#0e7c86",
              },
              {
                key: "ward:runoff",
                label:
                  first?.isGeneral === true
                    ? m.map_ward_general()
                    : m.map_ward_runoff(),
                color: "#d08a00",
              },
            ]
      ).map((kind) => ({ ...kind, short: null, person: null, party: null }));
      const outcomes =
        wards.length === 0 || race.places == null
          ? null
          : {
              places: {
                ...race.places,
                legend: kinds,
                districts: undefined,
                rows: race.places.rows.map((row) => {
                  const ward = wards.find((entry) => entry.path === row.path);
                  const spot =
                    ward == null ? null : ward.runoff == null ? 0 : 1;
                  return {
                    ...row,
                    shares: kinds.map((_, at) =>
                      at === spot ? (ward?.share ?? 0) : 0,
                    ),
                    leader: spot,
                    also: null,
                    margin: null,
                    earlier: null,
                    prior: null,
                    declared: null,
                  };
                }),
              },
              tones: kinds.map((kind) => swatch(kind.color, kind.key)),
              titles: kinds.map((kind) => kind.label),
            };
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
      const turned =
        race.turnout == null
          ? null
          : race.turnout.voters / race.turnout.registered;
      const isShort =
        race.kind === "ballot_question" &&
        race.results[0]?.outcome === "lost" &&
        race.quorum != null &&
        turned != null &&
        turned <= race.quorum;
      return {
        ...race,
        decided,
        change,
        isTier,
        whole: isPartial ? whole : null,
        sequel:
          !isPartial || scope != null || top[0] == null || top[0].key === "none"
            ? null
            : top[1]?.value === top[0].value
              ? m.race_after_tied({
                  party: definite(top[0].name),
                  runner: definite(top[1].name),
                  seats: count(top[0].value),
                  total: count(whole.total),
                })
              : m.race_after({
                  party: definite(top[0].name),
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
                    person: row.person,
                    party: row.party,
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
                        label:
                          row.key.startsWith("alliance:") ||
                          row.key.startsWith("person:")
                            ? row.label
                            : (row.short ?? row.label),
                        color: swatch(row.color, row.key),
                        person: row.keys.length > 1 ? null : row.person,
                        party: row.keys.length > 1 ? null : row.party,
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
                    scope == null &&
                    (isTallied ||
                      (race.size != null && race.size <= total + 2)),
                }
              : {
                  total: 1,
                  parts: race.results
                    .toSorted(
                      (a, b) =>
                        (a.lean ?? 1e9) - (b.lean ?? 1e9) || b.share - a.share,
                    )
                    .map((row) => ({
                      key: row.key,
                      label:
                        row.person == null
                          ? (row.short ?? row.label)
                          : row.label,
                      color: swatch(row.color, row.key),
                      value: row.share,
                      person: row.person,
                      party: row.party,
                      photo: row.photo,
                    })),
                  isCount: false,
                  isWhole: false,
                },
        isLater: isRound && (race.round ?? 1) > 1,
        outcomes,
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
        isTally: election.isIndirect || race.isTally,
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
            outcome: row.outcome,
            isDeclined: row.isDeclined,
            isWriteIn:
              row.isWriteIn && !["won", "elected"].includes(row.outcome ?? ""),
            ballots: row.ballots,
            seats: row.seats,
            lines: [],
          },
          earlier: null,
          delta: null,
          backed: null,
          origin: null,
          photo: row.photo,
          nominated: row.nominated,
          supported: row.supported,
        })),
        lede:
          outright !== undefined
            ? outright
            : race.isPrimaries
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
                : leader == null || leader.key === "unrecorded"
                  ? null
                  : race.kind === "elector_slate" && seated.length > 0
                    ? m.race_lede_electors({
                        party: definite(named(leader), true),
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
                            party: definite(named(leader), true),
                            seats: count(
                              isTallied ? wins : (leader.seats ?? 0),
                            ),
                            total: count(race.count),
                            noun,
                            share: percent(leader.share),
                          })
                        : seated.length > 0 &&
                            ((race.isTotal && leader.share === 0) ||
                              (!race.isOffice &&
                                ((race.unopposed?.seats ?? 0) * 10 >= total ||
                                  (race.multiple ?? 0) > 0)))
                          ? m.race_lede_seats_only({
                              party: definite(named(leader), true),
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
                                party: definite(named(leader), true),
                                seats: count(leader.seats ?? 0),
                                total: count(
                                  race.size != null && race.size <= total + 2
                                    ? race.size
                                    : total,
                                ),
                                share: percent(leader.share),
                              })
                            : isShort
                              ? m.race_lede_quorum({
                                  turnout: percent(turned),
                                  quorum: percent(race.quorum ?? 0),
                                  share: percent(leader.share),
                                })
                              : race.kind === "ballot_question" &&
                                  race.results[0]?.outcome === "lost"
                                ? m.race_lede_lost({
                                    answer: named(leader),
                                    share: percent(leader.share),
                                  })
                                : runner == null
                                  ? null
                                  : (seated.length > 0 ||
                                      ["won", "elected"].includes(
                                        race.results[0]?.outcome ?? "",
                                      )
                                      ? m.race_lede_won
                                      : m.race_lede_votes)({
                                      party: definite(named(leader), true),
                                      share: percent(leader.share),
                                      margin: points(
                                        leader.share - runner.share,
                                      ).replace(/^\+/u, ""),
                                      runner: definite(named(runner)),
                                    }),
        next:
          race.runoff?.runner == null
            ? turned == null || isShort
              ? null
              : m.race_turnout({ share: percent(turned) })
            : (race.runoff.isWon
                ? m.race_lede_runoff_won
                : m.race_lede_runoff_led)({
                party:
                  [leader, runner].some(
                    (row) => row != null && named(row) === race.runoff?.label,
                  ) && race.runoff.short != null
                    ? race.runoff.short
                    : race.runoff.label,
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
                runner:
                  [leader, runner].some(
                    (row) => row != null && named(row) === race.runoff?.runner,
                  ) && race.runoff.runnerShort != null
                    ? race.runoff.runnerShort
                    : race.runoff.runner,
              }),
      };
    }),
  );
  const regionsAt = $derived(
    scope == null
      ? races.findIndex((race) => race.body != null && race.count > 1)
      : -1,
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
      {#if turnout.basis != null}
        <span class="text-[0.8125rem] text-muted-foreground"
          >{turnout.basis === "rejected"
            ? m.turnout_basis_rejected()
            : m.turnout_basis_valid()}</span
        >
      {/if}
    </p>
  {/if}
  {#if election.control != null}
    <p
      class="flex flex-wrap items-baseline gap-x-2.5 gap-y-0.5 text-[0.9375rem]"
    >
      <span class="inline-flex items-center gap-1.5 font-semibold">
        {#if election.control.key != null}
          <span
            class="shrink-0 rounded-xs block-2.5 inline-2.5"
            style:background={swatch(
              election.control.color,
              election.control.key,
            )}
          ></span>
        {/if}
        {election.control.label == null
          ? m.control_none({ total: count(election.control.total) })
          : m.control_party({
              party: election.control.label,
              seats: count(election.control.seats),
              total: count(election.control.total),
            })}
      </span>
      <span class="text-[0.8125rem] text-muted-foreground"
        >{election.control.isRolled === true
          ? m.control_rolled()
          : m.control_derived()}</span
      >
    </p>
  {/if}
</header>

{@render ticket()}

{#if election.composition != null}
  {const composition = $derived(election.composition)}
  <section class="col-span-12 grid gap-2 bg-card px-3.5 py-3">
    <h2 class="text-[0.8125rem] font-bold">{m.composition_title()}</h2>
    <table class="border-collapse text-sm inline-full sm:inline-auto">
      <thead>
        <tr class="border-be text-[0.8125rem] text-muted-foreground">
          <th class="pe-6 pbe-1.5 text-start font-normal" scope="col">
            {m.col_party()}
          </th>
          {#if composition.hasBefore}
            <th class="px-2 pbe-1.5 text-end font-normal" scope="col">
              {m.col_before()}
            </th>
          {/if}
          <th class="px-2 pbe-1.5 text-end font-normal" scope="col">
            {m.col_after()}
          </th>
          {#if composition.hasBefore}
            <th class="ps-2 pbe-1.5 text-end font-normal" scope="col">
              {m.col_change()}
            </th>
          {/if}
        </tr>
      </thead>
      <tbody>
        {#each composition.rows as row (row.key)}
          <tr class="border-be border-border/60 last:border-be-0">
            <th class="py-1.5 pe-6 text-start font-normal" scope="row">
              <span class="inline-flex items-center gap-1.5">
                <span
                  class="shrink-0 rounded-xs block-2.5 inline-2.5"
                  style:background={swatch(row.color, row.key)}
                ></span>
                {row.short ?? row.label}
              </span>
            </th>
            {#if composition.hasBefore}
              <td
                class="px-2 py-1.5 text-end font-figures text-muted-foreground tabular-nums"
              >
                {count(row.before ?? 0)}
              </td>
            {/if}
            <td
              class="px-2 py-1.5 text-end font-figures font-semibold tabular-nums"
            >
              {count(row.after)}
            </td>
            {#if composition.hasBefore}
              <td class="py-1.5 ps-2 text-end font-figures tabular-nums">
                {points((row.after - (row.before ?? 0)) / 100, 0)}
              </td>
            {/if}
          </tr>
        {/each}
      </tbody>
    </table>
  </section>
{/if}

{#if election.councils.length > 0}
  <Councils councils={election.councils} />
{/if}

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
  {#if index === 0 && !isBallot && race.results.length > 0 && (race.wards ?? []).length === 0 && (others.length > 0 || race.balance.parts.some((part) => part.value > 0))}
    {#snippet headline()}
      <div
        id="race-{race.anchor}"
        class="grid scroll-mbs-4 content-start gap-1.5 bg-card px-3.5 pbs-4 pbe-3"
      >
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
    {/snippet}
    {#snippet totals()}
      {@render tally(race, true)}
    {/snippet}
    <Lead results={headline} map={totals} />
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
      {#if race.lede != null && (index === 0 || race.isLater) && (race.wards ?? []).length === 0}
        <p
          class={cn("text-pretty max-inline-[64ch]", {
            "text-[1.0625rem]": index === 0,
            "text-[0.9375rem]": index > 0,
          })}
        >
          {race.lede}
        </p>
      {/if}
      {#if race.results.length === 0 && !race.isPrimaries && (race.wards ?? []).length === 0}
        {@render field(race)}
      {:else if isFolding && index > 0}
        <button
          type="button"
          class="mbs-1.5 mbe-2 flex items-center justify-center gap-1.5 rounded-sm border border-input bg-card text-[0.8125rem] font-semibold keycap block-10 hover:bg-secondary"
          aria-expanded={opened[index] === true}
          onclick={() => (opened[index] = opened[index] !== true)}
          >{race.results.length === 0 &&
          (race.wards ?? []).length > 0 &&
          (race.wards ?? []).every(
            (row) => row.share == null && row.runoff == null,
          )
            ? opened[index] === true
              ? m.race_hide_field()
              : m.race_show_field()
            : opened[index] === true
              ? m.race_hide()
              : m.race_show()}<ChevronDownIcon
            class={cn("transition-transform block-4 inline-4", {
              "rotate-180": opened[index] === true,
            })}
            aria-hidden="true"
          /></button
        >
      {/if}
      {#if (race.wards ?? []).length > 0 && (!isFolding || index === 0 || opened[index] === true)}
        {#if race.outcomes != null && race.map != null && (race.map.shaped ?? 0) * 2 >= race.outcomes.places.rows.length}
          <div class="grid gap-2 pbs-1">
            {#if race.map.tiles != null}
              <PlaceMap
                places={race.outcomes.places}
                colors={race.outcomes.tones}
                tiles={race.map.tiles}
                own={race.map.own}
                nested={race.map.nested}
                frame={race.map.frame}
                blank={race.map.blank ?? null}
                outlying={race.map.outlying ?? null}
                parted={race.map.parted ?? null}
                approximate={race.map.approximate}
                sources={race.map.credits}
                noun={race.places?.levels[0]?.noun ?? ""}
                votes={race.votes}
                names={race.outcomes.titles}
                levels={[]}
                span={null}
              />
            {:else}
              <p
                class="grid place-items-center border border-dashed border-border px-4 text-center text-sm text-pretty text-muted-foreground block-[min(24rem,50svh)]"
              >
                {m.map_rebuilding()}
              </p>
            {/if}
          </div>
        {/if}
        {@render wards(race.wards ?? [], race.anchor, race.lede)}
      {/if}
    </section>
  {/if}
  {#if (race.wards ?? []).length === 0 && (race.results.length > 1 || race.isPrimaries || (index === 0 && race.results.length > 0)) && (!isFolding || index === 0 || opened[index] === true)}
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
    {const target = $derived(
      "drawn" in race
        ? (races.find(
            (other) => !other.isTotal && other.anchor === race.drawn,
          ) ?? race)
        : race,
    )}
    {const explore = $derived(
      scope == null &&
        target.path == null &&
        target.count >= 3 &&
        races.every(
          (other) =>
            other.anchor.replace(/-\d+$/u, "") !==
              target.anchor.replace(/-\d+$/u, "") ||
            other.count <= target.count,
        )
        ? `${page.url.pathname.slice(1)}/${target.anchor}`
        : null,
    )}
    {#snippet mapped()}
      {#if race.map != null && race.places != null}
        {const option = $derived(regionals[index]?.data ?? null)}
        {const region = $derived(
          regional[index] === "world.country"
            ? (worlds[index]?.data ?? null)
            : regional[index] === option?.places.level
              ? option
              : null,
        )}
        {const swing = $derived(swings[index]?.data ?? null)}
        {const final = $derived(
          region == null && firstRound[index] !== true ? race.decided : null,
        )}
        {const places = $derived(
          region?.places ?? swing?.places ?? final?.places ?? race.places,
        )}
        {const shape = $derived(region?.map ?? race.map)}
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
        {#if shape.tiles != null}
          {#key places.level}
            <PlaceMap
              {places}
              {explore}
              colors={swing == null && region == null
                ? (final?.tones ?? race.tones)
                : places.legend.map((item) => swatch(item.color, item.key))}
              tiles={shape.tiles}
              hexes={hexes.data ?? null}
              own={shape.own}
              nested={shape.nested}
              frame={shape.frame}
              blank={shape.blank ?? null}
              outlying={shape.outlying ?? null}
              parted={shape.parted ?? null}
              approximate={shape.approximate}
              sources={shape.credits}
              noun={places.levels[0]?.noun ?? ""}
              isTally={race.isTally}
              tallies={race.electors == null ||
              region != null ||
              places.level !== race.places.level
                ? null
                : places.legend.map(
                    (item) =>
                      race.electors?.rows.find((row) => row.key === item.key)
                        ?.votes ?? null,
                  )}
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
                : [
                    ...[race.places, option.places].map((entry) => {
                      const noun = entry.levels[0]?.noun ?? "";
                      return {
                        code: entry.level,
                        label: noun.charAt(0).toUpperCase() + noun.slice(1),
                        count: entry.rows.length,
                        path: "",
                        query: "",
                        isCurrent: entry.level === places.level,
                      };
                    }),
                    ...(option.world == null
                      ? []
                      : [
                          {
                            code: "world.country",
                            label: "",
                            count: option.world,
                            path: "",
                            query: "",
                            isCurrent: places.level === "world.country",
                          },
                        ]),
                  ]}
              home={election.breadcrumbs[0]?.name ?? null}
              pick={(code: string) => (regional[index] = code)}
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
        {:else}
          <p
            class="grid place-items-center border border-dashed border-border px-4 text-center text-sm text-pretty text-muted-foreground block-[min(24rem,50svh)]"
          >
            {m.map_rebuilding()}
          </p>
        {/if}
        {const districts = $derived(
          new Set(
            places.rows
              .filter((row) => row.isPool !== true)
              .map((row) => row.k),
          ).size,
        )}
        {#if shape.shaped != null && shape.shaped < districts}
          <p class="px-1 pbs-1 text-xs text-pretty text-muted-foreground">
            {m.map_shaped({
              shaped: count(shape.shaped),
              count: count(districts),
            })}
          </p>
        {/if}
      {/if}
    {/snippet}
    {#snippet summary()}
      <div
        class="@container grid content-start gap-1 bg-card px-1.5 pbs-2 pbe-2.5"
      >
        <Results
          rows={race.rows}
          trace={race.count === 1 ? race.path : null}
          span={null}
          votes={race.votes}
          isTally={race.isTally}
          nouns={plural(race.places?.levels[0]?.noun ?? "contest")}
          seats={race.isLone || (race.isOffice && race.electors == null)
            ? null
            : race.electors == null && !race.isSlate
              ? m.col_seats()
              : m.col_electors()}
        />
        {#if race.unopposed != null}
          <p class="px-1.5 pbs-1 text-[0.8125rem] text-muted-foreground">
            {m.race_unopposed({
              seats: race.unopposed.seats,
              parties: new Intl.ListFormat(getLocale(), {
                type: "conjunction",
              }).format(
                race.unopposed.parties.map((party) =>
                  m.race_unopposed_party({
                    party: party.label === "" ? m.record_none() : party.label,
                    seats: count(party.seats),
                  }),
                ),
              ),
            })}
          </p>
        {/if}
        {#if race.covered != null && race.size != null}
          <p class="px-1.5 pbs-1 text-[0.8125rem] text-muted-foreground">
            {m.race_partial_seats({
              count: count(race.covered),
              total: count(race.size),
            })}
          </p>
        {/if}
        {#if (race.multiple ?? 0) > 0}
          <p class="px-1.5 text-[0.8125rem] text-muted-foreground">
            {m.race_multiple({
              count: race.multiple ?? 0,
              noun: race.places?.levels[0]?.noun ?? "constituency",
              nouns: plural(race.places?.levels[0]?.noun ?? "constituency"),
            })}
          </p>
        {/if}
        {#each race.repeats ?? [] as repeat, index (index)}
          <p class="px-1.5 text-[0.8125rem] text-muted-foreground">
            {repeat.people.length === 1
              ? m.race_repeat_one({
                  name: repeat.people[0]?.name ?? "",
                  count: count(repeat.people[0]?.returns ?? 0),
                  party: definite(repeat.label),
                  seats: count(repeat.seats),
                  members: count(repeat.members),
                })
              : m.race_repeat_many({
                  names: new Intl.ListFormat(getLocale(), {
                    style: "long",
                    type: "conjunction",
                  }).format(repeat.people.map((person) => person.name)),
                  party: definite(repeat.label),
                  seats: count(repeat.seats),
                  members: count(repeat.members),
                })}
          </p>
        {/each}
      </div>
    {/snippet}
    {#snippet hemicycle()}
      <Seated
        parties={race.seated}
        places={race.holders}
        body={race.body}
        size={race.isTier || scope != null ? null : race.size}
        date={election.date}
        place={race.name}
        region={election.parent == null
          ? null
          : (election.breadcrumbs
              .filter((crumb) => crumb.path !== election.parent?.path)
              .at(-1)?.name ?? null)}
        change={race.change}
        isComplete={scope == null &&
          !race.isTier &&
          (race.size != null || race.count === 1)}
        partners={race.isTier ? [] : race.partners}
        governing={race.size === chamber?.size ? governing : []}
        isStaggered={race.isStaggered}
        unelected={race.unelected ?? []}
      />
    {/snippet}
    <Lead
      results={race.isPrimaries ? null : summary}
      map={isMapped && race.map != null && race.places != null ? mapped : null}
      aside={isSeated ? hemicycle : null}
    />
    {#if race.isNotional === true && race.change != null && race.since != null}
      <p class="col-span-12 bg-card px-3.5 py-2 text-xs text-muted-foreground">
        {m.seats_change_notional({ year: race.since })}
      </p>
    {/if}
    {#if race.resized != null && race.change != null && race.since != null && race.size != null}
      <p class="col-span-12 bg-card px-3.5 py-2 text-xs text-muted-foreground">
        {m.seats_change_resized({
          year: race.since,
          before: count(race.resized),
          now: count(race.size),
        })}
      </p>
    {/if}
    {#if (race.straddled ?? 0) > 0}
      <p class="col-span-12 bg-card px-3.5 py-2 text-xs text-muted-foreground">
        {m.seats_change_straddled()}
      </p>
    {/if}
    {#if race.places != null && !race.isTotal}
      {const shown = $derived(
        regional[index] === "world.country"
          ? (worlds[index]?.data?.places ?? race.places)
          : regional[index] != null &&
              regional[index] === regionals[index]?.data?.places.level
            ? regionals[index].data.places
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
        isTally={race.isTally}
        isMapped={isMapped ||
          races.some(
            (other) =>
              "drawn" in other &&
              other.drawn === race.anchor &&
              other.map != null,
          )}
        limit={isBallot ? 5 : 10}
      />
    {/if}
  {/if}
  {#if index === regionsAt}
    <Regions {id} />
  {/if}
  {#if index === 0 && others.some((other) => other.results.length > 0 || other.lede != null || other.isPrimaries) && !isBallot}
    {@render board()}
  {/if}
{/each}

{#snippet ticket()}
  {const isRoad = $derived(cycle.primaries.length > 0)}
  {#if stages.length > 0 && !isRoad}
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

  <Road
    rounds={[]}
    after={isRoad
      ? stages.map((child) => ({
          id: child.path ?? child.name,
          label: child.name.replace(/^\d{4} /u, ""),
          date: child.date,
          path: child.path,
        }))
      : []}
    cycle={{
      primaries: cycle.primaries.map((primary) => ({
        ...primary,
        isMinor: false,
        runoff: null,
        local: null,
      })),
      general:
        cycle.general == null
          ? null
          : {
              ...cycle.general,
              id: cycle.general.path ?? "",
              until: null,
              party: null,
              isMinor: false,
              winners: [],
              runoff: null,
              local: null,
            },
    }}
  >
    {#snippet more()}
      {#if cycle.parts.length > 0}
        <details class="text-[0.8125rem]">
          <summary
            class="cursor-pointer text-muted-foreground marker:text-border hover:text-foreground"
          >
            {m.cycle_parts()}
            <span class="font-figures tabular-nums"
              >{count(cycle.parts.length)}</span
            >
          </summary>
          <ul class="mbs-1.5 grid gap-x-6 gap-y-1 xl:grid-cols-2">
            {#each cycle.parts as part (part.place)}
              <li class="grid gap-x-3 sm:grid-cols-[9.5rem_minmax(0,1fr)]">
                <span class="truncate font-medium">{part.place}</span>
                <span class="flex flex-wrap gap-x-2.5">
                  {#each part.races as race, index (index)}
                    {const label = $derived(
                      [
                        race.date == null
                          ? null
                          : monthDay.format(new Date(`${race.date}T00:00:00Z`)),
                        race.party?.short ?? race.party?.label ?? null,
                      ]
                        .filter((piece) => piece != null)
                        .join(", "),
                    )}
                    {#if race.path == null || race.isCurrent}
                      <span class={cn({ "font-semibold": race.isCurrent })}
                        >{label}</span
                      >
                    {:else}
                      <Preview
                        class="underline decoration-border underline-offset-4 hover:decoration-foreground"
                        path={race.path}>{label}</Preview
                      >
                    {/if}
                  {/each}
                </span>
              </li>
            {/each}
          </ul>
        </details>
      {/if}
    {/snippet}
  </Road>
{/snippet}

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
                    !other.isPrimaries &&
                    (other.wards ?? []).length === 0,
                })}
              >
                {@render lede(other, false)}
              </div>
            {/if}
          </div>
          {#if other.results.length > 0 && (other.wards ?? []).length === 0}
            {@render tally(other, false)}
          {:else if other.results.length === 0 && !other.isPrimaries}
            {@render field(other)}
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

{#snippet wards(
  rows: NonNullable<(typeof races)[number]["wards"]>,
  anchor: string,
  text: string | null,
)}
  {const round = (runoff: { date: string; isGeneral: boolean }) => {
    const day = new Intl.DateTimeFormat(getLocale(), {
      month: "long",
      day: "numeric",
      timeZone: "UTC",
    }).format(new Date(`${runoff.date}T00:00:00Z`));
    return runoff.isGeneral
      ? m.wards_round_general({ date: day })
      : m.wards_round_runoff({ date: day });
  }}
  <div class="grid content-start gap-2 pbs-1 pbe-3">
    <p class="text-[0.9375rem] text-pretty max-inline-[64ch]">
      {text}
    </p>
    <ol
      class="grid grid-cols-[repeat(auto-fill,minmax(min(100%,19rem),1fr))] gap-x-6 text-sm"
    >
      {#each unfolded.has(anchor) || rows.length <= 20 ? rows : rows.slice(0, 18) as row, index (index)}
        {const final = $derived(row.runoff?.isWon === true ? row.runoff : null)}
        <li
          class="grid grid-cols-[fit-content(45%)_minmax(0,1fr)] items-baseline gap-x-3 border-be border-border/60 py-1.5 pointer-coarse:py-2"
        >
          {#if row.path == null}
            <span class="text-[0.8125rem] text-muted-foreground"
              >{row.label}</span
            >
          {:else}
            <a
              class="text-[0.8125rem] text-muted-foreground underline decoration-border underline-offset-2 hover:text-foreground hover:decoration-foreground"
              href={resolve("/[...path]", { path: row.path })}>{row.label}</a
            >
          {/if}
          <span class="grid gap-0.5 min-inline-0">
            {#if row.share == null && row.field.length > 0}
              <span class="grid gap-0.5">
                {const shown = $derived(
                  row.field.filter(
                    (entry, place) => place < 3 || entry.isIncumbent === true,
                  ),
                )}
                {#each shown as entry (entry.person)}
                  <span
                    class="flex flex-wrap items-baseline gap-x-1 min-inline-0"
                  >
                    <Name
                      class="font-semibold"
                      label={entry.name}
                      person={entry.person}
                    />
                    {#if entry.party != null}
                      <span class="text-xs text-muted-foreground"
                        >{entry.party}</span
                      >
                    {/if}
                    {#if entry.isIncumbent === true}
                      <span class="highlighter px-1 text-xs font-semibold"
                        >{m.slate_incumbent()}</span
                      >
                    {/if}
                  </span>
                {/each}
                {#if row.field.every((entry) => entry.isIncumbent === false)}
                  <span
                    class="justify-self-start highlighter px-1 text-xs font-semibold"
                    >{m.incumbency_open()}</span
                  >
                {/if}
                {#if row.field.length > shown.length}
                  <span class="text-xs text-muted-foreground"
                    >{m.wards_field_more({
                      count: count(row.field.length - shown.length),
                    })}</span
                  >
                {/if}
              </span>
            {/if}
            {#if row.share != null}
              <Name
                class="justify-self-start font-semibold"
                label={(final ?? row).name ?? ""}
                person={(final ?? row).person}
              />
            {/if}
            <span
              class={cn("text-xs text-muted-foreground", {
                hidden: row.share == null && row.field.length > 0,
              })}
            >
              {#if row.share == null}
                {m.wards_none()}
              {:else if row.runoff == null}
                {(row.isWon ? m.wards_won : m.wards_led)({
                  share: percent(row.share, 1),
                })}
              {:else if final == null}
                {m.wards_pending({
                  share: percent(row.share, 1),
                  round: round(row.runoff),
                })}
              {:else if final.person != null && final.person === row.person}
                {m.wards_kept({
                  share: percent(row.share, 1),
                  round: round(row.runoff),
                  final: percent(final.share, 1),
                })}
              {:else}
                {m.wards_took({
                  round: round(row.runoff),
                  final: percent(final.share, 1),
                  name: row.name ?? "",
                  share: percent(row.share, 1),
                })}
              {/if}
            </span>
          </span>
        </li>
      {/each}
    </ol>
    {#if rows.length > 20}
      <button
        type="button"
        class="justify-self-start text-[0.8125rem] font-semibold underline decoration-border underline-offset-4 hover:decoration-foreground pointer-coarse:py-2"
        aria-expanded={unfolded.has(anchor)}
        onclick={() => {
          if (unfolded.has(anchor)) {
            unfolded.delete(anchor);
          } else {
            unfolded.add(anchor);
          }
        }}
        >{unfolded.has(anchor)
          ? m.regions_fewer()
          : m.regions_more({ count: count(rows.length) })}</button
      >
    {/if}
  </div>
{/snippet}

{#snippet field(race: (typeof races)[number])}
  {#if race.field.length === 0}
    <p class="text-[0.9375rem] text-muted-foreground">{m.no_results()}</p>
  {:else}
    <ul class="grid gap-1 pbe-2 text-[0.9375rem]">
      {#each race.field as entry (entry.person)}
        <li class="flex flex-wrap items-baseline gap-x-1.5">
          <Name
            class="font-semibold"
            label={entry.name}
            person={entry.person}
          />
          {#if entry.party != null}
            <span class="text-[0.8125rem] text-muted-foreground"
              >{entry.party}</span
            >
          {/if}
          {#if entry.isIncumbent === true}
            <span class="highlighter px-1 text-xs font-semibold"
              >{m.slate_incumbent()}</span
            >
          {/if}
        </li>
      {/each}
      {#if race.field.every((entry) => entry.isIncumbent === false)}
        <li>
          <span class="highlighter px-1 text-xs font-semibold"
            >{m.incumbency_open()}</span
          >
        </li>
      {/if}
    </ul>
  {/if}
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
        path={race.path}
        >{race.results.length === 0 && race.field.length > 0
          ? m.race_open_scheduled()
          : m.race_open()}</Preview
      >
    {/if}
    {#if race.first?.path != null}
      <Preview
        class="underline decoration-border underline-offset-4 hover:decoration-foreground"
        path={race.first.path}>{m.round_first()}</Preview
      >
    {/if}
    {#if race.pending != null}
      <span
        >{(race.pending.isGeneral ? m.race_next_general : m.race_next_runoff)({
          date: date(race.pending.date),
        })}</span
      >
    {/if}
  </p>
{/snippet}

{#snippet tally(race: (typeof races)[number], isLead: boolean)}
  <div class="grid content-end gap-3">
    {#if race.whole == null}
      <Balance {...race.balance} {isLead} />
    {:else}
      <div class="grid gap-1">
        <p class="text-[0.8125rem] text-muted-foreground">
          {m.board_won()}
        </p>
        <Balance {...race.balance} {isLead} />
      </div>
      <div class="grid gap-1">
        <p class="text-[0.8125rem] text-muted-foreground">
          {scope == null
            ? m.board_after()
            : m.board_after_whole({
                chamber: race.name,
                seats: count(race.whole.total),
              })}
        </p>
        <Balance
          parts={race.whole.parts.map((part) => ({
            ...part,
            party: part.key === "none" ? null : part.key,
          }))}
          total={race.whole.total}
          isWhole={race.whole.isWhole}
          isCount
        />
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

{#if sections.local != null}
  <Local local={sections.local} isOwn={true} />
{/if}
{#if sections.counties != null}
  <Counties counties={sections.counties} />
{/if}
{#if scope == null}
  <Alongside {id} />
  <Governments {id} {chamber} />
  {#if regionsAt === -1}
    <Regions {id} />
  {/if}
{/if}
<Gains {id} within={scope?.id} />
<ByElections {id} within={scope?.id} />

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
  {#if election.seated != null}
    {const seated = $derived(election.seated)}
    <section class="col-span-12 grid gap-2 bg-card px-3.5 py-3">
      <div class="flex flex-wrap items-baseline justify-between gap-x-4">
        <h2 class="text-[0.8125rem] font-bold">{m.seats_title()}</h2>
        <p class="text-[0.8125rem] text-muted-foreground">
          {m.seats_filled({ count: count(seated.total) })}
        </p>
      </div>
      <table class="border-collapse text-sm inline-full sm:inline-auto">
        <thead>
          <tr class="border-be text-[0.8125rem] text-muted-foreground">
            <th class="pe-6 pbe-1.5 text-start font-normal" scope="col">
              {m.col_party()}
            </th>
            <th class="ps-2 pbe-1.5 text-end font-normal" scope="col">
              {m.col_seats()}
            </th>
          </tr>
        </thead>
        <tbody>
          {#each seated.parties as row, index (index)}
            <tr class="border-be border-border/60 last:border-be-0">
              <th class="py-1.5 pe-6 text-start font-normal" scope="row">
                <span class="inline-flex items-center gap-1.5">
                  <span
                    class="shrink-0 rounded-xs block-2.5 inline-2.5"
                    style:background={swatch(row.color, `party:${row.label}`)}
                  ></span>
                  {row.isIndependent
                    ? m.seats_independents()
                    : (row.short ?? row.label)}
                </span>
              </th>
              <td
                class="py-1.5 ps-2 text-end font-figures font-semibold tabular-nums"
              >
                {count(row.seats)}
              </td>
            </tr>
          {/each}
          {#if seated.others != null}
            <tr>
              <th
                class="py-1.5 pe-6 text-start font-normal text-muted-foreground"
                scope="row"
              >
                {m.election_seated_others({
                  count: count(seated.others.count),
                })}
              </th>
              <td
                class="py-1.5 ps-2 text-end font-figures text-muted-foreground tabular-nums"
              >
                {count(seated.others.seats)}
              </td>
            </tr>
          {/if}
        </tbody>
      </table>
    </section>
  {/if}
  <section class="col-span-12 bg-card px-3.5 py-3">
    <h2 class="mbe-2 text-[0.8125rem] font-bold">
      {partNoun == null
        ? m.election_children()
        : m.election_places({ noun: partNoun })}
    </h2>
    <ul class="grid grid-cols-2 gap-x-6 text-sm sm:grid-cols-3 lg:grid-cols-4">
      {#each parts as child, index (index)}
        <li
          class="flex flex-wrap items-baseline justify-between gap-x-3 border-be border-border/60 py-1.5 min-inline-0"
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
          {#if child.lead != null && child.seats != null}
            <span class="truncate text-xs text-muted-foreground inline-full">
              {child.lead.label === ""
                ? m.seats_independents()
                : child.lead.label}
              <span class="font-figures tabular-nums"
                >{count(child.lead.seats)}
                {m.seats_of({ count: count(child.seats) })}</span
              >
            </span>
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
