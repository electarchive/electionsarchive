<script lang="ts">
  import { count, date, percent } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";
  import type { PageData } from "./$types";
  import Electors from "./electors.svelte";
  import Places from "./places.svelte";
  import Preview from "./preview.svelte";
  import Results, { MEASURES } from "./results.svelte";
  import Seats from "./seats.svelte";

  const {
    election,
  }: { election: Extract<PageData, { kind: "election" }>["election"] } =
    $props();

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
      .toSorted((a, b) => (a.place ?? a.name).localeCompare(b.place ?? b.name)),
  );
  const partNoun = $derived(
    parts.every((child) => child.noun != null && child.noun === parts[0]?.noun)
      ? parts[0]?.noun
      : null,
  );

  const races = $derived(
    election.races.map((race) => {
      const seated = race.results.filter((row) => (row.seats ?? 0) > 0);
      let total = 0;
      for (const row of seated) {
        total += row.seats ?? 0;
      }
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
      return {
        ...race,
        name:
          race.round != null &&
          election.races.some(
            (other) => other !== race && other.name === race.name,
          )
            ? m.race_round({ name: race.name, round: race.round })
            : race.name,
        votes: MEASURES[race.measure]?.() ?? m.col_votes(),
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
              : seated.length > 0 && !race.isOffice
                ? m.race_lede_seats({
                    party: leader.label,
                    seats: count(leader.seats ?? 0),
                    total: count(race.size ?? total),
                    share: percent(leader.share),
                  })
                : runner == null
                  ? null
                  : (seated.length > 0 ? m.race_lede_won : m.race_lede_votes)({
                      party: leader.label,
                      share: percent(leader.share),
                      margin: count(
                        Math.round((leader.share - runner.share) * 1000) / 10,
                      ),
                      runner: runner.label,
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
</header>

{#each races as race, index (index)}
  <section class="col-span-12 grid gap-1.5 bg-card px-3.5 pbs-4 pbe-1">
    <div class="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
      <h2
        class="font-serif text-2xl/tight font-[640] tracking-[-0.005em] text-balance"
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
    {#if race.lede != null}
      <p class="text-[0.9375rem] text-pretty max-inline-[64ch]">
        {race.lede}
      </p>
    {/if}
  </section>
  {#if race.electors != null && race.electors.rows.length > 0}
    <Electors electors={race.electors} />
  {/if}
  <div
    class={cn(
      "@container col-span-12 grid content-start gap-1 bg-card px-1.5 pbs-2 pbe-2.5",
      {
        "lg:col-span-8":
          race.seated.length > 0 && race.electors == null && !race.isOffice,
      },
    )}
  >
    <Results
      rows={race.rows}
      span={null}
      votes={race.votes}
      seats={race.seated.length === 0 ||
      (race.isOffice && race.electors == null)
        ? null
        : race.electors == null
          ? m.col_seats()
          : m.col_electors()}
    />
  </div>
  {#if race.seated.length > 0 && race.electors == null && !race.isOffice}
    <Seats
      parties={race.seated}
      places={[]}
      here={null}
      place={race.name}
      link={null}
      size={race.size}
      isComplete={race.size != null || race.count === 1}
    />
  {/if}
  {#if race.places != null}
    <Places
      places={{
        ...race.places,
        levels: race.places.levels.filter(
          (level) => level.code === race.places?.level,
        ),
      }}
      span={null}
      publishers={[]}
      votes={race.votes}
      limit={15}
    />
  {/if}
{:else}
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

{#if parts.length > 0}
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
