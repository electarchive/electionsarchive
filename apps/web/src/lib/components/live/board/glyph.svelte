<script lang="ts">
  import type { Kind } from "#lib/live/board/layout.js";

  const { kind }: { kind: Kind } = $props();
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
  {#if kind === "race"}
    <rect x="5" y="7" width="26" height="4" rx="1" />
    <rect x="5" y="14" width="18" height="4" rx="1" opacity="0.45" />
    <rect x="5" y="21" width="8" height="4" rx="1" opacity="0.2" />
    <rect x="36" y="7" width="7" height="4" rx="1" opacity="0.7" />
  {:else if kind === "map"}
    <path d="M7 8h10l2 4h9l3-3 9 2v8l-4 6H23l-3-3h-7l-6-4z" opacity="0.35" />
    <path d="M17 8l2 4h9l-2 8h-6l-3-3z" opacity="0.8" />
  {:else if kind === "nation"}
    <path
      d="M5 10l6-3h14l5 2h8l5 3v6l-4 4-6 1-3 4-4-3H13l-5-3-3-5z"
      opacity="0.25"
    />
    <path d="M25 9l5 2h8l-1 7-6 2-4-3z" opacity="0.75" />
    <path d="M11 7h8v8h-9l-2-4z" opacity="0.5" />
  {:else if kind === "hexes"}
    {#each [0, 1, 2, 3, 4] as column (column)}
      {#each [0, 1, 2] as row (row)}
        <circle
          cx={10 + column * 7 + (row % 2) * 3.5}
          cy={9 + row * 7}
          r="3"
          opacity={(column + row) % 3 === 0 ? 0.8 : 0.3}
        />
      {/each}
    {/each}
  {:else if kind === "power"}
    <rect x="5" y="14" width="38" height="5" rx="1" opacity="0.15" />
    <rect x="5" y="14" width="17" height="5" rx="1" opacity="0.8" />
    <rect x="30" y="14" width="13" height="5" rx="1" opacity="0.5" />
    <rect x="23.5" y="11" width="1" height="11" />
  {:else if kind === "chance"}
    <path
      d="M8 24a16 16 0 0 1 32 0"
      fill="none"
      stroke="currentColor"
      stroke-width="4"
      opacity="0.2"
    />
    <path
      d="M8 24a16 16 0 0 1 22-14.9"
      fill="none"
      stroke="currentColor"
      stroke-width="4"
      opacity="0.8"
    />
  {:else if kind === "trend"}
    <path
      d="M5 24l8-4 7 1 8-8 7 2 8-7"
      fill="none"
      stroke="currentColor"
      stroke-width="2"
    />
    <path d="M5 24l8-2 7 3 8-4 7 1 8-2v5H5z" opacity="0.15" />
  {:else if kind === "counties" || kind === "closest" || kind === "flips"}
    {#each [0, 1, 2, 3] as row (row)}
      <rect x="5" y={6 + row * 6} width="14" height="3" rx="1" opacity="0.55" />
      <rect
        x={kind === "closest" ? 30 : 24}
        y={6 + row * 6}
        width={kind === "closest" ? 4 + row * 2 : 19 - row * 3}
        height="3"
        rx="1"
        opacity={kind === "flips" && row === 0 ? 1 : 0.3}
      />
    {/each}
  {:else if kind === "seats"}
    {#each Array.from({ length: 24 }, (_, index) => index) as index (index)}
      <rect
        x={6 + (index % 8) * 4.6}
        y={8 + Math.floor(index / 8) * 6}
        width="3.4"
        height="4"
        rx="0.6"
        opacity={index % 8 < 3 ? 0.8 : index % 8 > 5 ? 0.45 : 0.15}
      />
    {/each}
  {:else if kind === "ticker"}
    {#each [0, 1, 2] as row (row)}
      <rect x="5" y={7 + row * 7} width="5" height="3" rx="1" opacity="0.35" />
      <rect
        x="13"
        y={7 + row * 7}
        width={30 - row * 7}
        height="3"
        rx="1"
        opacity={row === 0 ? 0.85 : 0.4}
      />
    {/each}
  {:else if kind === "closings"}
    <circle
      cx="16"
      cy="16"
      r="9"
      fill="none"
      stroke="currentColor"
      stroke-width="2"
    />
    <path d="M16 11v5l3 2" fill="none" stroke="currentColor" stroke-width="2" />
    <rect x="30" y="10" width="13" height="3" rx="1" opacity="0.55" />
    <rect x="30" y="17" width="9" height="3" rx="1" opacity="0.3" />
  {:else}
    <rect x="7" y="8" width="30" height="2" rx="1" opacity="0.5" />
    <rect x="7" y="14" width="34" height="2" rx="1" opacity="0.5" />
    <rect x="7" y="20" width="20" height="2" rx="1" opacity="0.5" />
    <rect x="28" y="19" width="1.2" height="5" />
  {/if}
</svg>
