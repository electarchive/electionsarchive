<script lang="ts">
  import { resolve } from "$app/paths";
  import { page } from "$app/state";
  import PauseIcon from "@lucide/svelte/icons/pause";
  import PlayIcon from "@lucide/svelte/icons/play";
  import SearchIcon from "@lucide/svelte/icons/search";
  import XIcon from "@lucide/svelte/icons/x";
  import { count, date } from "#lib/format.js";
  import { link } from "#lib/linked.js";
  import { m } from "#lib/paraglide/messages.js";
  import { getLocale } from "#lib/paraglide/runtime.js";
  import { finder } from "#lib/search.svelte.js";
  import { cn } from "#lib/utils.js";
  import type { PageData } from "./$types";
  import Lead from "./lead.svelte";

  const { data }: { data: PageData } = $props();

  const EXAMPLES = ["Bundestag", "Georgia", "İstanbul", "Pelosi", "Zürich"];
  const NOW = new Date().getUTCFullYear();

  let focus = $state<string | null>(null);
  let year = $state<number | null>(
    Number(page.url.searchParams.get("year")) || null,
  );
  let isPlaying = $state(false);

  const totals = $derived.by(() => {
    let elections = 0;
    const years: Array<number> = [];
    for (const polity of data.polities) {
      elections += polity.elections + polity.within;
      years.push(...[polity.first, polity.last].filter((year) => year != null));
    }
    return {
      elections,
      first: years.length === 0 ? null : Math.min(...years),
      last: years.length === 0 ? null : Math.max(...years),
    };
  });
  const listed = $derived(
    data.polities
      .filter((polity) => polity.first != null)
      .toSorted(
        (a, b) =>
          (a.first ?? NOW) - (b.first ?? NOW) || a.name.localeCompare(b.name),
      ),
  );
  const byId = $derived(new Map(listed.map((polity) => [polity.id, polity])));
  const depth = $derived.by(() => {
    const firsts = data.atlas.shapes.flatMap((shape) => {
      const first = byId.get(shape.id)?.first;
      return first == null ? [] : [first];
    });
    return { low: Math.min(...firsts), high: Math.max(...firsts) };
  });
  const lit = $derived(focus == null ? null : (byId.get(focus) ?? null));
  const union = $derived(
    data.polities.find((polity) => polity.kind === "supranational")?.id,
  );

  const span = $derived.by(() => {
    const all = data.polities.flatMap((polity) => polity.years);
    return all.length === 0
      ? null
      : { first: Math.min(...all), last: Math.max(...all) };
  });
  const tally = $derived(
    Map.groupBy(
      data.polities.flatMap((polity) => polity.years),
      (held) => held,
    ),
  );
  const held = $derived(
    year == null
      ? []
      : data.polities.filter((polity) => polity.years.includes(year ?? 0)),
  );
  const heldIds = $derived(new Set(held.map((polity) => polity.id)));
  const onRecord = $derived(
    year == null
      ? 0
      : listed.filter((polity) => (polity.first ?? Infinity) <= (year ?? 0))
          .length,
  );
  const along = (value: number) =>
    span == null || span.last === span.first
      ? 0.5
      : (value - span.first) / (span.last - span.first);

  $effect(() => {
    link({ year: year == null ? null : String(year) });
  });

  $effect(() => {
    if (!isPlaying || span == null) {
      return;
    }
    const last = span.last;
    const timer = setInterval(() => {
      if ((year ?? last) >= last) {
        isPlaying = false;
      } else {
        year = (year ?? last) + 1;
      }
    }, 140);
    return () => clearInterval(timer);
  });

  function fill(id: string, first: number | null | undefined) {
    if (year == null) {
      return `color-mix(in oklab, var(--primary) ${strength(first)}%, var(--card))`;
    }
    if (heldIds.has(id)) {
      return "var(--primary)";
    }
    return (first ?? Infinity) <= year
      ? "color-mix(in oklab, var(--primary) 22%, var(--card))"
      : "color-mix(in oklab, var(--muted-foreground) 8%, var(--card))";
  }

  function pick(event: PointerEvent) {
    if (span == null) {
      return;
    }
    const box = (event.currentTarget as HTMLElement).getBoundingClientRect();
    const x = Math.max(
      0,
      Math.min(1, (event.clientX - box.left) / Math.max(box.width, 1)),
    );
    year = Math.round(span.first + x * (span.last - span.first));
  }

  function strength(first: number | null | undefined) {
    if (first == null) {
      return 0;
    }
    const span = Math.max(1, depth.high - depth.low);
    return 26 + Math.sqrt((depth.high - first) / span) * 74;
  }

  function seek(term: string) {
    finder.value = term;
    finder.isOpen = true;
  }
