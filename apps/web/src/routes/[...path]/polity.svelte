<script lang="ts">
  import Versus from "#routes/compare/places.svelte";
  import { resolve } from "$app/paths";
  import { page } from "$app/state";
  import ArrowLeftRightIcon from "@lucide/svelte/icons/arrow-left-right";
  import { createQuery } from "@tanstack/svelte-query";
  import Name from "#lib/components/name.svelte";
  import { count, date, percent } from "#lib/format.js";
  import { keep } from "#lib/linked.js";
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";
  import { orpc } from "#lib/rpc.js";
  import { cn } from "#lib/utils.js";
  import type { PageData } from "./$types";
  import PlaceMap from "./map.svelte";
  import Preview from "./preview.svelte";
  import Timeline from "./record.svelte";
  import Scrubber from "./scrubber.svelte";

  const {
    id,
    polity,
  }: { id: string; polity: Extract<PageData, { kind: "polity" }>["polity"] } =
    $props();

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
  const isLocal = $derived(
    polity.parent != null &&
      polity.results.length > 0 &&
      !["sovereign_state", "supranational", "federal_unit"].includes(
        polity.kind,
      ),
  );
  const above = $derived(polity.breadcrumbs.at(-1)?.name ?? "");
  const versus = createQuery(() => ({
    ...orpc.polities.compare.queryOptions({
      input: { a: id, b: polity.parent ?? id },
    }),
    enabled: isLocal,
  }));
  const shared = $derived.by(() => {
    const data = versus.data;
    if (data == null) {
      return null;
    }
    const series = data.series
      .filter((entry) => !entry.isQuestion && entry.points.length > 1)
      .toSorted(
        (a, b) =>
          (b.points.at(-1)?.date ?? "").localeCompare(
            a.points.at(-1)?.date ?? "",
          ) || b.points.length - a.points.length,
      )
      .slice(0, 2);
    return series.length === 0 ? null : { ...data, series };
  });

  let filter = $state(page.url.searchParams.get("show") ?? "all");
  let query = $state(page.url.searchParams.get("q") ?? "");
  let picked = $state<string | null>(page.url.searchParams.get("within"));

  $effect(() => {
    keep({
      show: filter === "all" ? null : filter,
      q: query.trim() === "" ? null : query,
      within: picked,
    });
  });
  let isAllYears = $state(false);

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
  const KINDS: Record<string, () => string> = {
    all: m.polity_filter_all,
    county: m.polity_kind_county,
    municipality: m.polity_kind_municipality,
    school_district: m.polity_kind_school_district,
    special_district: m.polity_kind_special_district,
    judicial_district: m.polity_kind_judicial_district,
  };
  const kinds = $derived(
    Object.keys(KINDS).filter(
      (key) =>
        key === "all" || polity.polities.some((child) => child.kind === key),
    ),
  );
  const kind = $derived(
    picked != null && kinds.includes(picked)
      ? picked
      : kinds.includes("county")
        ? "county"
        : "all",
  );
  const children = $derived(
    polity.polities.filter(
      (child) =>
        (kind === "all" || child.kind === kind) &&
        child.name
          .toLocaleLowerCase()
          .includes(query.trim().toLocaleLowerCase()),
    ),
  );
  const preview = $derived.by(() => {
    const shown = [];
    let races = 0;
    for (const group of polity.results) {
      if (shown.length > 0 && races + group.races.length > 16) {
        break;
      }
      shown.push(group);
      races += group.races.length;
    }
    return shown;
  });
  const results = $derived(isAllYears ? polity.results : preview);
  const isNational = $derived(
    ["sovereign_state", "supranational"].includes(polity.kind),
  );
  const headline = $derived(
    isNational
      ? null
      : (polity.latest?.races.find((race) => race.contest != null) ?? null),
  );

  let loaded = $state<{
    id: string;
    contest: Awaited<ReturnType<typeof orpc.contests.get.call>>;
    bounds: Awaited<ReturnType<typeof orpc.contests.boundaries.call>>;
  } | null>(null);

  $effect(() => {
    const id = headline?.contest;
    if (id == null || loaded?.id === id) {
      return;
    }
    const stop = new AbortController();
    void (async () => {
      try {
        const contest = await orpc.contests.get.call({ id });
        const bounds = await orpc.contests.boundaries.call({
          id,
          level: contest.places?.level,
        });
        if (!stop.signal.aborted) {
          loaded = { id, contest, bounds };
        }
      } catch {
        loaded = null;
      }
    })();
    return () => {
      stop.abort();
    };
  });

  const drawn = $derived.by(() => {
    const places = loaded?.contest.places;
    if (loaded == null || places == null) {
      return null;
    }
    const level = loaded.bounds.find((entry) => entry.scheme === places.level);
    const own = loaded.bounds.find(
      (entry) => entry.k != null && entry.scheme !== places.level,
    );
    const inner = loaded.bounds.find(
      (entry) =>
        entry.k == null && entry.scheme !== places.level && entry.tiles != null,
    );
    const districts = places.districts ?? [];
    return level?.tiles == null || own == null || own.frames.length === 0
      ? null
      : {
          places,
          tiles: level.tiles,
          own:
            own.tiles == null || own.k == null
              ? null
              : { tiles: own.tiles, k: own.k },
          nested:
            inner?.tiles == null || districts.length === 0
              ? null
              : { tiles: inner.tiles, ks: districts.map((row) => row.k) },
          frames: own.frames,
          isApproximate: level.isApproximate,
          noun:
            places.levels.find((entry) => entry.code === places.level)?.noun ??
            level.noun,
          colors: places.legend.map((item) => swatch(item.color, item.key)),
          names: places.legend.map((item) =>
            item.person == null ? (item.short ?? item.label) : item.label,
          ),
        };
  });
  const isMapped = $derived(
    headline?.contest != null &&
      (loaded?.id !== headline.contest || drawn != null),
  );
