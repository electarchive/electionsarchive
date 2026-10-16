<script lang="ts">
  import {
    createQueries,
    createQuery,
    useQueryClient,
  } from "@tanstack/svelte-query";
  import { untrack } from "svelte";
  import { count, date, percent } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";
  import { orpc } from "#lib/rpc.js";
  import { cn } from "#lib/utils.js";
  import type { PageData } from "./$types";
  import Electors from "./electors.svelte";
  import Governments from "./governments.svelte";
  import PlaceMap from "./map.svelte";
  import Places from "./places.svelte";
  import Preview from "./preview.svelte";
  import Regions from "./regions.svelte";
  import Results, { MEASURES } from "./results.svelte";
  import Seats from "./seats.svelte";
  import Tiers from "./tiers.svelte";

  const {
    id,
    election,
    sections,
  }: {
    id: string;
    election: Extract<PageData, { kind: "election" }>["election"];
    sections: Extract<PageData, { kind: "election" }>["sections"];
  } = $props();

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
      parts.every((child) => /^us\/[a-z]{2}\//.test(child.path ?? "")),
  );
  const partNoun = $derived(
    parts.every((child) => child.noun != null && child.noun === parts[0]?.noun)
      ? parts[0]?.noun
      : null,
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
    ...new Set(election.races.flatMap((race) => race.body ?? [])),
  ]);
  const swings = createQueries(() => ({
    queries: election.races.map((race, index) => {
      const path = race.path ?? "";
      const by = race.places?.level;
      return {
        queryKey: ["swing", path, by],
        enabled:
          race.path != null &&
          race.count === 1 &&
          race.map != null &&
          race.places != null &&
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
    const entries = election.races
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
    election.races.map((race) => {
      const winners = race.results.filter((row) => (row.seats ?? 0) > 0);
      let total = 0;
      for (const row of winners) {
        total += row.seats ?? 0;
      }
      const seated =
        race.count === 1 && total <= 1 && race.kind !== "elector_slate"
          ? []
          : winners;
      const leader =
        seated.toSorted((a, b) => (b.seats ?? 0) - (a.seats ?? 0))[0] ??
        race.results[0];
      const runner = race.results.find((row) => row.key !== leader?.key);
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
      const legend = race.places?.legend ?? [];
      const before = new Map(
        (race.before ?? []).map((entry) => [entry.key, entry.seats]),
      );
      const change =
        race.before == null ||
        seated.some((row) => row.key.startsWith("alliance:"))
          ? null
          : Object.fromEntries(
              seated.flatMap((row) => {
                const earlier = before.get(row.key);
                return earlier == null
                  ? []
                  : [[row.key, (row.seats ?? 0) - earlier]];
              }),
            );
      const ordered = seated.toSorted(
        (a, b) =>
          (a.lean ?? 1e9) - (b.lean ?? 1e9) || (b.seats ?? 0) - (a.seats ?? 0),
      );
      const whole =
        race.body == null || race.electors != null
          ? null
          : after.get(race.body);
      const isPartial =
        whole != null && race.count > 1 && whole.total > total * 1.25;
      const top = whole?.parts.toSorted((a, b) => b.value - a.value) ?? [];
      return {
        ...race,
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
                parts: race.electors.rows.map((row) => ({
                  key: row.key,
                  label:
                    row.person == null ? (row.short ?? row.label) : row.label,
                  color: swatch(row.color, row.key),
                  value: row.votes,
                })),
                isCount: true,
                isWhole: true,
              }
            : seated.length > 1 || (seated.length > 0 && race.count > 1)
              ? {
                  total:
                    race.size != null && race.size <= total + 2
                      ? race.size
                      : total,
                  parts: ordered.map((row) => ({
                    key: row.key,
                    label: row.key.startsWith("alliance:")
                      ? row.label
                      : (row.short ?? row.label),
                    color: swatch(row.color, row.key),
                    value: row.seats ?? 0,
                    delta:
                      change == null ? undefined : (change[row.key] ?? null),
                  })),
                  isCount: true,
                  isWhole: race.size != null && race.size <= total + 2,
                }
              : {
                  total: 1,
                  parts: race.results
                    .slice(0, 2)
                    .map((row) => ({
                      key: row.key,
                      label:
                        row.person == null
                          ? (row.short ?? row.label)
                          : row.label,
                      color: swatch(row.color, row.key),
                      value: row.share,
                    })),
                  isCount: false,
                  isWhole: false,
                },
        name:
          race.round != null &&
          election.races.some(
            (other) => other !== race && other.name === race.name,
          )
            ? m.race_round({ name: race.name, round: race.round })
            : race.name,
        votes: MEASURES[race.measure]?.() ?? m.col_votes(),
        isSlate: race.kind === "elector_slate",
        seated: seated.map((row) => ({
          ...row,
          seats: row.seats ?? 0,
          here: 0,
          members: [],
        })),
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
          },
          earlier: null,
          delta: null,
          backed: null,
          origin: null,
        })),
        lede:
          elector != null
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
                  : seated.length > 0 && !race.isOffice
                    ? m.race_lede_seats({
                        party: named(leader),
                        seats: count(leader.seats ?? 0),
                        total: count(
                          race.size != null && race.size <= total + 2
                            ? race.size
                            : total,
                        ),
                        share: percent(leader.share),
                      })
                    : runner == null
                      ? null
                      : (seated.length > 0
                          ? m.race_lede_won
                          : m.race_lede_votes)({
                          party: named(leader),
                          share: percent(leader.share),
                          margin: count(
                            Math.round((leader.share - runner.share) * 1000) /
                              10,
                          ),
                          runner: named(runner),
                        }),
      };
    }),
  );
