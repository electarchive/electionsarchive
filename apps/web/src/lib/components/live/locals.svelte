<script lang="ts">
  import SearchIcon from "@lucide/svelte/icons/search";
  import XIcon from "@lucide/svelte/icons/x";
  import type { Local, LocalKind, LocalSort } from "#lib/live/local.js";
  import type { Summary } from "#lib/live/summary.js";
  import Name from "#lib/components/name.svelte";
  import { Segmented } from "#lib/components/ui/segmented/index.js";
  import { percent } from "#lib/format.js";
  import { tone } from "#lib/live/format.js";
  import { liveLinks } from "#lib/live/links.js";
  import {
    arrange,
    fold,
    highlight,
    localize,
    sides,
    tokenize,
  } from "#lib/live/local.js";
  import { clears, line, terms } from "#lib/measure.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";

  const {
    summaries,
    labels = {},
    isNation = false,
  }: {
    summaries: Array<Summary>;
    labels?: Record<string, string>;
    isNation?: boolean;
  } = $props();

  const toLive = liveLinks();

  const PAGE = 30;
  const KINDS: Array<{ value: LocalKind; label: () => string }> = [
    { value: "measure", label: m.night_local_measures },
    { value: "county", label: m.night_local_county },
    { value: "city", label: m.night_local_city },
    { value: "school", label: m.night_local_school },
    { value: "judicial", label: m.night_local_judicial },
    { value: "special", label: m.night_local_special },
    { value: "other", label: m.night_local_other },
  ];

  let query = $state("");
  let kind = $state<LocalKind | "all">("all");
  let sort = $state<LocalSort>("close");
  let limit = $state(PAGE);
  let field = $state<HTMLInputElement | null>(null);

  const locals = $derived(
    summaries.map((summary) =>
      localize(summary, labels[summary.race.state] ?? summary.race.state),
    ),
  );
  const tokens = $derived(tokenize(query));
  const kinds = $derived.by(() => {
    const counts = new Map<LocalKind, number>();
    for (const local of locals) {
      counts.set(local.kind, (counts.get(local.kind) ?? 0) + 1);
    }
    return [
      {
        value: "all" as const,
        label: m.night_local_all(),
        count: locals.length,
      },
      ...KINDS.flatMap((entry) => {
        const found = counts.get(entry.value);
        return found == null
          ? []
          : [{ value: entry.value, label: entry.label(), count: found }];
      }),
    ];
  });
  const sorts = $derived([
    { value: "close" as const, label: m.night_local_sort_close() },
    { value: "counted" as const, label: m.night_local_sort_counted() },
    { value: "name" as const, label: m.night_local_sort_name() },
  ]);
  const groups = $derived(arrange(locals, tokens, kind, sort));
  const matched = $derived.by(() => {
    let found = 0;
    for (const group of groups) {
      found += group.locals.length;
    }
    return found;
  });
  const isFiltered = $derived(tokens.length > 0 || kind !== "all");

  function shortcut(event: KeyboardEvent) {
    const target = event.target;
    if (
      event.key !== "/" ||
      event.metaKey ||
      event.ctrlKey ||
      event.altKey ||
      (target instanceof HTMLElement &&
        (target.isContentEditable ||
          ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)))
    ) {
      return;
    }
    event.preventDefault();
    field?.focus();
  }

  function reset() {
    limit = PAGE;
  }

  function clear() {
    query = "";
    reset();
    field?.focus();
  }

  function href(local: Local) {
    return toLive(local.summary.race.slug);
  }

  function verdict(local: Local) {
    const { summary } = local;
    const pair = sides(summary.race);
    if (local.isMeasure && pair != null) {
      const yes = summary.race.options[pair.yes];
      if (summary.winner != null) {
        return {
          text:
            summary.winner.id === yes?.id
              ? m.night_local_passed()
              : m.night_local_failed(),
          isFinal: true,
          isYes: summary.winner.id === yes?.id,
        };
      }
      if (summary.leader != null) {
        const isPassing = clears(
          summary.race.rule,
          summary.totals[pair.yes] ?? 0,
          summary.totals[pair.no] ?? 0,
        );
        return {
          text: isPassing ? m.night_local_passing() : m.night_local_failing(),
          isFinal: false,
          isYes: isPassing,
        };
      }
      return null;
    }
    return summary.winner == null
      ? null
      : { text: m.night_local_won(), isFinal: true, isYes: null };
  }

  function standings(local: Local) {
    const { summary } = local;
    const order = summary.totals
      .map((votes, index) => ({ votes, index }))
      .filter((entry) => summary.race.options[entry.index]?.isWriteIn !== true)
      .toSorted((a, b) => b.votes - a.votes);
    const winners = new Set(summary.race.certified?.winners ?? []);
    const shown = Math.min(5, Math.max(2, local.seats + 1));
    return order
      .map((entry, rank) => ({ ...entry, rank }))
      .filter(
        (entry) =>
          entry.rank < shown ||
          (tokens.length > 0 &&
            tokens.some((token) =>
              fold(summary.race.options[entry.index]?.label ?? "").includes(
                token,
              ),
            )),
      )
      .flatMap(({ rank, ...entry }) => {
        const option = summary.race.options[entry.index];
        return option == null
          ? []
          : [
              {
                option,
                rank,
                share: summary.total > 0 ? entry.votes / summary.total : null,
                isWinner: winners.has(option.id),
                isInside: summary.total > 0 && rank < local.seats,
              },
            ];
      });
  }