</script>

{#snippet shapes(isInset: boolean)}
  <path d={data.atlas.land} class="fill-muted-foreground/8" />
  {#each data.atlas.shapes as shape (shape.id)}
    {#if shape.isCovered}
      {const polity = $derived(byId.get(shape.id))}
      <a
        href={polity?.path == null
          ? undefined
          : resolve("/[...path]", { path: polity.path })}
        aria-label={polity?.name}
        tabindex={isInset ? -1 : undefined}
        onpointerenter={() => (focus = shape.id)}
        onpointerleave={() => (focus = null)}
        onfocus={() => (focus = shape.id)}
        onblur={() => (focus = null)}
      >
        <path
          d={shape.path}
          style:fill={fill(shape.id, polity?.first)}
          class={cn(
            "stroke-foreground/50 transition-[fill,stroke] duration-300 motion-reduce:transition-none",
            { "stroke-foreground": focus === shape.id },
          )}
          stroke-width={focus === shape.id ? 2 : 0.75}
          vector-effect="non-scaling-stroke"
        />
      </a>
    {:else}
      <path
        d={shape.path}
        class={cn(
          "fill-muted-foreground/15 stroke-card transition-[fill] duration-300 motion-reduce:transition-none",
          {
            "fill-muted-foreground/40":
              (focus != null && focus === union) ||
              (union != null && heldIds.has(union)),
          },
        )}
        stroke-width="0.75"
        vector-effect="non-scaling-stroke"
      />
    {/if}
  {/each}
{/snippet}

<svelte:head>
  <link rel="canonical" href="{page.url.origin}/" />
  <meta property="og:type" content="website" />
  <meta property="og:title" content={m.wordmark()} />
  <meta property="og:image" content="{page.url.origin}/og/index.png" />
  <meta name="twitter:card" content="summary_large_image" />
</svelte:head>

<main class="p-3.5 max-sm:p-0">
  <article
    class="grid grid-cols-12 gap-px overflow-hidden rounded-lg border bg-border max-sm:rounded-none max-sm:border-x-0"
  >
    <header
      class="col-span-12 grid grid-cols-[minmax(0,1fr)] gap-4 bg-card px-3.5 pbs-8 pbe-6 sm:px-6 sm:pbs-12 sm:pbe-8"
    >
      <h1
        class="font-serif text-[clamp(2.25rem,6vw,4.75rem)] leading-[0.96] font-[680] tracking-tight text-balance max-inline-[14ch]"
      >
        {m.home_intro()}
      </h1>
      {#if totals.first != null && totals.last != null}
        <p
          class="text-[clamp(1rem,1.4vw,1.1875rem)] text-pretty text-muted-foreground max-inline-[52ch]"
        >
          {m.home_lede({
            elections: count(totals.elections),
            first: String(totals.first),
            last: String(totals.last),
          })}
        </p>
      {/if}
      <div class="grid gap-2.5 max-inline-160">
        <button
          type="button"
          class="flex items-center gap-3 rounded-md border-2 border-foreground bg-card ps-3.5 pe-2 text-start text-[1.0625rem] text-muted-foreground shadow-[4px_4px_0_var(--primary)] block-13 min-inline-0 hover:bg-secondary hover:text-foreground"
          onclick={() => seek("")}
        >
          <SearchIcon class="shrink-0 text-foreground block-5 inline-5" />
          <span class="grow truncate">{m.home_search_cta()}</span>
          <kbd
            class="keycap rounded-xs border border-input px-1.5 font-mono text-xs max-sm:hidden"
            >⌘K</kbd
          >
        </button>
        <p class="flex flex-wrap items-center gap-1.5 text-[0.8125rem]">
          <span class="text-muted-foreground">{m.home_search_try()}</span>
          {#each EXAMPLES as example (example)}
            <button
              type="button"
              class="rounded-full border border-input px-2.5 block-7 hover:border-foreground hover:bg-secondary"
              onclick={() => seek(example)}>{example}</button
            >
          {/each}
        </p>
      </div>
    </header>

    <section
      class="col-span-12 grid grid-cols-[minmax(0,1fr)] content-start gap-3 bg-card px-3.5 pbs-3.5 pbe-3 lg:col-span-8"
      aria-labelledby="atlas-title"
    >
      <div class="grid gap-0.5">
        <h2
          id="atlas-title"
          class="font-serif text-xl/tight font-[640] tracking-[-0.005em]"
        >
          {m.home_map_title()}
        </h2>
        <p class="text-[0.8125rem] text-muted-foreground max-inline-[64ch]">
          {m.home_map_lede()}
        </p>
      </div>
      {#if span != null}
        <div
          class="grid grid-cols-[auto_minmax(0,1fr)_auto] items-end gap-x-3 pbs-5"
        >
          <button
            type="button"
            class="flex items-center justify-center rounded-sm border border-input bg-card block-8 inline-8 hover:bg-secondary"
            aria-label={isPlaying ? m.scrubber_pause() : m.scrubber_play()}
            aria-pressed={isPlaying}
            onclick={() => {
              if (!isPlaying && (year == null || year >= span.last)) {
                year = span.first;
              }
              isPlaying = !isPlaying;
            }}
          >
            {#if isPlaying}
              <PauseIcon class="block-4 inline-4" />
            {:else}
              <PlayIcon class="block-4 inline-4" />
            {/if}
          </button>
          <div class="grid gap-1">
            <div
              class="relative cursor-grab touch-none rounded-sm outline-offset-4 select-none block-8 active:cursor-grabbing"
              role="slider"
              tabindex="0"
              aria-label={m.home_scrub_slider()}
              aria-valuemin={span.first}
              aria-valuemax={span.last}
              aria-valuenow={year ?? span.last}
              onpointerdown={(event) => {
                event.currentTarget.setPointerCapture(event.pointerId);
                isPlaying = false;
                pick(event);
              }}
              onpointermove={(event) => {
                if (event.currentTarget.hasPointerCapture(event.pointerId)) {
                  pick(event);
                }
              }}
              onkeydown={(event) => {
                const moves: Record<string, number> = {
                  ArrowLeft: -1,
                  ArrowDown: -1,
                  ArrowRight: 1,
                  ArrowUp: 1,
                  PageDown: -10,
                  PageUp: 10,
                  Home: -Infinity,
                  End: Infinity,
                };
                const move = moves[event.key];
                if (move != null) {
                  event.preventDefault();
                  isPlaying = false;
                  year = Math.max(
                    span.first,
                    Math.min(span.last, (year ?? span.last) + move),
                  );
                }
              }}
            >
              <span
                class="absolute inset-x-0 inset-be-0 bg-border block-px"
                aria-hidden="true"
              ></span>
              {#each tally as [value, group] (value)}
                <span
                  class={cn(
                    "absolute inset-be-0 -translate-x-1/2 rounded-t-[1px] transition-colors duration-150 inline-0.5 motion-reduce:transition-none",
                    {
                      "bg-muted-foreground/45": value !== year,
                      "bg-primary": value === year,
                    },
                  )}
                  style:inset-inline-start="{along(value) * 100}%"
                  style:block-size="{Math.min(100, 25 + group.length * 25)}%"
                  aria-hidden="true"
                ></span>
              {/each}
              {#if year != null}
                <span
                  class="pointer-events-none absolute inset-y-0 -translate-x-1/2 bg-foreground inline-px"
                  style:inset-inline-start="{along(year) * 100}%"
                  aria-hidden="true"
                ></span>
                <span
                  class="pointer-events-none absolute inset-be-[calc(100%+0.25rem)] rounded-xs bg-foreground px-1.5 font-mono text-xs font-semibold text-background tabular-nums"
                  style:inset-inline-start="{along(year) * 100}%"
                  style:translate="{-along(year) * 100}% 0"
                  aria-hidden="true">{year}</span
                >
              {/if}
            </div>
            <div
              class="flex justify-between font-mono text-[0.6875rem] text-muted-foreground tabular-nums"
              aria-hidden="true"
            >
              <span>{span.first}</span><span>{span.last}</span>
            </div>
          </div>
          {#if year == null}
            <span
              class="text-xs text-muted-foreground max-inline-[18ch] max-sm:hidden"
              >{m.home_scrub_hint()}</span
            >
          {:else}
            <button
              type="button"
              class="flex items-center gap-1 rounded-sm px-2 text-xs font-semibold block-8 hover:bg-secondary"
              onclick={() => {
                isPlaying = false;
                year = null;
              }}
            >
              <XIcon class="block-3.5 inline-3.5" />{m.home_scrub_all()}
            </button>
          {/if}
        </div>
      {/if}
      {#if year != null}
        <div class="grid gap-0.5" aria-live="polite">
          <p
            class="flex flex-wrap items-baseline gap-x-3 gap-y-1 font-serif text-[clamp(2rem,4vw,3rem)] leading-none font-[640] tracking-[-0.03em] tabular-nums"
          >
            {year}
            <span
              class="font-sans text-[0.8125rem] font-normal tracking-normal text-muted-foreground"
              >{m.home_scrub_record({
                count: count(onRecord),
                total: count(listed.length),
              })}</span
            >
          </p>
          <p class="text-sm font-semibold text-pretty">
            {held.length === 0
              ? m.home_scrub_quiet()
              : m.home_scrub_held({
                  places: new Intl.ListFormat(getLocale(), {
                    type: "conjunction",
                  }).format(held.map((polity) => polity.name)),
                })}
          </p>
        </div>
      {/if}
      <div class="relative">
        <svg
          class="block block-auto inline-full"
          viewBox={data.atlas.box}
          role="img"
          aria-label={m.home_map_title()}
        >
          {@render shapes(false)}
        </svg>
        <svg
          class="absolute inset-e-0 inset-be-[8%] block rounded-sm border bg-card block-auto inline-[46%] max-sm:static max-sm:mbs-2 max-sm:inline-full"
          viewBox={data.atlas.inset}
          aria-hidden="true"
        >
          {@render shapes(true)}
        </svg>
        {#if year == null}
          <div
            class="absolute inset-e-0 inset-bs-0 grid gap-1 text-[0.6875rem] text-muted-foreground inline-36 max-sm:static max-sm:inline-full"
          >
            <span
              class="block rounded-xs block-2"
              style:background={`linear-gradient(to right, var(--primary), color-mix(in oklab, var(--primary) 22%, var(--card)))`}
            ></span>
            <span class="flex justify-between font-mono tabular-nums">
              <span>{depth.low}</span><span>{depth.high}</span>
            </span>
          </div>
        {:else}
          <ul
            class="absolute inset-e-0 inset-bs-0 grid gap-1 text-[0.6875rem] text-muted-foreground max-sm:static max-sm:mbs-2 max-sm:flex max-sm:flex-wrap max-sm:gap-x-3"
          >
            {#each [{ key: "held", label: m.home_scrub_key_held(), color: "var(--primary)" }, { key: "record", label: m.home_scrub_key_record(), color: "color-mix(in oklab, var(--primary) 22%, var(--card))" }, { key: "none", label: m.home_scrub_key_none(), color: "color-mix(in oklab, var(--muted-foreground) 8%, var(--card))" }] as item (item.key)}
              <li class="flex items-center gap-1.5">
                <span
                  class="rounded-xs ring-1 ring-border block-2.5 inline-2.5"
                  style:background-color={item.color}
                ></span>{item.label}
              </li>
            {/each}
          </ul>
        {/if}
        {#if lit != null}
          <div
            class="pointer-events-none absolute inset-s-0 inset-be-2 grid gap-0.5 rounded-sm bg-card/95 px-3 py-2 text-xs shadow-menu ring-1 ring-border"
          >
            <span class="font-serif text-base/tight font-[640]">{lit.name}</span
            >
            <span class="text-muted-foreground"
              >{m.home_elections({
                count: lit.elections,
              })}{#if lit.first != null && lit.last != null}, {m.polity_span({
                  first: String(lit.first),
                  last: String(lit.last),
                })}{/if}</span
            >
            {#if lit.latest != null}
              <span>{lit.latest.name}</span>
            {/if}
          </div>
        {/if}
      </div>
    </section>

    <aside
      class="col-span-12 grid content-start gap-2 bg-card px-3.5 pbs-3.5 pbe-3 lg:col-span-4"
      aria-labelledby="polities-title"
    >
      <h2 id="polities-title" class="text-[0.8125rem] font-bold">
        {m.home_polities()}
      </h2>
      <ol class="grid">
        {#each listed as polity (polity.id)}
          {const from = $derived(
            (((polity.first ?? NOW) - (depth.low - 4)) /
              (NOW - (depth.low - 4))) *
              100,
          )}
          <li>
            <svelte:element
              this={polity.path == null ? "div" : "a"}
              href={polity.path == null
                ? undefined
                : resolve("/[...path]", { path: polity.path })}
              class={cn(
                "grid grid-cols-[minmax(0,1fr)_auto] items-baseline gap-x-3 gap-y-1 rounded-xs p-1.5 transition-opacity duration-300 motion-reduce:transition-none",
                { "bg-secondary": focus === polity.id },
                {
                  "opacity-40":
                    year != null && (polity.first ?? Infinity) > year,
                },
                { "hover:bg-secondary": polity.path != null },
              )}
              role={polity.path == null ? "presentation" : undefined}
              onpointerenter={() => (focus = polity.id)}
              onpointerleave={() => (focus = null)}
              onfocus={() => (focus = polity.id)}
              onblur={() => (focus = null)}
            >
              <span class="truncate text-[0.9375rem] font-semibold"
                >{polity.name}</span
              >
              <span
                class="font-mono text-[0.6875rem] text-muted-foreground tabular-nums"
                >{m.home_map_since({ year: String(polity.first) })}</span
              >
              <span
                class="relative col-span-2 block overflow-hidden rounded-full bg-muted block-1.5"
                aria-hidden="true"
              >
                <span
                  class="absolute inset-y-0 inset-e-0 rounded-full min-inline-1"
                  style:inset-inline-start="{from}%"
                  style:background-color={`color-mix(in oklab, var(--primary) ${strength(polity.first)}%, var(--muted))`}
                ></span>
              </span>
            </svelte:element>
          </li>
        {/each}
      </ol>
    </aside>

    {#if data.recent.length > 0}
      <section
        class="col-span-12 grid gap-3 bg-card px-3.5 pbs-3.5 pbe-3.5"
        aria-labelledby="recent-title"
      >
        <h2
          id="recent-title"
          class="font-serif text-xl/tight font-[640] tracking-[-0.005em]"
        >
          {m.home_recent()}
        </h2>
        <ul
          class="grid grid-cols-[repeat(auto-fill,minmax(min(100%,15rem),1fr))] gap-2"
        >
          {#each data.recent as election (election.path)}
            <li>
              <a
                class="grid content-between gap-3 rounded-md border bg-card px-3 pbs-2.5 pbe-3 block-full hover:border-foreground"
                href={resolve("/[...path]", { path: election.path })}
              >
                <span class="grid gap-0.5">
                  <span class="text-xs font-semibold text-muted-foreground"
                    >{election.polity}</span
                  >
                  <span class="font-serif text-lg/tight font-[640] text-balance"
                    >{election.name}</span
                  >
                  <span
                    class="font-mono text-xs text-muted-foreground tabular-nums"
                    >{date(election.date)}</span
                  >
                </span>
                <Lead id={election.id} />
              </a>
            </li>
          {/each}
        </ul>
      </section>
    {/if}

    {#if data.upcoming.length > 0}
      <section
        class="col-span-12 grid gap-2 bg-card px-3.5 pbs-3.5 pbe-3.5"
        aria-labelledby="upcoming-title"
      >
        <h2 id="upcoming-title" class="text-[0.8125rem] font-bold">
          {m.home_upcoming()}
        </h2>
        <ul class="grid gap-1 text-sm">
          {#each data.upcoming as election (election.path)}
            <li class="flex flex-wrap items-baseline gap-x-3">
              <span class="font-mono text-xs tabular-nums inline-28"
                >{date(election.date)}</span
              >
              <a
                class="hover:highlighter"
                href={resolve("/[...path]", { path: election.path })}
                >{election.name}</a
              >
            </li>
          {/each}
        </ul>
      </section>
    {/if}

    {#if data.today.length > 0}
      <section
        class="col-span-12 grid gap-3 bg-card px-3.5 pbs-3.5 pbe-3.5"
        aria-labelledby="today-title"
      >
        <div class="grid gap-0.5">
          <h2
            id="today-title"
            class="font-serif text-xl/tight font-[640] tracking-[-0.005em]"
          >
            {m.home_today()}
          </h2>
          <p class="text-[0.8125rem] text-muted-foreground">
            {m.home_today_lede()}
          </p>
        </div>
        <ol
          class="grid grid-cols-[repeat(auto-fill,minmax(min(100%,20rem),1fr))] gap-x-6"
        >
          {#each data.today as election (election.path)}
            <li
              class="grid grid-cols-[4rem_minmax(0,1fr)] items-baseline gap-x-3 border-be border-border/60 py-2"
            >
              <span class="font-mono text-xl tracking-[-0.02em] tabular-nums"
                >{election.date.slice(0, 4)}</span
              >
              <span class="grid gap-0.5">
                <a
                  class="justify-self-start text-[0.9375rem] font-semibold hover:highlighter"
                  href={resolve("/[...path]", { path: election.path })}
                  >{election.name}</a
                >
                <span class="text-xs text-muted-foreground"
                  >{[election.polity, date(election.date)]
                    .filter((part) => part != null)
                    .join(", ")}</span
                >
              </span>
            </li>
          {/each}
        </ol>
      </section>
    {/if}
  </article>
</main>
