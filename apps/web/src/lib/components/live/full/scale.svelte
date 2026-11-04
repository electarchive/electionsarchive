<script lang="ts">
  import type { Safety } from "#lib/shade.js";
  import { percent } from "#lib/format.js";
  import { marginShade, safety } from "#lib/shade.js";

  const {
    sides = null,
    bins = [],
  }: {
    sides?:
      | [{ name: string; color: string }, { name: string; color: string }]
      | null;
    bins?: Array<{ from: number; safety: Safety }>;
  } = $props();

  const ticks = $derived.by(() => {
    const levels = bins.length;
    const at = (position: number) => (position / (2 * levels)) * 100;
    return [
      ...bins
        .slice(1)
        .map((bin, index) => ({
          key: `a${index}`,
          at: at(levels - 1 - index),
          label: percent(bin.from, 0),
        })),
      { key: "even", at: at(levels), label: "0" },
      ...bins
        .slice(1)
        .map((bin, index) => ({
          key: `b${index}`,
          at: at(levels + 1 + index),
          label: percent(bin.from, 0),
        })),
    ];
  });
</script>

{#if sides == null}
  <div class="grid gap-1">
    <div class="flex gap-px overflow-hidden rounded-xs" aria-hidden="true">
      {#each [0.1, 0.3, 0.5, 0.7, 0.88] as step (step)}
        <span
          class="flex-1 block-2.5"
          style:background-color={marginShade("var(--map-ink)", safety(step))}
        ></span>
      {/each}
    </div>
    <div
      class="flex justify-between font-figures text-[0.6875rem]/4 tabular-nums"
    >
      <span>{percent(0, 0)}</span>
      <span>{percent(1, 0)}</span>
    </div>
  </div>
{:else}
  <div class="grid gap-1">
    <div class="flex gap-px overflow-hidden rounded-xs" aria-hidden="true">
      {#each sides as side, index (index)}
        <span class="flex flex-1 gap-px" class:flex-row-reverse={index === 0}>
          {#each bins as bin (bin.safety)}
            <span
              class="flex-1 block-2.5"
              style:background-color={marginShade(side.color, bin.safety)}
            ></span>
          {/each}
        </span>
      {/each}
    </div>
    <div
      class="relative font-figures text-[0.625rem]/3 text-muted-foreground tabular-nums block-3"
      aria-hidden="true"
    >
      {#each ticks as tick (tick.key)}
        <span
          class="absolute inset-bs-0 -translate-x-1/2"
          style:inset-inline-start="{tick.at}%">{tick.label}</span
        >
      {/each}
    </div>
    <div class="flex justify-between gap-3 text-[0.6875rem]/4 font-semibold">
      {#each sides as side, index (index)}
        <span class="truncate">{side.name}</span>
      {/each}
    </div>
  </div>
{/if}
