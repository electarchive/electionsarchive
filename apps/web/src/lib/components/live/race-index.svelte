<script lang="ts">
  import ChevronDownIcon from "@lucide/svelte/icons/chevron-down";
  import ChevronLeftIcon from "@lucide/svelte/icons/chevron-left";
  import ChevronRightIcon from "@lucide/svelte/icons/chevron-right";
  import LayoutGridIcon from "@lucide/svelte/icons/layout-grid";
  import SearchIcon from "@lucide/svelte/icons/search";
  import XIcon from "@lucide/svelte/icons/x";
  import { MediaQuery } from "svelte/reactivity";
  import type { Summary } from "#lib/live/summary.js";
  import * as Drawer from "#lib/components/ui/drawer/index.js";
  import * as Popover from "#lib/components/ui/popover/index.js";
  import { isHover } from "#lib/explain.svelte.js";
  import { percent } from "#lib/format.js";
  import { HATCH } from "#lib/live/format.js";
  import { liveLinks } from "#lib/live/links.js";
  import { localize, tokenize } from "#lib/live/local.js";
  import { shade, verdict } from "#lib/live/summary.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";

  const {
    summaries,
    current = null,
  }: { summaries: Array<Summary>; current?: string | null } = $props();

  const toLive = liveLinks();
  const wide = new MediaQuery("min-width: 640px");

  const KINDS = [
    { value: "statewide", label: m.night_index_statewide },
    { value: "measure", label: m.night_local_measures },
    { value: "county", label: m.night_local_county },
    { value: "city", label: m.night_local_city },
    { value: "school", label: m.night_local_school },
    { value: "judicial", label: m.night_local_judicial },
    { value: "special", label: m.night_local_special },
    { value: "other", label: m.night_local_other },
  ];
  const JUDICIAL = /\b(?:judge|justice|court|magistrate|surrogate)\b/iu;

  let isOpen = $state(false);
  let query = $state("");
  let probed = $state<string | null>(null);

  const groups = $derived.by(() => {
    const found = new Map<
      string,
      Array<{ summary: Summary; haystack: string }>
    >();
    for (const summary of summaries) {
      const { race } = summary;
      const local = localize(summary, race.state);
      let kind: string = local.kind;
      if (
        race.office === "senate" ||
        race.office === "governor" ||
        race.jurisdiction?.kind === "state"
      ) {
        kind = "statewide";
      } else if (!local.isMeasure && JUDICIAL.test(race.title)) {
        kind = "judicial";
      }
      const list = found.get(kind) ?? [];
      list.push({ summary, haystack: local.haystack });
      found.set(kind, list);
    }
    return KINDS.flatMap((kind) => {
      const list = found.get(kind.value);
      if (list == null) {
        return [];
      }
      list.sort((a, b) =>
        a.summary.race.title.localeCompare(b.summary.race.title, undefined, {
          numeric: true,
        }),
      );
      const prefix = list.length > 1 ? shared(list) : "";
      return [
        {
          key: kind.value,
          label: kind.label(),
          prefix: prefix.replace(/:\s*$/u, ""),
          entries: list.map((entry) => ({
            ...entry,
            label: entry.summary.race.title.slice(prefix.length),
          })),
        },
      ];
    });
  });
  const order = $derived(groups.flatMap((group) => group.entries));
  const at = $derived(
    order.findIndex((entry) => entry.summary.race.slug === current),
  );
  const previous = $derived(
    order.length === 0 ? null : order.at(at <= 0 ? -1 : at - 1),
  );
  const next = $derived(
    order.length === 0 ? null : order[at === -1 ? 0 : (at + 1) % order.length],
  );
  const counts = $derived.by(() => {
    let called = 0;
    let counting = 0;
    let later = 0;
    for (const { summary } of order) {
      if (summary.winner != null || summary.projected != null) {
        called += 1;
      } else if (summary.leader != null) {
        counting += 1;
      } else if (summary.later != null) {
        later += 1;
      }
    }
    return {
      called,
      counting,
      later,
      waiting: order.length - called - counting - later,
    };
  });
  const tokens = $derived(tokenize(query));
  const matched = $derived(
    groups.flatMap((group) => {
      const entries = group.entries.filter((entry) =>
        tokens.every((token) => entry.haystack.includes(token)),
      );
      return entries.length === 0 ? [] : [{ ...group, entries }];
    }),
  );
  const shown = $derived(
    order.find((entry) => entry.summary.race.slug === probed) ?? null,
  );

  function shared(list: Array<{ summary: Summary }>) {
    const titles = list.map((entry) => entry.summary.race.title);
    let prefix = titles[0] ?? "";
    for (const title of titles) {
      while (!title.startsWith(prefix)) {
        prefix = prefix.slice(0, -1);
      }
    }
    const cut = prefix.lastIndexOf(": ");
    return cut === -1 ? "" : prefix.slice(0, cut + 2);
  }

  function close() {
    isOpen = false;
    query = "";
  }
</script>

