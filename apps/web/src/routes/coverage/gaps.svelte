<script lang="ts">
  import ChevronRightIcon from "@lucide/svelte/icons/chevron-right";
  import { createQuery } from "@tanstack/svelte-query";
  import { SvelteSet } from "svelte/reactivity";
  import { count, percent } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { orpc } from "#lib/rpc.js";
  import { cn } from "#lib/utils.js";
  import type { Counts, Goal } from "./reasons.js";
  import Bar from "./bar.svelte";
  import Self from "./gaps.svelte";
  import { LEVELS } from "./reasons.js";

  const {
    goal,
    rows = null,
    place = null,
    level = null,
  }: {
    goal: Goal;
    rows?: Array<Counts & { key: string; label: string | null }> | null;
    place?: string | null;
    level?: string | null;
  } = $props();

  const fetched = createQuery(() => ({
    ...orpc.coverage.gaps.queryOptions({
      input: { goal, state: place ?? undefined, level: level ?? undefined },
    }),
    enabled: rows == null,
  }));
  const all = $derived(rows ?? fetched.data ?? []);
  const depth = $derived(place == null ? 0 : level == null ? 1 : 2);
  let isAll = $state(false);
  const list = $derived(isAll ? all : all.slice(0, 10));
  const opened = new SvelteSet<string>();
  const current = new Date().getFullYear();
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
{#each list as row (row.key)}
  {const name = $derived(
    depth === 0
      ? (row.label ?? row.key)
      : depth === 1
        ? (LEVELS[row.key]?.() ?? row.key)
        : Number(row.key) === current
          ? m.coverage_in_progress({ year: row.key })
          : row.key,
  )}
  {const isOpen = $derived(opened.has(row.key))}
  <div
    role="row"
    aria-level={depth + 1}
    aria-expanded={depth < 2 ? isOpen : undefined}
    class="grid grid-cols-[minmax(0,1fr)_3.5rem_7rem] items-center gap-x-3 bg-card px-3.5 py-1.5 text-sm tabular-nums sm:grid-cols-[minmax(0,1fr)_5rem_16rem]"
  >
    <span
      role="gridcell"
      class={cn("flex items-center gap-1 min-inline-0", {
        "ps-5": depth === 1,
        "ps-10": depth === 2,
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
      {:else}
        <span class="truncate">{name}</span>
      {/if}
    </span>
    <span role="gridcell" class="text-end">
      {count(row.total - row.complete)}
    </span>
    <span role="gridcell" class="grid gap-1">
      <span class="text-xs text-muted-foreground">
        {percent(row.complete / Math.max(1, row.total), 1)}
      </span>
      <Bar counts={row} {goal} class="block-2" />
    </span>
  </div>
  {#if isOpen}
    <Self
      {goal}
      place={depth === 0 ? row.key : place}
      level={depth === 1 ? row.key : null}
    />
  {/if}
{/each}
{#if !isAll && all.length > list.length}
  <div role="row" class="bg-card px-3.5 py-1.5 text-sm">
    <span
      role="gridcell"
      class={cn({ "ps-5": depth === 1, "ps-10": depth === 2 })}
    >
      <button
        type="button"
        class="text-muted-foreground underline-offset-2 hover:text-foreground hover:underline focus-visible:outline-2 focus-visible:outline-ring"
        onclick={() => (isAll = true)}
        >{m.coverage_show_all({ count: count(all.length) })}</button
      >
    </span>
  </div>
{/if}
