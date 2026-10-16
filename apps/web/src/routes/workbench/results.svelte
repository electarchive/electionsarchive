<script lang="ts">
  import type { SvelteSet } from "svelte/reactivity";
  import type { orpc } from "#lib/rpc.js";
  import { percent } from "#lib/format.js";
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
                  share: row.current.share,
                  earlier: row.earlier?.share ?? null,
                },
              ],
        )
        .filter((bar, index) => index < 3 || bar.share >= 0.01)
        .slice(0, 10);
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
        share: total === 0 ? 0 : (sums[index] ?? 0) / total,
        earlier:
          total === 0 || !isComparable || contest.target == null
            ? null
            : (before[index] ?? 0) / total,
      }))
      .toSorted((a, b) => b.share - a.share)
      .filter((bar, index) => index < 3 || bar.share >= 0.01)
      .slice(0, 10);
  });
</script>

<div class="grid gap-1 p-3.5">
  {#if picked.size > 0}
    <p class="pbe-1 text-xs text-muted-foreground">
      {m.workbench_selected({ count: picked.size })}
    </p>
  {/if}
  {#each bars as bar (bar.key)}
    <button
      type="button"
      class={cn(
        "grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1 rounded-xs px-1.5 py-1 text-start text-sm hover:bg-secondary",
        { "bg-secondary": bar.key === focus },
      )}
      aria-pressed={bar.key === focus}
      onclick={() => onfocus(bar.key)}
    >
      <span class="flex items-baseline gap-1.5 min-inline-0">
        <span
          class="shrink-0 self-center rounded-xs block-2.5 inline-2.5"
          style:background-color={swatch(bar.color, bar.key)}
        ></span>
        <span class="truncate font-semibold">{bar.label}</span>
        {#if bar.short != null}
          <span class="truncate text-xs text-muted-foreground">{bar.short}</span
          >
        {/if}
      </span>
      <span class="font-mono text-[0.8125rem] tabular-nums"
        >{percent(bar.share)}</span
      >
      <span class="relative col-span-2 bg-muted block-1.5" aria-hidden="true">
        <span
          class="absolute inset-y-0 inset-s-0"
          style:inline-size="{bar.share * 100}%"
          style:background-color={swatch(bar.color, bar.key)}
        ></span>
        {#if bar.earlier != null}
          <span
            class="absolute -inset-y-1 bg-foreground inline-0.5"
            style:inset-inline-start="{bar.earlier * 100}%"
          ></span>
        {/if}
      </span>
    </button>
  {/each}
</div>