{#snippet meter(counted: number | null, className: string)}
  <span
    class={cn(
      "relative overflow-hidden rounded-xs bg-muted block-1",
      className,
    )}
    aria-hidden="true"
  >
    <span
      class="absolute inset-y-0 inset-s-0 bg-foreground transition-[inline-size] duration-700 motion-reduce:transition-none"
      style:inline-size="{(counted ?? 0) * 100}%"
    ></span>
  </span>
{/snippet}

{#snippet label()}
  <LayoutGridIcon
    class="shrink-0 text-muted-foreground block-4 inline-4"
    aria-hidden="true"
  />
  {m.night_index_all({ count: order.length })}
  <ChevronDownIcon
    class={cn(
      "shrink-0 text-muted-foreground transition-transform block-3.5 inline-3.5",
      { "rotate-180": isOpen },
    )}
    aria-hidden="true"
  />
{/snippet}

{#snippet list()}
  <div class="grid grid-cols-[minmax(0,1fr)] gap-4">
    <label
      class="flex items-center gap-2 rounded-sm border border-input bg-card px-2.5 block-9 focus-within:border-foreground focus-within:ring-2 focus-within:ring-ring/40"
    >
      <SearchIcon
        class="shrink-0 text-muted-foreground block-4 inline-4"
        aria-hidden="true"
      />
      <input
        bind:value={query}
        type="search"
        enterkeyhint="search"
        autocomplete="off"
        spellcheck="false"
        class="flex-1 bg-transparent text-sm outline-none min-inline-0 [&::-webkit-search-cancel-button]:hidden"
        placeholder={m.night_local_search()}
        aria-label={m.night_local_search()}
        onkeydown={(event: KeyboardEvent) => {
          if (event.key === "Escape" && query !== "") {
            event.preventDefault();
            event.stopPropagation();
            query = "";
          }
        }}
      />
      {#if query !== ""}
        <button
          type="button"
          class="-me-1 grid place-items-center rounded-xs text-muted-foreground block-6 inline-6 hover:bg-secondary hover:text-foreground"
          aria-label={m.night_local_clear()}
          onclick={() => (query = "")}
        >
          <XIcon class="block-3.5 inline-3.5" aria-hidden="true" />
        </button>
      {/if}
    </label>
    {#each matched as group (group.key)}
      <section class="grid grid-cols-[minmax(0,1fr)] gap-1.5">
        <h3
          class="flex items-baseline gap-2 border-be border-border pbe-1.5 text-[0.8125rem]"
        >
          <span class="shrink-0 font-semibold">{group.label}</span>
          <span class="shrink-0 font-figures text-muted-foreground tabular-nums"
            >{group.entries.length}</span
          >
          {#if group.prefix !== ""}
            <span class="truncate text-xs text-muted-foreground"
              >{group.prefix}</span
            >
          {/if}
        </h3>
        <ul
          class="grid grid-cols-[repeat(auto-fill,minmax(15rem,1fr))] gap-x-2 gap-y-px"
        >
          {#each group.entries as entry (entry.summary.race.id)}
            {const summary = $derived(entry.summary)}
            {const color = $derived(shade(summary))}
            {const isCurrent = $derived(summary.race.slug === current)}
            <li>
              <a
                href={toLive(summary.race.slug)}
                aria-current={isCurrent ? "page" : undefined}
                class={cn(
                  "grid grid-cols-[auto_minmax(0,1fr)] items-start gap-x-2.5 gap-y-1 rounded-sm px-2 py-1.5 transition-colors hover:bg-secondary",
                  { "bg-secondary ring-1 ring-foreground": isCurrent },
                )}
                onclick={close}
              >
                <span
                  class={cn("mbs-1 rounded-xs block-2.5 inline-2.5", {
                    "bg-muted ring-1 ring-border ring-inset": color == null,
                  })}
                  style:background-color={color}
                ></span>
                <span
                  class="line-clamp-2 text-[0.8125rem]/snug font-medium text-pretty"
                  >{entry.label}</span
                >
                <span
                  class="col-start-2 flex items-center gap-2 text-xs text-muted-foreground min-inline-0"
                >
                  <span
                    class={cn("truncate", {
                      "font-semibold text-foreground": summary.winner != null,
                    })}>{verdict(summary)}</span
                  >
                  {#if summary.counted != null}
                    {@render meter(
                      summary.counted,
                      "ms-auto shrink-0 inline-10",
                    )}
                  {/if}
                </span>
              </a>
            </li>
          {/each}
        </ul>
      </section>
    {:else}
      <p class="py-6 text-center text-sm text-muted-foreground">
        {m.night_index_empty({ query })}
      </p>
    {/each}
  </div>
{/snippet}

<div
  class="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 gap-y-2.5 sm:grid-cols-[auto_auto_minmax(0,1fr)]"
>
  {#if wide.current}
    <Popover.Root bind:open={isOpen}>
      <Popover.Trigger
        class="flex items-center gap-2 rounded-sm px-3 text-[0.8125rem] font-semibold ring-1 ring-border transition-colors block-9 hover:bg-secondary aria-expanded:bg-secondary"
      >
        {@render label()}
      </Popover.Trigger>
      <Popover.Content
        align="start"
        class="gap-0 overflow-y-auto overscroll-contain p-4 inline-[min(60rem,calc(100vw-2rem))] max-block-[min(36rem,var(--bits-popover-content-available-height))]"
      >
        {@render list()}
      </Popover.Content>
    </Popover.Root>
  {:else}
    <Drawer.Root bind:open={isOpen}>
      <Drawer.Trigger
        class="flex items-center gap-2 rounded-sm px-3 text-[0.8125rem] font-semibold ring-1 ring-border transition-colors block-9 hover:bg-secondary aria-expanded:bg-secondary"
      >
        {@render label()}
      </Drawer.Trigger>
      <Drawer.Content>
        <Drawer.Title class="px-4 pbs-3 text-base font-semibold"
          >{m.night_index_all({ count: order.length })}</Drawer.Title
        >
        <div class="px-4 pbs-3 pbe-[calc(1.25rem+env(safe-area-inset-bottom))]">
          {@render list()}
        </div>
      </Drawer.Content>
    </Drawer.Root>
  {/if}

  <div class="flex items-center gap-1">
    {#if previous != null}
      <a
        href={toLive(previous.summary.race.slug)}
        class="grid place-items-center rounded-sm text-muted-foreground ring-1 ring-border transition-colors block-9 inline-9 hover:bg-secondary hover:text-foreground"
        aria-label={m.night_index_previous({
          race: previous.summary.race.title,
        })}
        title={previous.summary.race.title}
      >
        <ChevronLeftIcon class="block-4 inline-4" aria-hidden="true" />
      </a>
    {/if}
    {#if at !== -1}
      <span
        class="px-1.5 font-figures text-xs text-muted-foreground tabular-nums"
        >{m.night_index_position({ index: at + 1, count: order.length })}</span
      >
    {/if}
    {#if next != null}
      <a
        href={toLive(next.summary.race.slug)}
        class="grid place-items-center rounded-sm text-muted-foreground ring-1 ring-border transition-colors block-9 inline-9 hover:bg-secondary hover:text-foreground"
        aria-label={m.night_index_next({ race: next.summary.race.title })}
        title={next.summary.race.title}
      >
        <ChevronRightIcon class="block-4 inline-4" aria-hidden="true" />
      </a>
    {/if}
  </div>

  <p
    class="order-last col-span-2 flex items-center gap-x-3 truncate text-xs text-muted-foreground min-block-5 sm:order-0 sm:col-span-1 sm:justify-end"
    aria-live="polite"
  >
    {#if shown == null}
      <span class="font-figures tabular-nums"
        >{m.night_index_called({ count: counts.called })}</span
      >
      <span class="font-figures tabular-nums"
        >{m.night_index_counting({ count: counts.counting })}</span
      >
      <span class="font-figures tabular-nums"
        >{m.night_races_waiting({ count: counts.waiting })}</span
      >
      {#if counts.later > 0}
        <span class="font-figures tabular-nums"
          >{m.night_races_later({ count: counts.later })}</span
        >
      {/if}
    {:else}
      <span class="truncate"
        ><span class="font-semibold text-foreground"
          >{shown.summary.race.title}</span
        >
        {verdict(shown.summary)}{#if shown.summary.counted != null},
          {m.night_races_counted({
            share: percent(shown.summary.counted, 0),
          })}{/if}</span
      >
    {/if}
  </p>

  <ol
    class="col-span-2 flex flex-wrap gap-x-3 gap-y-2 sm:col-span-3"
    onpointerleave={() => (probed = null)}
  >
    {#each groups as group (group.key)}
      <li
        class="grid gap-1 min-inline-max"
        style:flex="{group.entries.length} 1 0%"
      >
        <ol class="flex gap-0.5">
          {#each group.entries as entry (entry.summary.race.id)}
            {const summary = $derived(entry.summary)}
            {const color = $derived(shade(summary))}
            {const isCurrent = $derived(summary.race.slug === current)}
            <li class="flex flex-1 min-inline-1">
              <a
                href={toLive(summary.race.slug)}
                aria-current={isCurrent ? "page" : undefined}
                aria-label="{summary.race.title}: {verdict(summary)}"
                class={cn(
                  "flex-1 rounded-xs transition-[background-color,box-shadow] duration-500 block-4 motion-reduce:transition-none",
                  { "bg-muted": color == null },
                  {
                    "ring-2 ring-foreground ring-offset-1 ring-offset-background":
                      isCurrent,
                    "hover:ring-1 hover:ring-foreground": !isCurrent,
                  },
                )}
                style:background-color={color}
                style:background-image={summary.later == null
                  ? undefined
                  : HATCH}
                onpointerenter={(event) => {
                  if (isHover(event)) probed = summary.race.slug;
                }}
                onfocus={() => (probed = summary.race.slug)}
                onblur={() => (probed = null)}
              ></a>
            </li>
          {/each}
        </ol>
        <span class="flex items-baseline gap-1.5 text-xs min-inline-0">
          <span class="truncate font-medium">{group.label}</span>
          <span class="font-figures text-muted-foreground tabular-nums"
            >{group.entries.length}</span
          >
        </span>
      </li>
    {/each}
  </ol>
</div>
