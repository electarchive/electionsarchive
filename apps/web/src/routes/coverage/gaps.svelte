<script lang="ts">
  import { resolve } from "$app/paths";
  import ArrowUpRightIcon from "@lucide/svelte/icons/arrow-up-right";
  import ChevronRightIcon from "@lucide/svelte/icons/chevron-right";
  import { createQuery } from "@tanstack/svelte-query";
  import { SvelteSet } from "svelte/reactivity";
  import { count, percent } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { getLocale } from "#lib/paraglide/runtime.js";
  import { orpc } from "#lib/rpc.js";
  import { cn } from "#lib/utils.js";
  import type { Counts, Goal } from "#lib/coverage.js";
  import Bar from "./bar.svelte";
  import Self from "./gaps.svelte";
  import { levelLabel, weighted } from "#lib/coverage.js";

  const {
    polity,
    goal,
    tier = null,
    order,
    rows = null,
    place = null,
    level = null,
    start = 0,
  }: {
    polity: string;
    goal: Goal;
    tier?: string | null;
    order: "gap" | "name";
    rows?: Array<
      Counts & { key: string; label: string | null; path: string | null }
    > | null;
    place?: string | null;
    level?: string | null;
    start?: number;
  } = $props();

  const fetched = createQuery(() => ({
    ...orpc.coverage.gaps.queryOptions({
      input: {
        polity,
        goal,
        tier: tier ?? undefined,
        state: place ?? undefined,
        level: level ?? undefined,
      },
    }),
    enabled: rows == null,
  }));
  const depth = $derived(place == null ? 0 : level == null ? 1 : 2);
  const all = $derived.by(() => {
    const named = (rows ?? fetched.data ?? []).map((row) => ({
      row,
      name:
        depth === 0
          ? (row.label ?? (row.key === "" ? m.coverage_region_none() : row.key))
          : depth === 1
            ? levelLabel(polity, row.key)
            : row.upcoming + row.pending > 0
              ? m.coverage_year_upcoming({
                  year: row.key,
                  count: count(row.upcoming + row.pending),
                })
              : row.key,
    }));
    return order === "name"
      ? named.toSorted((a, b) => a.name.localeCompare(b.name, getLocale()))
      : named;
  });
  let isAll = $state(false);
  let isDone = $state(false);
  const missing = $derived(
    all.filter(({ row }) => isDone || row.total > row.complete),
  );
  const done = $derived(all.length - missing.length);
  const list = $derived(isAll ? missing : missing.slice(0, 10));
  const opened = new SvelteSet<string>();
</script>

{#if rows == null && fetched.data == null}
  <div role="row" class="bg-card px-3.5 py-1.5 text-sm text-muted-foreground">
    <span role="gridcell">{m.coverage_loading()}</span>
  </div>
{:else if all.length === 0}
  <div role="row" class="bg-card px-3.5 py-1.5 text-sm text-muted-foreground">
    <span role="gridcell">{m.coverage_empty()}</span>
  </div>
{/if}
{#each list as { row, name } (row.key)}
  {const isOpen = $derived(opened.has(row.key))}
  <div
    role="row"
    aria-level={depth + 1}
    aria-expanded={depth < 2 ? isOpen : undefined}
    class="grid grid-cols-[minmax(0,1fr)_3.5rem_7rem] items-center gap-x-3 bg-card px-3.5 py-1.5 text-sm tabular-nums sm:grid-cols-[minmax(0,1fr)_5rem_16rem] lg:grid-cols-[minmax(0,30rem)_5rem_16rem]"
  >
    <span
      role="gridcell"
      class={cn("flex items-center gap-1 min-inline-0", {
        "ps-5": depth - start === 1,
        "ps-10": depth - start === 2,
      })}
    >
      {#if depth < 2}
        <button
          type="button"
          class="flex items-center gap-1 text-start min-inline-0 hover:underline focus-visible:outline-2 focus-visible:outline-ring"
          aria-label={isOpen
            ? m.coverage_collapse({ name })
            : m.coverage_expand({ name })}
          onclick={() => {
            if (isOpen) {
              opened.delete(row.key);
            } else {
              opened.add(row.key);
            }
          }}
        >
          <ChevronRightIcon
            class={cn(
              "shrink-0 text-muted-foreground transition-transform block-4 inline-4",
              { "rotate-90": isOpen },
            )}
          />
          <span class="truncate">{name}</span>
        </button>
        {#if row.path != null}
          <a
            class="flex shrink-0 items-center justify-center rounded-xs text-muted-foreground block-6 inline-6 hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring pointer-coarse:block-9 pointer-coarse:inline-9"
            href={resolve("/[...path]", { path: row.path })}
            aria-label={m.coverage_open({ name })}
            ><ArrowUpRightIcon class="block-3.5 inline-3.5" /></a
          >
        {/if}
      {:else}
        <span class="truncate">{name}</span>
      {/if}
    </span>
    <span role="gridcell" class="text-end">
      {count(row.total - row.complete - row.knownGap - row.blocked)}
    </span>
    <span role="gridcell" class="grid gap-1">
      {const done = $derived(
        goal === "local" && row.populationTotal > 0 ? weighted(row) : row,
      )}
      <span class="truncate text-xs text-muted-foreground">
        {[
          percent(done.complete / Math.max(1, done.total), 1),
          ...(row.knownGap > 0
            ? [m.coverage_known_gap_of({ count: count(row.knownGap) })]
            : []),
          ...(row.blocked > 0
            ? [m.coverage_blocked_of({ count: count(row.blocked) })]
            : []),
        ].join(" · ")}
      </span>
      <Bar
        counts={done}
        {goal}
        {polity}
        population={goal === "local" && row.populationTotal > 0}
        class="block-2"
      />
    </span>
  </div>
  {#if isOpen}
    <Self
      {polity}
      {goal}
      {order}
      {tier}
      place={depth === 0 ? row.key : place}
      level={depth === 1 ? row.key : null}
      {start}
    />
  {/if}
{/each}
{#if !isAll && missing.length > list.length}
  <div role="row" class="bg-card px-3.5 py-1.5 text-sm">
    <span
      role="gridcell"
      class={cn({ "ps-5": depth - start === 1, "ps-10": depth - start === 2 })}
    >
      <button
        type="button"
        class="text-muted-foreground underline-offset-2 hover:text-foreground hover:underline focus-visible:outline-2 focus-visible:outline-ring"
        onclick={() => (isAll = true)}
        >{m.coverage_show_all({ count: count(missing.length) })}</button
      >
    </span>
  </div>
{/if}
{#if done > 0 && (isAll || missing.length <= list.length)}
  <div role="row" class="bg-card px-3.5 py-1.5 text-sm">
    <span
      role="gridcell"
      class={cn({ "ps-5": depth - start === 1, "ps-10": depth - start === 2 })}
    >
      <button
        type="button"
        class="text-muted-foreground underline-offset-2 hover:text-foreground hover:underline focus-visible:outline-2 focus-visible:outline-ring"
        onclick={() => (isDone = true)}
        >{m.coverage_region_complete({ count: count(done) })}</button
      >
    </span>
  </div>
{/if}
