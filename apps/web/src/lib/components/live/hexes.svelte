<script lang="ts">
  import type { Snippet } from "svelte";

  let {
    cartogram,
    colors,
    label,
    hovered = $bindable(null),
    href,
    onPick,
    tip,
  }: {
    cartogram: {
      box: [number, number, number, number];
      districts: Array<{ key: string; d: string; x: number; y: number }>;
      states: Array<string>;
      labels: Array<{ x: number; y: number; text: string }>;
    };
    colors: Record<string, string>;
    label: string;
    hovered?: string | null;
    href: (key: string) => string | null;
    onPick?: (key: string) => void;
    tip: Snippet<[string]>;
  } = $props();

  let pointer = $state<{ x: number; y: number } | null>(null);

  const view = $derived(
    `${cartogram.box[0] - 1} ${cartogram.box[1] - 1} ${cartogram.box[2] - cartogram.box[0] + 2} ${cartogram.box[3] - cartogram.box[1] + 2}`,
  );
  const shown = $derived(
    cartogram.districts.find((district) => district.key === hovered) ?? null,
  );

  function track(
    event: PointerEvent & { currentTarget: Element },
    key: string,
  ) {
    const box = event.currentTarget.closest("svg")?.getBoundingClientRect();
    if (event.pointerType === "touch" || box == null) {
      return;
    }
    hovered = key;
    pointer = { x: event.clientX - box.left, y: event.clientY - box.top };
  }
</script>

<div class="relative">
  <svg
    viewBox={view}
    class="block inline-full"
    role="img"
    aria-label={label}
    onpointerleave={() => {
      hovered = null;
      pointer = null;
    }}
  >
    {#each cartogram.districts as district (district.key)}
      {const link = $derived(href(district.key))}
      <a
        href={link ?? undefined}
        aria-label={district.key.toUpperCase()}
        onpointermove={(event) => track(event, district.key)}
        onclick={(event) => {
          if (onPick != null) {
            event.preventDefault();
            onPick(district.key);
          }
        }}
        onfocus={() => (hovered = district.key)}
        onblur={() => (hovered = null)}
      >
        <path
          d={district.d}
          class="stroke-background transition-[fill] duration-700 motion-reduce:transition-none"
          stroke-width="0.35"
          fill={colors[district.key] ??
            "color-mix(in oklab, var(--muted-foreground) 9%, var(--background))"}
        />
      </a>
    {/each}
    <g
      class="pointer-events-none fill-none stroke-foreground"
      stroke-width="0.28"
    >
      {#each cartogram.states as d, index (index)}
        <path {d} stroke-linejoin="round" />
      {/each}
    </g>
    {#if shown != null}
      <path
        d={shown.d}
        class="pointer-events-none fill-none stroke-foreground"
        stroke-width="0.9"
        stroke-linejoin="round"
      />
    {/if}
    <g class="pointer-events-none" aria-hidden="true">
      {#each cartogram.labels as entry (entry.text)}
        <text
          x={entry.x}
          y={entry.y}
          text-anchor="middle"
          dominant-baseline="central"
          class="fill-foreground font-sans font-semibold"
          paint-order="stroke"
          stroke="var(--background)"
          stroke-width="0.6"
          font-size="2.1">{entry.text}</text
        >
      {/each}
    </g>
  </svg>
  {#if hovered != null && pointer != null}
    <div
      class="pointer-events-none absolute z-10 grid gap-1 rounded-sm bg-popover/95 px-3 py-2 text-xs shadow-menu ring-1 ring-border min-inline-48"
      style:left="{pointer.x + 14}px"
      style:top="{pointer.y + 14}px"
    >
      {@render tip(hovered)}
    </div>
  {/if}
</div>
