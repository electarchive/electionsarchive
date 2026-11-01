<script lang="ts">
  import type { SvelteSet } from "svelte/reactivity";
  import type { orpc } from "#lib/rpc.js";
  import { count, percent, points } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";
  import { cn } from "#lib/utils.js";

  const {
    contest,
    focus,
    picked,
    onfocus,
  }: {
    contest: Awaited<ReturnType<typeof orpc.contests.get.call>>;
    focus: string | null;
    picked: SvelteSet<string>;
    onfocus: (key: string) => void;
  } = $props();

  const led = $derived.by(() => {
    const places = contest.places;
    const found = new Map<string, number>();
    if (places == null) {
      return found;
    }
    for (const row of places.rows) {
      const entry = row.leader == null ? null : places.legend[row.leader];
      if (entry != null && (picked.size === 0 || picked.has(row.name))) {
        found.set(entry.key, (found.get(entry.key) ?? 0) + 1);
      }
    }
    return found;
  });
  const bars = $derived.by(() => {
    const places = contest.places;
    if (picked.size === 0 || places == null) {
      return contest.rows
        .flatMap((row) =>
          row.current == null
            ? []
            : [
                {
                  key: row.key,
                  label: row.label,
                  short: row.short,
                  color: row.color,
                  votes: row.current.votes,
                  share: row.current.share,
                  earlier: row.earlier?.share ?? null,
                  isWon: ["won", "elected"].includes(row.current.outcome ?? ""),
                },
              ],
        )
        .filter((bar, index) => index < 3 || bar.share >= 0.005)
        .slice(0, 12);
    }
    let total = 0;
    const sums = places.legend.map(() => 0);
    const before = places.legend.map(() => 0);
    let isComparable = true;
    for (const row of places.rows) {
      if (!picked.has(row.name)) {
        continue;
      }
      total += row.valid;
      for (const [index, share] of row.shares.entries()) {
        sums[index] = (sums[index] ?? 0) + share * row.valid;
        const earlier = row.earlier?.[index];
        if (earlier == null) {
          isComparable = false;
        } else {
          before[index] = (before[index] ?? 0) + earlier * row.valid;
        }
      }
    }
    return places.legend
      .map((entry, index) => ({
        key: entry.key,
        label: entry.label,
        short: entry.short,
        color: entry.color,
        votes: Math.round(sums[index] ?? 0),
        share: total === 0 ? 0 : (sums[index] ?? 0) / total,
        earlier:
          total === 0 || !isComparable || contest.target == null
            ? null
            : (before[index] ?? 0) / total,
        isWon: false,
      }))
      .toSorted((a, b) => b.share - a.share)
      .filter((bar, index) => index < 3 || bar.share >= 0.005)
      .slice(0, 12);
  });
  const spread = $derived.by(() => {
    const places = contest.places;
    const found = new Map<string, string>();
    if (places == null || places.rows.length < 2) {
      return found;
    }
    for (const [index, entry] of places.legend.entries()) {
      const marks: Array<string> = [];
      for (const row of places.rows) {
        const share = row.shares[index];
        if (share != null && (picked.size === 0 || picked.has(row.name))) {
          marks.push(`M${Math.round(share * 1000)} 0v8`);
        }
      }
      found.set(entry.key, marks.join(""));
    }
    return found;
  });
  const total = $derived.by(() => {
    let sum = 0;
    for (const bar of bars) {
      sum += bar.votes ?? 0;
    }
    return sum;
  });
  const margin = $derived(
    bars.length < 2 ? null : (bars[0]?.share ?? 0) - (bars[1]?.share ?? 0),
  );
  const hasEarlier = $derived(bars.some((bar) => bar.earlier != null));
</script>

