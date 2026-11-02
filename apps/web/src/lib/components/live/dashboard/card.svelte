<script lang="ts">
  import type { Snippet } from "svelte";
  import type { Point } from "#lib/live/hover.js";

  const { point, children }: { point: Point; children: Snippet } = $props();

  let width = $state(0);
  let height = $state(0);
  let innerWidth = $state(0);
  let innerHeight = $state(0);

  const GAP = 14;
  const EDGE = 8;

  const left = $derived(
    point.x + GAP + width <= innerWidth - EDGE
      ? point.x + GAP
      : Math.max(EDGE, point.x - GAP - width),
  );
  const top = $derived(
    point.y + GAP + height <= innerHeight - EDGE
      ? point.y + GAP
      : Math.max(EDGE, point.y - GAP - height),
  );
</script>

<svelte:window bind:innerWidth bind:innerHeight />

<div
  class="pointer-events-none fixed z-50 grid gap-1 rounded-sm bg-popover/95 px-3 py-2 text-xs shadow-menu ring-1 ring-border max-inline-[min(20rem,calc(100vw-1rem))] min-inline-48"
  style:left="{left}px"
  style:top="{top}px"
  bind:offsetWidth={width}
  bind:offsetHeight={height}
  aria-hidden="true"
>
  {@render children()}
</div>
