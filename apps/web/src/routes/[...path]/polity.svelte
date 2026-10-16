<script lang="ts">
  import Name from "#lib/components/name.svelte";
  import { count, date, percent } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";
  import { cn } from "#lib/utils.js";
  import type { PageData } from "./$types";
  import Preview from "./preview.svelte";

  const {
    polity,
  }: { polity: Extract<PageData, { kind: "polity" }>["polity"] } = $props();

  const STATUSES: Record<string, () => string> = {
    scheduled: m.election_scheduled,
    postponed: m.election_postponed,
    canceled: m.election_canceled,
    annulled: m.election_annulled,
  };
  const FILTERS: Record<string, () => string> = {
    all: m.polity_filter_all,
    general: m.polity_filter_general,
    primary: m.polity_filter_primary,
    runoff: m.polity_filter_runoff,
    other: m.polity_filter_other,
  };

  const uid = $props.id();

  let filter = $state("all");
  let query = $state("");

  const sorts = $derived(
    polity.elections.map((election) => ({
      ...election,
      sort:
        election.kind === "general"
          ? "general"
          : election.kind === "primary" || election.kind === "caucus"
            ? "primary"
            : election.kind === "runoff"
              ? "runoff"
              : "other",
    })),
  );
  const present = $derived(
    Object.keys(FILTERS).filter(
      (key) => key === "all" || sorts.some((election) => election.sort === key),
    ),
  );
  const years = $derived(
    Map.groupBy(
      sorts.filter((election) => filter === "all" || election.sort === filter),
      (election) => election.year,
    )
      .entries()
      .toArray(),
  );
  const span = $derived.by(() => {
    const dated = polity.elections.flatMap((election) =>
      election.year == null ? [] : [election.year],
    );
    return dated.length === 0
      ? null
      : { first: Math.min(...dated), last: Math.max(...dated) };
  });
  const children = $derived(
    polity.polities.filter((child) =>
      child.name.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()),
    ),
  );
</script>

<header class="col-span-12 grid gap-1.5 bg-card px-3.5 pbs-4 pbe-3.5">
  <h1
    class="font-serif text-[clamp(1.75rem,3vw,2.5rem)] leading-[1.02] font-[640] tracking-[-0.01em] text-balance"
  >
    {polity.name}
  </h1>
  {#if span != null}
    <p class="text-[0.9375rem] text-muted-foreground">
      {m.polity_lede({
        elections: m.home_elections({ count: polity.elections.length }),
        first: String(span.first),
        last: String(span.last),
      })}{#if polity.within > 0}{" "}{m.polity_within({
          count: count(polity.within),
        })}{/if}
    </p>
  {/if}
</header>

