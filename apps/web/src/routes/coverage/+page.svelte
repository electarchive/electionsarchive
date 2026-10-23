<script lang="ts">
  import { enhance } from "$app/forms";
  import { invalidateAll } from "$app/navigation";
  import { page } from "$app/state";
  import ChevronRightIcon from "@lucide/svelte/icons/chevron-right";
  import { count, percent } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { getLocale } from "#lib/paraglide/runtime.js";
  import { cn } from "#lib/utils.js";
  import type { PageData } from "./$types";
  import type { Counts, Goal } from "./reasons.js";
  import Bar from "./bar.svelte";
  import Gaps from "./gaps.svelte";
  import { GOALS, LABELS, REASONS } from "./reasons.js";
  import Tone from "./tone.svelte";

  const { data }: { data: PageData } = $props();

  const TITLES: Record<Goal, () => string> = {
    county: m.coverage_goal_county,
    history: m.coverage_goal_history,
    precinct: m.coverage_goal_precinct,
    local: m.coverage_goal_local,
  };
  const TABS: Record<Goal, () => string> = {
    county: m.coverage_goal_tab_county,
    history: m.coverage_goal_tab_history,
    precinct: m.coverage_goal_tab_precinct,
    local: m.coverage_goal_tab_local,
  };

  let goal = $state<Goal>("county");
  let order = $state<"gap" | "name">("gap");
  const lead = $derived(data.goals.find((row) => row.goal === "county"));
  const next = $derived(data.goals.filter((row) => row.goal !== "county"));

  $effect(() => {
    if (!data.building) {
      return;
    }
    const timer = setInterval(() => void invalidateAll(), 5000);
    return () => clearInterval(timer);
  });

  const built = $derived(
    data.builtAt == null
      ? m.coverage_building()
      : m.coverage_built({
          time: new Intl.DateTimeFormat(getLocale(), {
            dateStyle: "medium",
            timeStyle: "short",
          }).format(new Date(data.builtAt)),
        }),
  );
</script>

<svelte:head>
  <title>{m.coverage_title()} | {m.wordmark()}</title>
  <link rel="canonical" href="{page.url.origin}{page.url.pathname}" />
  <meta name="robots" content="noindex" />
</svelte:head>