</script>

<header class="col-span-12 grid gap-1.5 bg-card px-3.5 pbs-4 pbe-3.5">
  <div class="flex items-start justify-between gap-3">
    <h1 class="title">
      {polity.name}
    </h1>
    <a
      class="inline-flex shrink-0 items-center gap-1.5 rounded-sm border border-input px-2.5 text-[0.8125rem] font-semibold block-8 hover:bg-secondary"
      href={resolve(`/compare?a=${id}`)}
      ><ArrowLeftRightIcon class="block-3.5 inline-3.5" />{m.versus_action()}</a
    >
  </div>
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

{#if isLocal}
  {#if shared != null}
    <section
      class="col-span-12 grid gap-1 bg-card px-3.5 pbs-4 pbe-1"
      aria-labelledby="{uid}-versus"
    >
      <h2
        id="{uid}-versus"
        class="text-[clamp(1.5rem,2.4vw,2rem)]/tight font-[760] tracking-[-0.015em]"
      >
        {m.polity_versus_title({ place: polity.name })}
      </h2>
      <p class="flex flex-wrap items-baseline gap-x-3 gap-y-1 text-[0.9375rem]">
        <span class="text-pretty text-muted-foreground"
          >{m.polity_versus_lede({ place: polity.name, parent: above })}</span
        >
        <a
          class="text-[0.8125rem] font-semibold underline decoration-border underline-offset-4 hover:decoration-foreground"
          href={resolve(`/compare?a=${id}&b=${polity.parent ?? ""}`)}
          >{m.polity_versus_all({ place: polity.name, parent: above })}</a
        >
      </p>
    </section>
    <Versus data={shared} />
  {/if}
  {@render inPlace()}
{/if}

{#if isMapped}
  <div
    class="col-span-12 grid content-start gap-2 bg-card px-3.5 pbs-3 pbe-3.5 min-inline-0 lg:col-span-8"
  >
    {#if drawn == null}
      <p
        class="grid place-items-center rounded-sm text-sm text-muted-foreground ring-1 ring-border block-[min(32rem,60svh)]"
      >
        {m.map_loading()}
      </p>
    {:else}
      <h2 class="text-[0.8125rem] font-bold">
        {m.polity_map_title({ race: headline?.name ?? "", noun: drawn.noun })}
      </h2>
      <PlaceMap
        places={drawn.places}
        colors={drawn.colors}
        tiles={drawn.tiles}
        own={drawn.own}
        nested={drawn.nested}
        frames={drawn.frames}
        isApproximate={drawn.isApproximate}
        noun={drawn.noun}
        votes={m.col_votes()}
        names={drawn.names}
        levels={[]}
        span={null}
      />
    {/if}
  </div>
{/if}

{#if isNational}
  <Scrubber {id} place={polity.name} latest={polity.latest} />
{:else if polity.latest != null && polity.latest.races.length > 0}
  <section
    class={cn(
      "col-span-12 grid content-start gap-3 bg-card px-3.5 pbs-3 pbe-3.5",
      { "lg:col-span-4": isMapped },
    )}
    aria-labelledby="{uid}-latest"
  >
    <div
      class={cn("flex flex-wrap items-baseline gap-x-3 gap-y-0.5", {
        "lg:flex-col lg:items-start": isMapped,
      })}
    >
      <h2 id="{uid}-latest" class="text-[0.8125rem] font-bold">
        {m.polity_latest()}
      </h2>
      {#if polity.latest.path == null}
        <span class="heading">{polity.latest.name}</span>
      {:else}
        <Preview
          class="heading underline decoration-border underline-offset-4 hover:decoration-foreground"
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
        {const plain = $derived(
          race.name.replace(/^\d{4} /u, "").replace(/ election$/u, ""),
        )}
        {const short = $derived(
          plain.replace(new RegExp(`^${RegExp.escape(polity.name)} `, "u"), ""),
        )}
        {const stripped = $derived(
          polity.latest.races.some(
            (other) =>
              other !== race &&
              other.name.replace(/ election$/u, "").endsWith(` ${short}`),
          )
            ? plain
            : short,
        )}
        {const label = $derived(
          stripped.charAt(0).toUpperCase() + stripped.slice(1),
        )}
        <li class="grid content-start gap-1.5">
          <p class="text-sm font-semibold text-pretty">
            {#if race.path == null}
              {label}
            {:else}
              <Preview
                class="underline decoration-border underline-offset-4 hover:decoration-foreground"
                path={race.path}>{label}</Preview
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
                <span class="font-figures text-[0.8125rem] tabular-nums"
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

<Timeline
  {id}
  isExpected={["sovereign_state", "supranational", "federal_unit"].includes(
    polity.kind,
  )}
/>

{#if polity.elections.length > 0 || polity.results.length === 0}
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
                "-ms-px flex items-center border border-input bg-card px-2.5 text-[0.8125rem] font-semibold keycap block-7 first:ms-0 first:rounded-s-sm last:rounded-e-sm hover:bg-secondary",
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
              class="font-figures text-[0.9375rem] tracking-[-0.02em] tabular-nums"
              >{year ?? ""}</span
            >
            <ul class="grid gap-1 text-sm">
              {#each entries as election, index (index)}
                {const dated = $derived(
                  election.name.replace(
                    new RegExp(`^(?:\\p{L}+ (?:\\d{1,2}, )?)?${year} `, "u"),
                    "",
                  ),
                )}
                {const short = $derived(
                  dated.startsWith(`${polity.name} `)
                    ? dated.slice(polity.name.length + 1)
                    : dated,
                )}
                {const label = $derived(
                  short.charAt(0).toUpperCase() + short.slice(1),
                )}
                <li
                  class="grid grid-cols-[minmax(0,1fr)_auto] items-baseline gap-x-3"
                >
                  <span class="flex flex-wrap items-baseline gap-x-2">
                    {#if election.path == null}
                      <span>{label}</span>
                    {:else}
                      <Preview class="hover:highlighter" path={election.path}
                        >{label}</Preview
                      >
                    {/if}
                    {#if STATUSES[election.status] != null}
                      <span class="highlighter px-1 text-xs font-semibold"
                        >{STATUSES[election.status]?.()}</span
                      >
                    {/if}
                  </span>
                  <time
                    class="text-xs text-muted-foreground tabular-nums"
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
{/if}

{#if !isLocal}
  {@render inPlace()}
{/if}

{#if polity.polities.length > 0}
  <section
    class="col-span-12 grid content-start gap-2.5 bg-card px-3.5 py-3 lg:col-span-4"
    aria-labelledby="{uid}-places"
  >
    <div class="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
      <h2 id="{uid}-places" class="text-[0.8125rem] font-bold">
        {kinds.length === 2
          ? (KINDS[kinds[1] ?? ""]?.() ?? m.polity_subdivisions())
          : m.polity_subdivisions()}
      </h2>
      {#if kinds.length > 2}
        <div class="flex" role="group" aria-label={m.polity_kind_label()}>
          {#each kinds as key (key)}
            <button
              type="button"
              class={cn(
                "-ms-px flex items-center border border-input bg-card px-2.5 text-[0.8125rem] font-semibold keycap block-7 first:ms-0 first:rounded-s-sm last:rounded-e-sm hover:bg-secondary",
                {
                  "relative z-10 bg-primary text-primary-foreground hover:bg-primary":
                    kind === key,
                },
              )}
              aria-pressed={kind === key}
              onclick={() => (picked = key)}>{KINDS[key]?.()}</button
            >
          {/each}
        </div>
      {/if}
    </div>
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
                  class="ms-2 font-figures text-[0.6875rem]"
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

{#snippet inPlace()}
  {#if polity.results.length > 0}
    <section
      class="col-span-12 grid content-start gap-2.5 bg-card px-3.5 py-3"
      aria-labelledby="{uid}-results"
    >
      <div class="grid gap-0.5">
        <h2 id="{uid}-results" class="text-[0.8125rem] font-bold">
          {m.polity_results_title({ place: polity.name })}
        </h2>
        <p class="text-[0.8125rem] text-muted-foreground">
          {m.polity_results_lede({ place: polity.name })}
        </p>
      </div>
      <ol class="grid">
        {#each results as group (group.date)}
          <li
            class="grid items-baseline gap-x-3 gap-y-1.5 border-be border-border/60 py-2 last:border-be-0 sm:grid-cols-[4.5rem_minmax(0,1fr)]"
          >
            <time
              class="font-figures text-[0.9375rem] tracking-[-0.02em] tabular-nums"
              datetime={group.date}>{group.year ?? ""}</time
            >
            <ul class="grid gap-2">
              {#each group.races as race (race.id)}
                {const leader = $derived(race.leaders[0])}
                {const runner = $derived(race.leaders[1])}
                <li
                  class="grid items-center gap-x-4 gap-y-1 text-sm sm:grid-cols-[minmax(0,1fr)_6rem_minmax(0,15rem)]"
                >
                  <Preview
                    class="text-pretty first-letter:uppercase hover:highlighter"
                    path={race.path}
                    >{group.year != null &&
                    race.name.startsWith(`${group.year} `)
                      ? race.name.slice(String(group.year).length + 1)
                      : race.name}</Preview
                  >
                  <div
                    class="flex overflow-hidden rounded-xs bg-muted block-2 max-sm:hidden"
                    aria-hidden="true"
                  >
                    {#each race.leaders as row (row.key)}
                      <span
                        class="block-full"
                        style:inline-size="{row.share * 100}%"
                        style:background-color={swatch(row.color, row.key)}
                      ></span>
                    {/each}
                  </div>
                  <div class="grid gap-0.5 text-[0.8125rem]">
                    {#each [leader, runner] as row, index (index)}
                      {#if row != null}
                        <span
                          class={cn(
                            "grid grid-cols-[minmax(0,1fr)_auto] items-baseline gap-x-3",
                            { "text-muted-foreground": index > 0 },
                          )}
                        >
                          <Name
                            class="truncate min-inline-0"
                            label={row.label}
                            color={swatch(row.color, row.key)}
                            person={row.person}
                            party={row.party}
                          />
                          <span class="font-figures text-xs tabular-nums"
                            >{percent(row.share)}</span
                          >
                        </span>
                      {/if}
                    {/each}
                  </div>
                </li>
              {/each}
            </ul>
          </li>
        {/each}
      </ol>
      {#if preview.length < polity.results.length}
        <button
          type="button"
          class="justify-self-start rounded-sm border border-input bg-card px-2.5 text-[0.8125rem] font-semibold keycap block-7 hover:bg-secondary"
          aria-expanded={isAllYears}
          onclick={() => (isAllYears = !isAllYears)}
          >{isAllYears
            ? m.polity_results_fewer()
            : m.polity_results_more({
                count: String(polity.results.length),
              })}</button
        >
      {/if}
    </section>
  {/if}
{/snippet}