</script>

<header class="col-span-12 grid gap-1.5 bg-card px-3.5 pbs-4 pbe-3.5">
  <h1
    class="font-serif text-[clamp(1.75rem,3vw,2.5rem)] leading-[1.02] font-[640] tracking-[-0.01em] text-balance"
  >
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
  {#if races.length > 1}
    <ul class="mbs-2 grid gap-2 max-inline-[72ch]">
      {#each races.slice(0, 3) as race, index (index)}
        {#if race.lede != null}
          <li class="grid gap-0.5 border-s-2 border-border ps-3">
            <a
              class="text-[0.8125rem] font-semibold text-muted-foreground hover:text-foreground"
              href="#race-{index}">{race.name}</a
            >
            <span
              class="font-serif text-[clamp(1.0625rem,1.6vw,1.25rem)]/snug text-pretty"
              >{race.sequel ?? race.lede}</span
            >
          </li>
        {/if}
      {/each}
    </ul>
  {/if}
</header>

{#if races.length === 0}
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

{#if races.length > 1}
  <section
    class="col-span-12 grid gap-3 bg-card px-3.5 pbs-3.5 pbe-4"
    aria-labelledby="board-title"
  >
    <h2 id="board-title" class="text-[0.8125rem] font-bold">
      {m.board_title({ count: races.length })}
    </h2>
    <ol
      class={cn(
        "grid gap-px overflow-hidden rounded-sm bg-border ring-1 ring-border",
        {
          "sm:grid-cols-2": races.length >= 2 && races.length <= 4,
          "sm:*:last:col-span-2 lg:grid-cols-3 lg:*:last:col-span-1":
            races.length === 3,
          "lg:grid-cols-4": races.length === 4,
          "grid-cols-[repeat(auto-fill,minmax(min(100%,17rem),1fr))]":
            races.length > 4,
        },
      )}
    >
      {#each races as race, index (index)}
        <li class="grid">
          <a
            class="group grid content-start gap-2.5 bg-card p-3 hover:bg-secondary/60"
            href="#race-{index}"
          >
            <span class="flex items-baseline justify-between gap-3">
              <span
                class="font-serif text-lg/tight font-[640] text-balance group-hover:underline group-hover:decoration-border group-hover:underline-offset-4"
                >{race.name}</span
              >
              {#if race.count > 1}
                <span
                  class="shrink-0 font-mono text-xs text-muted-foreground tabular-nums"
                  >{race.count}</span
                >
              {/if}
            </span>
            {@render balance(race.balance)}
            {#if race.whole != null}
              <span class="grid gap-1.5 border-bs border-border/60 pbs-2">
                <span class="text-xs text-muted-foreground"
                  >{m.board_after()}</span
                >
                {@render balance({ ...race.whole, isCount: true })}
              </span>
            {/if}
          </a>
        </li>
      {/each}
    </ol>
  </section>
{/if}

{#each races as race, index (index)}
  <section
    id="race-{index}"
    class="col-span-12 grid scroll-mbs-4 gap-1.5 bg-card px-3.5 pbs-4 pbe-1"
  >
    <div class="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
      <h2
        class={cn("font-serif font-[640] tracking-[-0.005em] text-balance", {
          "text-[clamp(1.5rem,2.4vw,2rem)]/tight": index === 0,
          "text-2xl/tight": index > 0,
        })}
      >
        {race.name}
      </h2>
      <p
        class="flex items-baseline gap-3 text-[0.8125rem] text-muted-foreground"
      >
        {#if race.count > 1}
          <span>{m.race_contests({ count: race.count })}</span>
        {/if}
        {#if race.path != null}
          <Preview
            class="font-semibold text-foreground underline decoration-border underline-offset-4 hover:decoration-foreground"
            path={race.path}>{m.race_open()}</Preview
          >
        {/if}
      </p>
    </div>
    {#if race.lede != null && (races.length === 1 || index >= 3 || race.sequel != null)}
      <p
        class={cn("text-pretty max-inline-[64ch]", {
          "text-[1.0625rem]": index === 0,
          "text-[0.9375rem]": index > 0,
        })}
      >
        {race.lede}
      </p>
    {/if}
  </section>
  {#if race.electors != null && race.electors.rows.length > 0}
    <Electors electors={race.electors} />
  {/if}
  {const isSeated = $derived(
    race.seated.length > 0 &&
      race.electors == null &&
      !race.isOffice &&
      !race.isSlate,
  )}
  {const isMapped = $derived(
    race.map != null && race.places != null && index < 4,
  )}
  {#if isMapped && race.map != null && race.places != null}
    <div
      class={cn("col-span-12 bg-card px-3.5 pbs-3 pbe-3.5 min-inline-0", {
        "lg:col-span-8": isSeated,
        "lg:col-span-7 xl:col-span-8": !isSeated,
      })}
    >
      {const swing = $derived(swings[index]?.data ?? null)}
      {const places = $derived(swing?.places ?? race.places)}
      <PlaceMap
        {places}
        colors={swing == null
          ? race.tones
          : places.legend.map((item) => swatch(item.color, item.key))}
        tiles={race.map.tiles}
        own={race.map.own}
        frames={race.map.frames}
        isApproximate={race.map.isApproximate}
        noun={race.places.levels[0]?.noun ?? ""}
        votes={race.votes}
        names={swing == null
          ? race.titles
          : places.legend.map((item) =>
              item.person == null ? (item.short ?? item.label) : item.label,
            )}
        levels={[]}
        span={swing?.span ??
          (race.count > 1 &&
          race.since != null &&
          race.places.rows.some((row) => row.prior != null)
            ? {
                from: race.since,
                to: election.date?.slice(0, 4) ?? "",
                forward: false,
              }
            : null)}
      />
    </div>
  {/if}
  {#if isSeated}
    <Seats
      parties={race.seated}
      places={[]}
      here={null}
      place={race.name}
      link={null}
      size={race.size}
      change={race.change}
      isComplete={race.size != null || race.count === 1}
      partners={race.partners}
      governing={race.size === chamber?.size ? governing : []}
      chamber={race.body}
    />
  {/if}
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
      seats={race.seated.length === 0 ||
      (race.isOffice && race.electors == null)
        ? null
        : race.electors == null && !race.isSlate
          ? m.col_seats()
          : m.col_electors()}
    />
  </div>
  {#if race.places != null}
    <Places
      places={{
        ...race.places,
        levels: race.places.levels.filter(
          (level) => level.code === race.places?.level,
        ),
      }}
      span={null}
      trace={race.count === 1 ? race.path : null}
      publishers={[]}
      votes={race.votes}
      {isMapped}
      limit={15}
    />
  {/if}
{/each}

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
            class="shrink-0 font-mono text-xs text-muted-foreground"
            datetime={child.date}>{date(child.date)}</time
          >
        </li>
      {/each}
    </ul>
  </section>
{/if}

<Governments {id} {chamber} />
<Regions {id} />

{#if isTiled}
  <Tiers
    {id}
    {parts}
    noun={partNoun ?? ""}
    race={election.races.find((race) => race.electors != null)?.name ?? null}
  />
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
              class="shrink-0 font-mono text-xs text-muted-foreground"
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

{#snippet balance(bar: {
  total: number;
  parts: Array<{
    key: string;
    label: string;
    color: string;
    value: number;
    delta?: number | null;
  }>;
  isCount: boolean;
  isWhole: boolean;
})}
  {const shown = $derived(
    bar.parts
      .filter((part) => part.value > 0)
      .toSorted((a, b) => b.value - a.value)
      .slice(0, 2)
      .toSorted((a, b) => bar.parts.indexOf(a) - bar.parts.indexOf(b)),
  )}
  <span class="grid gap-1.5">
    <span class="flex items-baseline justify-between gap-3 text-[0.8125rem]">
      {#each shown as part, at (part.key)}
        <span
          class={cn("flex items-baseline gap-1.5 min-inline-0", {
            "flex-row-reverse text-end": at === 1,
          })}
        >
          <span class="truncate font-semibold">{part.label}</span>
          <span class="shrink-0 font-mono text-xs tabular-nums"
            >{bar.isCount ? count(part.value) : percent(part.value)}</span
          >
          {#if part.delta !== undefined}
            <span
              class={cn("shrink-0 font-mono text-[0.6875rem] tabular-nums", {
                "text-muted-foreground": part.delta == null || part.delta === 0,
              })}
              >{part.delta == null
                ? m.seats_change_new()
                : part.delta === 0
                  ? "±0"
                  : `${part.delta > 0 ? "+" : "−"}${count(Math.abs(part.delta))}`}</span
            >
          {/if}
        </span>
      {/each}
    </span>
    <span
      class="relative flex animate-sweep overflow-hidden rounded-xs bg-muted block-2.5 motion-reduce:animate-none"
      aria-hidden="true"
    >
      {#each bar.isCount ? bar.parts : shown as part, at (part.key)}
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
          class="absolute inset-y-0 bg-foreground inline-0.5"
          style:inset-inline-start="{((Math.floor(bar.total / 2) + 1) /
            bar.total) *
            100}%"
        ></span>
      {/if}
    </span>
  </span>
{/snippet}