<div class="grid gap-2.5 p-3.5">
  <div class="grid gap-1">
    <div
      class="flex overflow-hidden rounded-xs bg-muted block-3"
      aria-hidden="true"
    >
      {#each bars as bar (bar.key)}
        <span
          class="border-e border-card last:border-e-0"
          style:inline-size="{bar.share * 100}%"
          style:background-color={swatch(bar.color, bar.key)}
        ></span>
      {/each}
    </div>
    {#if hasEarlier}
      <div class="flex overflow-hidden opacity-45 block-1" aria-hidden="true">
        {#each bars as bar (bar.key)}
          <span
            style:inline-size="{(bar.earlier ?? 0) * 100}%"
            style:background-color={swatch(bar.color, bar.key)}
          ></span>
        {/each}
      </div>
    {/if}
    <p
      class="flex flex-wrap gap-x-4 font-figures text-xs text-muted-foreground tabular-nums"
    >
      {#if picked.size > 0}
        <span class="font-sans font-semibold text-foreground"
          >{m.workbench_selected({ count: picked.size })}</span
        >
      {/if}
      {#if total > 0}
        <span>{m.workbench_results_total({ count: count(total) })}</span>
      {/if}
      {#if margin != null}
        <span>{m.workbench_results_margin({ points: points(margin) })}</span>
      {/if}
    </p>
  </div>
  <div
    class="grid grid-cols-[minmax(0,1fr)_auto_auto_auto] items-center gap-x-3 text-sm"
  >
    {#each bars as bar (bar.key)}
      {const color = $derived(swatch(bar.color, bar.key))}
      {const change = $derived(
        bar.earlier == null ? null : bar.share - bar.earlier,
      )}
      <button
        type="button"
        class={cn(
          "col-span-4 grid grid-cols-subgrid items-center gap-y-1 rounded-xs px-1.5 py-1 text-start hover:bg-secondary",
          { "bg-secondary": bar.key === focus },
        )}
        aria-pressed={bar.key === focus}
        onclick={() => onfocus(bar.key)}
      >
        <span class="flex items-baseline gap-1.5 min-inline-0">
          <span
            class="shrink-0 self-center rounded-xs block-2.5 inline-2.5"
            style:background-color={color}
          ></span>
          <span class={cn("truncate", { "font-semibold": bar.isWon })}
            >{bar.label}</span
          >
          {#if bar.short != null}
            <span class="truncate text-xs text-muted-foreground max-sm:hidden"
              >{bar.short}</span
            >
          {/if}
          {#if (led.get(bar.key) ?? 0) > 0}
            <span
              class="shrink-0 font-figures text-xs text-muted-foreground tabular-nums"
              >{m.workbench_results_led({
                count: count(led.get(bar.key) ?? 0),
              })}</span
            >
          {/if}
        </span>
        <span
          class="text-end font-figures text-xs text-muted-foreground tabular-nums max-sm:hidden"
          >{bar.votes == null ? "" : count(bar.votes)}</span
        >
        <span
          class="text-end font-figures text-[0.8125rem] font-semibold tabular-nums"
          >{percent(bar.share)}</span
        >
        <span
          class={cn(
            "text-end font-figures text-xs tabular-nums min-inline-11",
            { "text-muted-foreground": change == null || change < 0 },
          )}>{change == null ? "" : points(change)}</span
        >
        <span class="relative col-span-4 block-2" aria-hidden="true">
          {#if bar.earlier != null}
            <span
              class="absolute inset-y-0 inset-s-0 border opacity-60"
              style:inline-size="{bar.earlier * 100}%"
              style:border-color={color}
            ></span>
          {/if}
          <span
            class="absolute inset-y-0.5 inset-s-0"
            style:inline-size="{bar.share * 100}%"
            style:background-color={color}
          ></span>
        </span>
        {#if spread.has(bar.key)}
          <svg
            class="col-span-4 block-2 inline-full"
            viewBox="0 0 1000 8"
            preserveAspectRatio="none"
            aria-hidden="true"
          >
            <path
              d={spread.get(bar.key)}
              style:stroke={color}
              stroke-opacity="0.45"
              stroke-width="1.5"
              vector-effect="non-scaling-stroke"
            />
          </svg>
        {/if}
      </button>
    {/each}
  </div>
  {#if spread.size > 0}
    <p class="text-xs text-muted-foreground">{m.workbench_results_spread()}</p>
  {/if}
</div>
