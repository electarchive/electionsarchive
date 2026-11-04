<script lang="ts">
  import type { Snippet } from "svelte";
  import type { Shapes } from "#lib/live/board/feed.svelte.js";
  import Hatches from "#lib/components/live/hatches.svelte";
  import { MUTED } from "#lib/live/board/labels.js";
  import { cn } from "#lib/utils.js";

  let {
    shapes,
    fills,
    flips = {},
    label,
    focus = null,
    marked = null,
    hovered = $bindable(null),
    onPick,
    tip,
  }: {
    shapes: Shapes;
    fills: Record<string, string>;
    flips?: Record<string, string>;
    label: string;
    focus?: Set<string> | null;
    marked?: string | null;
    hovered?: string | null;
    onPick?: (id: string) => void;
    tip: Snippet<[string]>;
  } = $props();

  const id = $props.id();

  let pointer = $state<{ x: number; y: number; isRight: boolean } | null>(null);
  let rect = $state<DOMRectReadOnly>();

  const stripes = $derived([...new Set(Object.values(flips))]);

  const entries = $derived(Object.entries(shapes.areas));
  const view = $derived(`-4 -4 ${shapes.box[2] + 8} ${shapes.box[3] + 8}`);
  const outlined = $derived(
    [hovered, marked].flatMap((id) =>
      id == null || shapes.areas[id] == null ? [] : [shapes.areas[id]],
    ),
  );

  function track(event: PointerEvent & { currentTarget: Element }, id: string) {
    const box = event.currentTarget.closest("svg")?.getBoundingClientRect();
    if (event.pointerType === "touch" || box == null) {
      return;
    }
    hovered = id;
    const x = event.clientX - box.left;
    pointer = { x, y: event.clientY - box.top, isRight: x > box.width / 2 };
  }
</script>

<div class="relative grid place-items-center block-full min-block-0">
  <svg
    viewBox={view}
    class="block max-block-full max-inline-full"
    preserveAspectRatio="xMidYMid meet"
    role="img"
    aria-label={label}
    bind:contentRect={rect}
    onpointerleave={() => {
      hovered = null;
      pointer = null;
    }}
  >
    <defs>
      <Hatches
        {id}
        {stripes}
        box={[shapes.box[2] + 8, shapes.box[3] + 8]}
        {rect}
      />
    </defs>
    {#each entries as [area, d] (area)}
      {const isDim = $derived(focus != null && !focus.has(area))}
      <path
        {d}
        role="button"
        tabindex="-1"
        aria-disabled={onPick == null || isDim}
        class={cn(
          "stroke-background transition-[fill] duration-500 motion-reduce:transition-none",
          { "cursor-pointer": onPick != null && !isDim },
        )}
        stroke-width="0.6"
        vector-effect="non-scaling-stroke"
        fill={isDim ? MUTED : (fills[area] ?? MUTED)}
        opacity={isDim ? 0.55 : 1}
        onpointermove={(event) => {
          if (!isDim) {
            track(event, area);
          }
        }}
        onclick={() => {
          if (!isDim) {
            onPick?.(area);
          }
        }}
        onkeydown={(event) => {
          if (!isDim && (event.key === "Enter" || event.key === " ")) {
            onPick?.(area);
          }
        }}
      />
      {#if !isDim && flips[area] != null}
        <path
          {d}
          class="pointer-events-none"
          fill="url(#{id}-{stripes.indexOf(flips[area] ?? '')})"
        />
      {/if}
    {/each}
    {#each outlined as d, index (index)}
      <path
        {d}
        class="pointer-events-none fill-none stroke-foreground"
        stroke-width="1.6"
        stroke-linejoin="round"
        vector-effect="non-scaling-stroke"
      />
    {/each}
  </svg>
  {#if hovered != null && pointer != null}
    <div
      class="pointer-events-none absolute z-20 grid gap-0.5 rounded-sm bg-popover/95 px-2.5 py-1.5 text-[0.75rem] shadow-menu ring-1 ring-border min-inline-40"
      style:left={pointer.isRight ? undefined : `${pointer.x + 12}px`}
      style:right={pointer.isRight
        ? `calc(100% - ${pointer.x - 12}px)`
        : undefined}
      style:top="{pointer.y + 12}px"
    >
      {@render tip(hovered)}
    </div>
  {/if}
</div>
