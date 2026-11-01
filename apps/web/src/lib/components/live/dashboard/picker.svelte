<script lang="ts">
  import type { Summary } from "#lib/live/summary.js";
  import { percent } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";

  const {
    codes,
    labels,
    senate,
    governor,
    counted,
    color,
    href,
  }: {
    codes: Array<string>;
    labels: Record<string, string>;
    senate: (code: string) => Summary | undefined;
    governor: (code: string) => Summary | undefined;
    counted: (code: string) => number | null;
    color: (summary: Summary) => string;
    href: (code: string) => string;
  } = $props();

  const TILES: Array<[string, number, number]> = [
    ["ak", 0, 0],
    ["me", 10, 0],
    ["vt", 9, 1],
    ["nh", 10, 1],
    ["wa", 0, 2],
    ["id", 1, 2],
    ["mt", 2, 2],
    ["nd", 3, 2],
    ["mn", 4, 2],
    ["il", 5, 2],
    ["wi", 6, 2],
    ["mi", 7, 2],
    ["ny", 8, 2],
    ["ri", 9, 2],
    ["ma", 10, 2],
    ["or", 0, 3],
    ["nv", 1, 3],
    ["wy", 2, 3],
    ["sd", 3, 3],
    ["ia", 4, 3],
    ["in", 5, 3],
    ["oh", 6, 3],
    ["pa", 7, 3],
    ["nj", 8, 3],
    ["ct", 9, 3],
    ["ca", 0, 4],
    ["ut", 1, 4],
    ["co", 2, 4],
    ["ne", 3, 4],
    ["mo", 4, 4],
    ["ky", 5, 4],
    ["wv", 6, 4],
    ["va", 7, 4],
    ["md", 8, 4],
    ["de", 9, 4],
    ["az", 1, 5],
    ["nm", 2, 5],
    ["ks", 3, 5],
    ["ar", 4, 5],
    ["tn", 5, 5],
    ["nc", 6, 5],
    ["sc", 7, 5],
    ["dc", 8, 5],
    ["ok", 3, 6],
    ["la", 4, 6],
    ["ms", 5, 6],
    ["al", 6, 6],
    ["ga", 7, 6],
    ["hi", 0, 7],
    ["tx", 3, 7],
    ["fl", 8, 7],
  ];

  const known = $derived(new Set(codes));
</script>

{#snippet mark(summary: Summary | undefined, letter: string)}
  {#if summary != null}
    {const fill = $derived(color(summary))}
    <span
      class="grid place-items-center rounded-[2px] text-[0.5625rem]/none font-bold block-3 inline-3 sm:text-[0.625rem]/none sm:block-3.5 sm:inline-3.5"
      style:background-color={fill}
      style:color="oklch(from {fill} clamp(0, (0.7 - l) * 100, 1) 0 0)"
      aria-hidden="true">{letter}</span
    >
  {/if}
{/snippet}

<ol
  class="grid grid-cols-11 gap-0.5 sm:gap-1"
  aria-label={m.night_pick_label()}
>
  {#each TILES as [code, x, y] (code)}
    {const isKnown = $derived(known.has(code))}
    {const share = $derived(isKnown ? counted(code) : null)}
    <li
      class="aspect-square min-inline-0"
      style:grid-column-start={x + 1}
      style:grid-row-start={y + 1}
    >
      {#if isKnown}
        <a
          href={href(code)}
          class="relative flex flex-col justify-between overflow-hidden rounded-xs border border-border bg-card p-0.5 outline-none block-full inline-full hover:border-foreground focus-visible:border-foreground focus-visible:ring-2 focus-visible:ring-ring/40 sm:p-1"
          aria-label={labels[code] ?? code.toUpperCase()}
          title={labels[code] ?? code.toUpperCase()}
        >
          <span class="text-[0.625rem]/none font-bold uppercase sm:text-xs/none"
            >{code}</span
          >
          <span class="flex gap-px sm:gap-0.5">
            {@render mark(senate(code), m.night_pick_senate())}
            {@render mark(governor(code), m.night_pick_governor())}
          </span>
          {#if share != null}
            <span
              class="absolute inset-s-0 inset-be-0 bg-foreground block-0.5"
              style:inline-size="{share * 100}%"
              aria-hidden="true"
            ></span>
            <span class="sr-only"
              >{m.night_tip_counted({ share: percent(share, 0) })}</span
            >
          {/if}
        </a>
      {:else}
        <span
          class="flex rounded-xs border border-dashed border-border p-0.5 text-[0.625rem]/none font-semibold text-muted-foreground uppercase block-full inline-full sm:p-1 sm:text-xs/none"
          aria-hidden="true">{code}</span
        >
      {/if}
    </li>
  {/each}
</ol>