{#snippet progress(row: { goal: Goal } & Counts, isLead: boolean)}
  {const held = $derived(row.total - row.absent)}
  <div class="grid gap-3">
    <p class="flex flex-wrap items-baseline gap-x-3 gap-y-1 tabular-nums">
      <span
        class={cn("font-bold tracking-[-0.01em]", {
          "text-3xl": isLead,
          "text-xl": !isLead,
        })}
      >
        {(row.goal === "local"
          ? m.coverage_progress_local
          : m.coverage_progress)({
          complete: count(row.complete),
          total: count(row.total),
        })}
      </span>
      <span class="text-muted-foreground">
        {m.coverage_percent({
          percent: percent(row.complete / Math.max(1, row.total), 1),
        })}
      </span>
      <span class="text-muted-foreground">
        {m.coverage_short({ count: count(row.total - row.complete) })}
      </span>
    </p>
    <Bar counts={row} goal={row.goal} class={isLead ? "block-4" : "block-3"} />
    <ul class="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
      {#each REASONS[row.goal] as reason (reason)}
        {const definition = $derived(
          reason === "complete"
            ? {
                county: m.coverage_reason_complete_county_def,
                history: m.coverage_reason_complete_history_def,
                precinct: m.coverage_reason_complete_precinct_def,
                local: m.coverage_reason_complete_local_def,
              }[row.goal]()
            : reason === "absent" && row.goal === "local"
              ? m.coverage_reason_absent_local_def()
              : {
                  coarse: m.coverage_reason_coarse_def,
                  partial: m.coverage_reason_partial_def,
                  totals: m.coverage_reason_totals_def,
                  bare: m.coverage_reason_bare_def,
                  absent: m.coverage_reason_absent_def,
                }[reason](),
        )}
        <li class="grid grid-cols-[0.75rem_minmax(0,1fr)] gap-x-2">
          <Tone {reason} class="mbs-1 rounded-xs block-3 inline-3" />
          <span>
            <span class="font-semibold">{LABELS[reason]()}</span>
            <span class="tabular-nums">{count(row[reason])}</span>
            <span class="block text-muted-foreground">{definition}</span>
          </span>
        </li>
      {/each}
    </ul>
    {#if row.goal === "history"}
      <p class="text-sm text-muted-foreground">
        {m.coverage_goal_history_note()}
      </p>
    {:else if row.goal === "local"}
      <p class="text-sm text-muted-foreground">
        {m.coverage_goal_local_note()}
      </p>
    {/if}
    {#if row.goal === "county" && held > 0}
      <p class="text-sm text-muted-foreground">
        {m.coverage_turnout({ count: count(row.turnout), held: count(held) })}
      </p>
    {:else if row.goal === "precinct" && row.precincts > 0}
      <p class="text-sm text-muted-foreground">
        {m.coverage_shapes({
          shaped: count(row.shaped),
          precincts: count(row.precincts),
        })}
      </p>
    {/if}
  </div>
{/snippet}

<main class="pbe-16">
  <article class="grid gap-px overflow-clip border-be bg-border">
    <header class="grid gap-2 bg-card px-3.5 pbs-4 pbe-3.5">
      <h1 class="title">{m.coverage_title()}</h1>
      <p class="text-[0.9375rem] text-muted-foreground max-inline-[72ch]">
        {m.coverage_lede()}
      </p>
      <div class="flex items-center gap-2 text-xs text-muted-foreground">
        {built}
        {#if data.dev}
          <form method="POST" action="?/refresh" use:enhance>
            <button
              type="submit"
              disabled={data.building}
              class="rounded-sm border border-border px-2 text-foreground block-6 hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring disabled:opacity-50 disabled:hover:bg-transparent"
              >{m.coverage_refresh()}</button
            >
          </form>
          {#if data.building}
            <span>{m.coverage_refresh_requested()}</span>
          {/if}
        {/if}
      </div>
    </header>
    {#if lead != null}
      <section
        aria-labelledby="coverage-lead"
        class="grid gap-3 bg-card px-3.5 py-4"
      >
        <hgroup class="grid gap-1">
          <p
            class="text-xs font-bold tracking-wide text-muted-foreground uppercase"
          >
            {m.coverage_goal_now()}
          </p>
          <h2
            id="coverage-lead"
            class="text-lg/snug font-bold max-inline-[60ch]"
          >
            {TITLES.county()}
          </h2>
        </hgroup>
        {@render progress(lead, true)}
      </section>
    {/if}
    {#if next.length > 0}
      <section aria-labelledby="coverage-next" class="grid bg-card">
        <hgroup class="grid gap-0.5 px-3.5 pbs-4 pbe-2">
          <h2 id="coverage-next" class="text-lg font-bold">
            {m.coverage_next()}
          </h2>
          <p class="text-sm text-muted-foreground">{m.coverage_next_hint()}</p>
        </hgroup>
        {#each next as row (row.goal)}
          <details class="group border-bs">
            <summary
              class="flex cursor-pointer list-none items-start gap-2 px-3.5 py-2.5 hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring [&::-webkit-details-marker]:hidden"
            >
              <ChevronRightIcon
                class="mbs-0.5 shrink-0 text-muted-foreground transition-transform block-4 inline-4 group-open:rotate-90"
              />
              <span class="grid flex-1 gap-1 min-inline-0">
                <span class="font-semibold">{TITLES[row.goal]()}</span>
                <span class="text-sm text-muted-foreground tabular-nums">
                  {(row.goal === "local"
                    ? m.coverage_progress_local
                    : m.coverage_progress)({
                    complete: count(row.complete),
                    total: count(row.total),
                  })},
                  {m.coverage_percent({
                    percent: percent(row.complete / Math.max(1, row.total), 1),
                  })}
                </span>
              </span>
            </summary>
            <div class="px-3.5 ps-9.5 pbe-4">
              {@render progress(row, false)}
            </div>
          </details>
        {/each}
      </section>
    {/if}
    <section aria-labelledby="coverage-where" class="grid gap-px bg-border">
      <div class="grid gap-2 bg-card px-3.5 pbs-4 pbe-3">
        <h2 id="coverage-where" class="text-lg font-bold">
          {m.coverage_where()}
        </h2>
        <p class="text-sm text-muted-foreground">{m.coverage_where_hint()}</p>
        <div class="flex flex-wrap items-center gap-2">
          <div
            role="group"
            aria-label={m.coverage_goal_tabs()}
            class="flex flex-wrap rounded-sm border border-border p-0.5 text-sm inline-fit"
          >
            {#each GOALS as option (option)}
              <button
                type="button"
                aria-pressed={goal === option}
                class={cn(
                  "rounded-[3px] px-2 block-7 focus-visible:outline-2 focus-visible:outline-ring",
                  {
                    "bg-foreground text-background": goal === option,
                    "text-muted-foreground hover:text-foreground":
                      goal !== option,
                  },
                )}
                onclick={() => (goal = option)}>{TABS[option]()}</button
              >
            {/each}
          </div>
          <div
            role="group"
            aria-label={m.coverage_sort()}
            class="flex rounded-sm border border-border p-0.5 text-sm inline-fit"
          >
            {#each ["gap", "name"] as const as option (option)}
              <button
                type="button"
                aria-pressed={order === option}
                class={cn(
                  "rounded-[3px] px-2 block-7 focus-visible:outline-2 focus-visible:outline-ring",
                  {
                    "bg-foreground text-background": order === option,
                    "text-muted-foreground hover:text-foreground":
                      order !== option,
                  },
                )}
                onclick={() => (order = option)}
                >{option === "gap"
                  ? m.coverage_sort_gap()
                  : m.coverage_sort_name()}</button
              >
            {/each}
          </div>
        </div>
        <p class="text-sm text-muted-foreground">{TITLES[goal]()}</p>
        <p class="text-xs text-muted-foreground">{m.coverage_column_note()}</p>
      </div>
      <div role="treegrid" aria-label={m.coverage_where()} class="grid gap-px">
        <div
          role="row"
          class="grid grid-cols-[minmax(0,1fr)_3.5rem_7rem] items-end gap-x-3 bg-card px-3.5 py-1.5 text-xs/tight font-bold sm:grid-cols-[minmax(0,1fr)_5rem_16rem]"
        >
          <span role="columnheader">{m.coverage_column_name()}</span>
          <span role="columnheader" class="text-end"
            >{m.coverage_column_short()}</span
          >
          <span role="columnheader">{m.coverage_column_done()}</span>
        </div>
        {#key goal}
          <Gaps {goal} {order} rows={goal === "county" ? data.gaps : null} />
        {/key}
      </div>
    </section>
  </article>
</main>