{#if polity.latest != null && polity.latest.races.length > 0}
  <section
    class="col-span-12 grid gap-3 bg-card px-3.5 pbs-3 pbe-3.5"
    aria-labelledby="{uid}-latest"
  >
    <div class="flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
      <h2 id="{uid}-latest" class="text-[0.8125rem] font-bold">
        {m.polity_latest()}
      </h2>
      {#if polity.latest.path == null}
        <span class="font-serif text-xl/tight font-[640]"
          >{polity.latest.name}</span
        >
      {:else}
        <Preview
          class="font-serif text-xl/tight font-[640] underline decoration-border underline-offset-4 hover:decoration-foreground"
          path={polity.latest.path}>{polity.latest.name}</Preview
        >
      {/if}
      <time
        class="text-[0.8125rem] text-muted-foreground"
        datetime={polity.latest.date}>{date(polity.latest.date)}</time
      >
    </div>
    <ul
      class="grid grid-cols-[repeat(auto-fit,minmax(min(100%,17rem),1fr))] gap-x-6 gap-y-4"
    >
      {#each polity.latest.races as race, index (index)}
        {const isSeated = $derived(race.contested > 1)}
        <li class="grid content-start gap-1.5">
          <p class="text-sm font-semibold text-pretty">
            {#if race.path == null}
              {race.name}
            {:else}
              <Preview
                class="underline decoration-border underline-offset-4 hover:decoration-foreground"
                path={race.path}>{race.name}</Preview
              >
            {/if}
          </p>
          <div
            class="flex overflow-hidden rounded-xs bg-muted block-2"
            aria-hidden="true"
          >
            {#each race.results as row (row.key)}
              <span
                class="block-full"
                style:inline-size="{(isSeated
                  ? (row.seats ?? 0) / race.contested
                  : row.share) * 100}%"
                style:background-color={swatch(row.color, row.key)}
              ></span>
            {/each}
          </div>
          <ul class="grid gap-0.5 text-sm">
            {#each race.results.slice(0, 2) as row (row.key)}
              <li
                class="grid grid-cols-[minmax(0,1fr)_auto] items-baseline gap-x-3"
              >
                <Name
                  class="truncate min-inline-0"
                  label={row.label}
                  color={swatch(row.color, row.key)}
                  person={row.person}
                  party={row.party}
                />
                <span class="font-mono text-[0.8125rem] tabular-nums"
                  >{isSeated
                    ? m.polity_seats({
                        seats: count(row.seats ?? 0),
                        size: count(race.contested),
                      })
                    : percent(row.share)}</span
                >
              </li>
            {/each}
          </ul>
        </li>
      {/each}
    </ul>
  </section>
{/if}

<section
  class={cn("col-span-12 grid content-start gap-2.5 bg-card px-3.5 py-3", {
    "lg:col-span-8": polity.polities.length > 0,
  })}
  aria-labelledby="{uid}-elections"
>
  <div class="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
    <h2 id="{uid}-elections" class="text-[0.8125rem] font-bold">
      {m.polity_elections()}
    </h2>
    {#if present.length > 2}
      <div class="flex" role="group" aria-label={m.polity_filter_label()}>
        {#each present as key (key)}
          <button
            type="button"
            class={cn(
              "-ms-px flex keycap items-center border border-input bg-card px-2.5 text-[0.8125rem] font-semibold block-7 first:ms-0 first:rounded-s-sm last:rounded-e-sm hover:bg-secondary",
              {
                "relative z-10 bg-primary text-primary-foreground hover:bg-primary":
                  filter === key,
              },
            )}
            aria-pressed={filter === key}
            onclick={() => (filter = key)}>{FILTERS[key]?.()}</button
          >
        {/each}
      </div>
    {/if}
  </div>
  {#if polity.elections.length === 0}
    <p class="text-sm text-muted-foreground">{m.polity_no_elections()}</p>
  {:else if years.length === 0}
    <p class="text-sm text-muted-foreground">{m.polity_none_in_filter()}</p>
  {:else}
    <ol class="grid">
      {#each years as [year, entries] (year)}
        <li
          class="grid grid-cols-[4.5rem_minmax(0,1fr)] items-baseline gap-x-3 border-be border-border/60 py-1.5 last:border-be-0"
        >
          <span
            class="font-mono text-[0.9375rem] tracking-[-0.02em] tabular-nums"
            >{year ?? ""}</span
          >
          <ul class="grid gap-1 text-sm">
            {#each entries as election, index (index)}
              <li
                class="grid grid-cols-[minmax(0,1fr)_auto] items-baseline gap-x-3"
              >
                <span class="flex flex-wrap items-baseline gap-x-2">
                  {#if election.path == null}
                    <span>{election.name}</span>
                  {:else}
                    <Preview class="hover:highlighter" path={election.path}
                      >{election.name}</Preview
                    >
                  {/if}
                  {#if STATUSES[election.status] != null}
                    <span class="highlighter px-1 text-xs font-semibold"
                      >{STATUSES[election.status]?.()}</span
                    >
                  {/if}
                </span>
                <time
                  class="text-xs text-muted-foreground tabular-nums max-sm:hidden"
                  datetime={election.date}>{date(election.date)}</time
                >
              </li>
            {/each}
          </ul>
        </li>
      {/each}
    </ol>
  {/if}
</section>

{#if polity.polities.length > 0}
  <section
    class="col-span-12 grid content-start gap-2.5 bg-card px-3.5 py-3 lg:col-span-4"
    aria-labelledby="{uid}-places"
  >
    <h2 id="{uid}-places" class="text-[0.8125rem] font-bold">
      {m.polity_subdivisions()}
    </h2>
    {#if polity.polities.length > 12}
      <input
        class="rounded-sm border border-input bg-card px-2.5 text-sm block-8 placeholder:text-muted-foreground"
        type="search"
        placeholder={m.places_filter()}
        aria-label={m.places_filter()}
        bind:value={query}
      />
    {/if}
    {#if children.length === 0}
      <p class="text-sm text-muted-foreground">
        {m.places_filter_empty({ query })}
      </p>
    {:else}
      <ul class="grid text-sm">
        {#each children as child, index (index)}
          <li
            class="grid grid-cols-[minmax(0,1fr)_auto] items-baseline gap-x-3 border-be border-border/60 py-1 last:border-be-0"
          >
            {#if child.path == null}
              <span class="truncate">{child.name}</span>
            {:else}
              <Preview class="truncate hover:highlighter" path={child.path}
                >{child.name}</Preview
              >
            {/if}
            <span class="text-xs text-muted-foreground tabular-nums"
              >{m.home_elections({
                count: child.elections,
              })}{#if child.first != null && child.last != null}<span
                  class="ms-2 font-mono text-[0.6875rem]"
                  >{child.first === child.last
                    ? child.first
                    : m.polity_span({
                        first: String(child.first),
                        last: String(child.last),
                      })}</span
                >{/if}</span
            >
          </li>
        {/each}
      </ul>
    {/if}
  </section>
{/if}
