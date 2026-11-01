<script lang="ts">
  import type { Kind } from "./grid.js";

  const { kind }: { kind: Kind } = $props();

  const DOTS = [
    [8, 22, 2],
    [12, 18, 3],
    [17, 20, 2],
    [20, 13, 4],
    [26, 15, 2.5],
    [30, 10, 3],
    [35, 12, 2],
    [39, 7, 3.5],
  ] as const;
  const SWARM = [
    [12, 16, 2],
    [16, 13, 2.5],
    [16, 19, 2],
    [21, 16, 3.5],
    [26, 12, 2],
    [26, 20, 2.5],
    [30, 16, 2],
    [35, 16, 1.5],
  ] as const;
</script>

<svg
  viewBox="0 0 48 32"
  class="shrink-0 text-foreground block-8 inline-12"
  fill="currentColor"
  aria-hidden="true"
>
  <rect
    x="0.5"
    y="0.5"
    width="47"
    height="31"
    rx="2"
    fill="var(--background)"
    stroke="var(--border)"
  />
  {#if kind === "summary"}
    <rect x="5" y="6" width="5" height="3" rx="1" opacity="0.5" />
    <rect x="5" y="11" width="22" height="5" rx="1" opacity="0.85" />
    <rect x="5" y="20" width="9" height="3" rx="1" opacity="0.35" />
    <rect x="17" y="20" width="9" height="3" rx="1" opacity="0.35" />
    <rect x="29" y="20" width="9" height="3" rx="1" opacity="0.35" />
  {:else if kind === "bar"}
    <rect x="5" y="6" width="38" height="3" rx="1" opacity="0.2" />
    <rect x="5" y="6" width="21" height="3" rx="1" opacity="0.8" />
    <rect x="5" y="13" width="26" height="3" rx="1" opacity="0.8" />
    <rect x="5" y="19" width="17" height="3" rx="1" opacity="0.45" />
    <rect x="5" y="25" width="5" height="2" rx="1" opacity="0.25" />
  {:else if kind === "place"}
    <rect x="5" y="6" width="16" height="4" rx="1" opacity="0.85" />
    {#each [0, 1, 2] as row (row)}
      <rect
        x="5"
        y={14 + row * 5}
        width={30 - row * 9}
        height="2.5"
        rx="1"
        opacity={0.75 - row * 0.2}
      />
      <rect
        x="38"
        y={14 + row * 5}
        width="5"
        height="2.5"
        rx="1"
        opacity="0.3"
      />
    {/each}
  {:else if kind === "map"}
    <path d="M7 8h10l2 4h9l3-3 9 2v8l-4 6H23l-3-3h-7l-6-4z" opacity="0.35" />
    <path d="M17 8l2 4h9l-2 8h-6l-3-3z" opacity="0.8" />
  {:else if kind === "table"}
    {#each [0, 1, 2, 3] as row (row)}
      <rect x="5" y={6 + row * 6} width="12" height="3" rx="1" opacity="0.55" />
      <rect
        x="21"
        y={6 + row * 6}
        width={8 + ((row * 5) % 9)}
        height="3"
        rx="1"
        opacity="0.7"
      />
      <rect x="37" y={6 + row * 6} width="6" height="3" rx="1" opacity="0.3" />
    {/each}
  {:else if kind === "closest" || kind === "movers" || kind === "flips"}
    {#each [0, 1, 2, 3] as row (row)}
      <circle
        cx="7"
        cy={7.5 + row * 6}
        r="1.6"
        opacity={kind === "flips" ? 0.4 : 0.8}
      />
      {#if kind === "flips"}
        <circle cx="11" cy={7.5 + row * 6} r="1.6" opacity="0.85" />
      {/if}
      <rect
        x="15"
        y={6 + row * 6}
        width="11"
        height="3"
        rx="1"
        opacity="0.55"
      />
      {#if kind === "movers"}
        <rect
          x={row % 2 === 0 ? 35 : 35 - (6 - row)}
          y={6 + row * 6}
          width={row % 2 === 0 ? 8 - row : 6 - row}
          height="3"
          rx="0.5"
          opacity={row % 2 === 0 ? 0.8 : 0.4}
        />
        <rect x="34.6" y="5" width="0.8" height="23" opacity="0.4" />
      {:else}
        <rect
          x="30"
          y={6 + row * 6}
          width={4 + row * 3}
          height="3"
          rx="1"
          opacity="0.4"
        />
      {/if}
    {/each}
  {:else if kind === "scatter"}
    <rect x="5" y="16" width="38" height="0.8" opacity="0.35" />
    <path d="M6 24 42 8" stroke="currentColor" stroke-width="1" fill="none" />
    {#each DOTS as [cx, cy, r] (`${cx}-${cy}`)}
      <circle {cx} {cy} {r} opacity="0.55" />
    {/each}
  {:else if kind === "strip"}
    <rect x="23.5" y="6" width="1" height="20" opacity="0.8" />
    {#each SWARM as [cx, cy, r] (`${cx}-${cy}`)}
      <circle {cx} {cy} {r} opacity="0.5" />
    {/each}
  {:else if kind === "hemicycle"}
    {#each Array.from({ length: 15 }, (_, index) => index) as index (index)}
      {const angle = $derived(Math.PI - (index / 14) * Math.PI)}
      <circle
        cx={24 + Math.cos(angle) * 15}
        cy={26 - Math.sin(angle) * 15}
        r="2"
        opacity={index < 7 ? 0.8 : index < 9 ? 0.25 : 0.5}
      />
    {/each}
    {#each Array.from({ length: 9 }, (_, index) => index) as index (index)}
      {const angle = $derived(Math.PI - (index / 8) * Math.PI)}
      <circle
        cx={24 + Math.cos(angle) * 9}
        cy={26 - Math.sin(angle) * 9}
        r="2"
        opacity={index < 4 ? 0.8 : index < 5 ? 0.25 : 0.5}
      />
    {/each}
  {:else}
    <rect x="7" y="8" width="30" height="2" rx="1" opacity="0.5" />
    <rect x="7" y="14" width="34" height="2" rx="1" opacity="0.5" />
    <rect x="7" y="20" width="20" height="2" rx="1" opacity="0.5" />
    <rect x="28" y="19" width="1.2" height="5" />
  {/if}
</svg>