</script>

<svelte:window onkeydown={shortcut} />

{#snippet marked(text: string)}
  {#each highlight(text, tokens) as segment, index (index)}
    {#if segment.isHit}
      <mark class="bg-transparent highlighter text-inherit">{segment.text}</mark
      >
    {:else}
      {segment.text}
    {/if}
  {/each}
{/snippet}

<section
  class="grid gap-4 bg-background px-4 py-6 sm:px-8"
  aria-labelledby="night-local"
>
  <div class="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
    <h2 id="night-local" class="heading text-lg!">{m.night_local_title()}</h2>
    <p
      class="font-figures text-xs text-muted-foreground tabular-nums"
      aria-live="polite"
    >
      {m.night_local_shown({ shown: matched, count: locals.length })}
    </p>
  </div>

  <div class="grid gap-2">
    <div class="flex flex-wrap items-center gap-2">
      <label
        class="flex grow basis-72 items-center gap-2 rounded-sm border border-input bg-card px-2.5 block-9 focus-within:border-foreground focus-within:ring-2 focus-within:ring-ring/40"
      >
        <SearchIcon
          class="shrink-0 text-muted-foreground block-4 inline-4"
          aria-hidden="true"
        />
        <input
          bind:this={field}
          bind:value={query}
          type="search"
          enterkeyhint="search"
          autocomplete="off"
          spellcheck="false"
          class="flex-1 bg-transparent text-sm outline-none min-inline-0 [&::-webkit-search-cancel-button]:hidden"
          placeholder={m.night_local_search()}
          aria-label={m.night_local_search()}
          aria-keyshortcuts="/"
          oninput={reset}
          onkeydown={(event: KeyboardEvent) => {
            if (event.key === "Escape" && query !== "") {
              event.preventDefault();
              clear();
            }
          }}
        />
        {#if query === ""}
          <kbd
            class="hidden rounded-xs border border-border px-1.5 font-figures text-[0.6875rem] text-muted-foreground sm:inline"
            title={m.night_local_shortcut()}>/</kbd
          >
        {:else}
          <button
            type="button"
            class="-me-1 grid place-items-center rounded-xs text-muted-foreground block-6 inline-6 hover:bg-secondary hover:text-foreground"
            aria-label={m.night_local_clear()}
            onclick={clear}
          >
            <XIcon class="block-3.5 inline-3.5" aria-hidden="true" />
          </button>
        {/if}
      </label>
      <Segmented
        options={sorts}
        bind:value={sort}
        label={m.night_local_sort()}
        onValueChange={reset}
      />
    </div>
    {#if kinds.length > 2}
      <Segmented
        options={kinds}
        bind:value={kind}
        label={m.night_local_kind()}
        onValueChange={reset}
      />
    {/if}
  </div>

  {#if groups.length === 0}
    <div
      class="grid justify-items-start gap-2 border-bs border-border py-6 text-sm"
    >
      <p>{m.night_local_empty({ query: query.trim() })}</p>
      {#if isFiltered}
        <button
          type="button"
          class="rounded-sm border border-input bg-card px-2.5 text-[0.8125rem] font-semibold keycap block-7 hover:bg-secondary"
          onclick={() => {
            kind = "all";
            clear();
          }}>{m.night_local_clear()}</button
        >
      {/if}
    </div>
  {:else}
    <div class="gap-x-10 lg:columns-2">
      {#each groups.slice(0, limit) as group (group.key)}
        <section class="grid break-inside-avoid content-start pbe-5">
          <h3
            class="flex items-baseline justify-between gap-3 border-be border-foreground pbe-1"
          >
            <span class="text-[0.9375rem] font-bold text-pretty"
              >{@render marked(group.place)}</span
            >
            {#if isNation && group.place !== (labels[group.state] ?? group.state)}
              <span class="shrink-0 text-xs text-muted-foreground"
                >{@render marked(labels[group.state] ?? group.state)}</span
              >
            {/if}
          </h3>
          <ul class="grid">
            {#each group.locals as local (local.summary.race.id)}
              {const summary = $derived(local.summary)}
              {const status = $derived(verdict(local))}
              {const pair = $derived(
                local.isMeasure ? sides(summary.race) : null,
              )}
              <li class="border-be border-border">
                <a
                  href={href(local)}
                  class="grid gap-1.5 py-2 outline-none hover:highlighter focus-visible:highlighter"
                >
                  <span class="flex items-baseline justify-between gap-3">
                    <span
                      class="text-[0.8125rem] leading-snug font-semibold text-pretty"
                      >{@render marked(summary.race.title)}</span
                    >
                    {#if status != null}
                      <span
                        class={cn("shrink-0 text-xs font-bold", {
                          "text-(--yes)": status.isYes === true,
                          "text-(--no)": status.isYes === false,
                        })}
                        >{#if status.isFinal && status.isYes == null}✓{/if}
                        {status.text}</span
                      >
                    {/if}
                  </span>
                  {#if pair != null}
                    {const yes = $derived(summary.totals[pair.yes] ?? 0)}
                    {const no = $derived(summary.totals[pair.no] ?? 0)}
                    {const total = $derived(yes + no)}
                    <span class="grid gap-1">
                      <span
                        class="flex justify-between gap-3 font-figures text-xs tabular-nums"
                      >
                        <span class="text-(--yes)"
                          ><span class="font-sans font-semibold"
                            >{summary.race.options[pair.yes]?.label}</span
                          >
                          {total > 0 ? percent(yes / total) : ""}</span
                        >
                        <span class="text-(--no)"
                          >{total > 0 ? percent(no / total) : ""}
                          <span class="font-sans font-semibold"
                            >{summary.race.options[pair.no]?.label}</span
                          ></span
                        >
                      </span>
                      <span
                        class="relative flex overflow-hidden rounded-xs bg-muted block-1.5"
                        aria-hidden="true"
                      >
                        {#if total > 0}
                          <span
                            class="bg-(--yes) transition-[inline-size] duration-700 motion-reduce:transition-none"
                            style:inline-size="{(yes / total) * 100}%"
                          ></span>
                          <span class="flex-1 bg-(--no)"></span>
                        {/if}
                        <span
                          class="absolute inset-y-0 bg-background inline-px"
                          style:inset-inline-start="{line(summary.race.rule) *
                            100}%"
                        ></span>
                      </span>
                      {#if summary.race.rule != null}
                        <span class="text-xs text-muted-foreground"
                          >{terms(summary.race.rule).join(". ")}</span
                        >
                      {/if}
                    </span>
                  {:else}
                    <ol class="grid gap-0.5">
                      {#each standings(local) as entry (entry.option.id)}
                        <li
                          class={cn(
                            "grid grid-cols-[minmax(0,1fr)_3rem] items-center gap-x-2 font-figures text-xs tabular-nums",
                            {
                              "mbs-0.5 border-bs border-dashed border-foreground/40 pbs-1":
                                local.seats > 1 && entry.rank === local.seats,
                            },
                          )}
                        >
                          <span class="flex items-center gap-1.5 min-inline-0">
                            <span
                              class="shrink-0 rounded-full block-2 inline-2"
                              style:background-color={tone(entry.option)}
                            ></span>
                            <Name
                              label={entry.option.label}
                              person={entry.option.person}
                              hasSwatch={false}
                              hasLink={false}
                              class={cn("truncate font-sans", {
                                "font-semibold":
                                  entry.isWinner || entry.isInside,
                                "text-muted-foreground":
                                  !entry.isWinner && !entry.isInside,
                              })}>{@render marked(entry.option.label)}</Name
                            >
                            {#if entry.option.party != null}
                              <Name
                                label={entry.option.party.short}
                                party={entry.option.party.id}
                                color={entry.option.party.color}
                                hasSwatch={false}
                                hasLink={false}
                                class="shrink-0 text-muted-foreground"
                              />
                            {/if}
                            <span
                              class="relative ms-auto hidden overflow-hidden rounded-xs bg-muted block-1 inline-16 sm:block"
                              aria-hidden="true"
                            >
                              <span
                                class="absolute inset-y-0 inset-s-0 transition-[inline-size] duration-700 motion-reduce:transition-none"
                                style:inline-size="{(entry.share ?? 0) * 100}%"
                                style:background-color={tone(entry.option)}
                              ></span>
                            </span>
                          </span>
                          <span
                            class={cn("text-end", {
                              "font-semibold": entry.isWinner || entry.isInside,
                            })}
                            >{entry.share == null
                              ? "–"
                              : percent(entry.share)}</span
                          >
                        </li>
                      {/each}
                    </ol>
                  {/if}
                  <span
                    class="flex flex-wrap gap-x-3 font-figures text-[0.6875rem] text-muted-foreground tabular-nums"
                  >
                    <span
                      >{summary.later != null
                        ? m.night_later()
                        : summary.counted == null || summary.total === 0
                          ? m.night_local_waiting()
                          : m.night_local_counted({
                              share: percent(summary.counted, 0),
                            })}</span
                    >
                    {#if local.seats > 1}
                      <span>{m.night_local_seats({ count: local.seats })}</span>
                    {/if}
                  </span>
                </a>
              </li>
            {/each}
          </ul>
        </section>
      {/each}
    </div>
    {#if groups.length > limit}
      <button
        type="button"
        class="justify-self-start rounded-sm border border-input bg-card px-2.5 text-[0.8125rem] font-semibold keycap block-7 hover:bg-secondary"
        onclick={() => (limit += PAGE)}
        >{m.night_local_more({
          count: Math.min(PAGE, groups.length - limit),
        })}</button
      >
    {/if}
  {/if}
</section>
