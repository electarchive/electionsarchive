<script lang="ts">
  import type { Snippet } from "svelte";
  import { cn } from "#lib/utils.js";

  let {
    room,
    detent = $bindable("peek"),
    height = $bindable(0),
    label,
    head,
    children,
  }: {
    room: number;
    detent?: "peek" | "half" | "full";
    height?: number;
    label: string;
    head: Snippet;
    children: Snippet;
  } = $props();

  const PEEK = 132;
  let top = $state(0);
  let drag = $state<{
    from: number;
    at: number;
    start: number;
    time: number;
  } | null>(null);
  const stops = $derived({
    peek: Math.min(top > 0 ? top : PEEK, room),
    half: Math.round(room * 0.52),
    full: Math.max(PEEK, room - 8),
  });
  const shown = $derived(
    drag == null
      ? stops[detent]
      : Math.min(stops.full, Math.max(56, drag.start - (drag.at - drag.from))),
  );

  $effect(() => {
    height = drag == null ? stops[detent] : Math.min(stops[detent], shown);
  });

  function cycle() {
    detent = detent === "peek" ? "half" : detent === "half" ? "full" : "peek";
  }

  function settle(current: number, velocity: number) {
    const ranked = (["peek", "half", "full"] as const).map((name) => ({
      name,
      gap: Math.abs(stops[name] - (current + velocity * 180)),
    }));
    ranked.sort((a, b) => a.gap - b.gap);
    detent = ranked[0]?.name ?? "peek";
  }
</script>

<section
  class={cn(
    "absolute inset-x-0 inset-be-0 z-20 grid grid-rows-[auto_minmax(0,1fr)] rounded-t-md bg-card shadow-menu ring-1 ring-border",
    {
      "transition-[block-size] duration-200 ease-out motion-reduce:transition-none":
        drag == null,
    },
  )}
  style:block-size="{shown}px"
  aria-label={label}
>
  <div
    class="cursor-grab touch-none select-none active:cursor-grabbing"
    role="presentation"
    bind:clientHeight={top}
    onpointerdown={(event) => {
      const target = event.target as HTMLElement;
      if (
        target.closest("a, button, input") != null &&
        target.closest("[data-grip]") == null
      ) {
        return;
      }
      event.currentTarget.setPointerCapture(event.pointerId);
      drag = {
        from: event.clientY,
        at: event.clientY,
        start: shown,
        time: event.timeStamp,
      };
    }}
    onpointermove={(event) => {
      if (drag != null) {
        drag = { ...drag, at: event.clientY };
      }
    }}
    onpointerup={(event) => {
      if (drag == null) {
        return;
      }
      const moved = drag.from - event.clientY;
      const velocity = moved / Math.max(16, event.timeStamp - drag.time);
      const current = shown;
      drag = null;
      if (Math.abs(moved) < 6) {
        cycle();
        return;
      }
      settle(current, velocity);
    }}
    onpointercancel={() => {
      drag = null;
    }}
  >
    <button
      type="button"
      class="mx-auto grid place-items-center block-5 inline-16"
      data-grip
      aria-label={label}
      aria-expanded={detent !== "peek"}
      onclick={(event) => {
        if (event.detail === 0) {
          cycle();
        }
      }}
    >
      <span class="rounded-full bg-input block-1 inline-9"></span>
    </button>
    {@render head()}
  </div>
  <div
    class={cn("overscroll-contain min-block-0", {
      "overflow-hidden": detent === "peek" && drag == null,
      "overflow-y-auto": detent !== "peek" || drag != null,
    })}
  >
    {@render children()}
  </div>
</section>
