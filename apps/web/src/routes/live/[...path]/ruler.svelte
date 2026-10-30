<script lang="ts">
  import ChevronLeftIcon from "@lucide/svelte/icons/chevron-left";
  import ChevronRightIcon from "@lucide/svelte/icons/chevron-right";
  import PauseIcon from "@lucide/svelte/icons/pause";
  import PlayIcon from "@lucide/svelte/icons/play";
  import { prefersReducedMotion } from "svelte/motion";
  import { m } from "#lib/paraglide/messages.js";
  import { getLocale } from "#lib/paraglide/runtime.js";
  import { cn } from "#lib/utils.js";

  let {
    frames,
    position = $bindable(),
    isPlaying = $bindable(),
    hasFinal,
    zone,
    trace,
    counts,
  }: {
    frames: Array<{ at: string }>;
    position: number;
    isPlaying: boolean;
    hasFinal: boolean;
    zone: string;
    trace: Array<{ at: string; margin: number; color: string }>;
    counts: Array<number>;
  } = $props();

  let width = $state(720);
  let isDragging = $state(false);

  const HEIGHT = 64;
  const BASE = 42;
  const PAD = 10;
  const FINAL = 76;

  const last = $derived(frames.length - 1 + (hasFinal ? 1 : 0));
  const times = $derived(frames.map((frame) => Date.parse(frame.at)));
  const start = $derived(Math.floor((times[0] ?? 0) / 3_600_000) * 3_600_000);
  const end = $derived(
    Math.ceil(((times.at(-1) ?? 0) + 15 * 60_000) / 3_600_000) * 3_600_000,
  );
  const span = $derived(Math.max(1, end - start));
  const right = $derived(width - PAD - (hasFinal ? FINAL : 0));
  const x = (time: number) => PAD + ((time - start) / span) * (right - PAD);
  const finalX = $derived(width - PAD - FINAL / 2 + 6);
  const xs = $derived([
    ...times.map((time) => x(time)),
    ...(hasFinal ? [finalX] : []),
  ]);
  const hours = $derived.by(() => {
    const marks: Array<number> = [];
    for (let time = start; time <= end; time += 3_600_000) {
      marks.push(time);
    }
    return marks;
  });
  const hour = $derived(
    new Intl.DateTimeFormat(getLocale(), { hour: "numeric", timeZone: zone }),
  );
  const clock = $derived(
    new Intl.DateTimeFormat(getLocale(), {
      hour: "numeric",
      minute: "2-digit",
      timeZone: zone,
    }),
  );
  const isFinal = $derived(hasFinal && position === frames.length);
  const readout = $derived(
    isFinal ? m.live_final() : clock.format(times[position] ?? 0),
  );
  const peak = $derived(
    Math.max(0.02, ...trace.map((point) => Math.abs(point.margin))),
  );
  const y = (margin: number) => BASE - 8 - (Math.abs(margin) / peak) * 24;
  const most = $derived(Math.max(1, ...counts));

  function nearest(clientX: number, box: DOMRect) {
    const at = clientX - box.left;
    let best = 0;
    for (const [index, value] of xs.entries()) {
      if (Math.abs(value - at) < Math.abs((xs[best] ?? 0) - at)) {
        best = index;
      }
    }
    return best;
  }

  function step(by: number) {
    isPlaying = false;
    position = Math.min(last, Math.max(0, position + by));
  }

  function toggle() {
    if (!isPlaying && position >= last) {
      position = 0;
    }
    isPlaying = !isPlaying;
  }

  $effect(() => {
    if (!isPlaying) {
      return;
    }
    const timer = setInterval(
      () => {
        if (position >= last) {
          isPlaying = false;
          return;
        }
        position += 1;
      },
      prefersReducedMotion.current ? 1600 : 1100,
    );
    return () => clearInterval(timer);
  });
</script>

<div
  class="flex flex-wrap items-center gap-x-3 gap-y-0.5 sm:flex-nowrap sm:gap-5"
>
  <div class="flex shrink-0 items-center gap-1">
    <button
      type="button"
      class="grid place-items-center rounded-full bg-foreground text-background keycap block-10 inline-10 hover:bg-foreground/85"
      aria-label={isPlaying ? m.live_pause() : m.live_play()}
      onclick={toggle}
    >
      {#if isPlaying}
        <PauseIcon
          class="block-4 inline-4"
          fill="currentColor"
          strokeWidth={0}
        />
      {:else}
        <PlayIcon
          class="translate-x-px block-4 inline-4"
          fill="currentColor"
          strokeWidth={0}
        />
      {/if}
    </button>
    <button
      type="button"
      class="grid place-items-center rounded-full text-muted-foreground keycap block-8 inline-8 hover:bg-accent hover:text-foreground disabled:opacity-35"
      aria-label={m.live_back()}
      disabled={position === 0}
      onclick={() => step(-1)}
    >
      <ChevronLeftIcon class="block-4 inline-4" />
    </button>
    <button
      type="button"
      class="grid place-items-center rounded-full text-muted-foreground keycap block-8 inline-8 hover:bg-accent hover:text-foreground disabled:opacity-35"
      aria-label={m.live_forward()}
      disabled={position === last}
      onclick={() => step(1)}
    >
      <ChevronRightIcon class="block-4 inline-4" />
    </button>
  </div>
  <output
    class="shrink-0 font-figures text-[1.75rem] leading-none font-[760] tracking-[-0.02em] tabular-nums min-inline-[6.5ch] max-sm:text-[1.375rem]"
    aria-live="polite">{readout}</output
  >
  <div
    class="relative grow min-inline-0 max-sm:basis-full"
    bind:clientWidth={width}
  >
    <svg
      role="slider"
      tabindex="0"
      aria-label={m.live_ruler_label()}
      aria-valuemin={0}
      aria-valuemax={last}
      aria-valuenow={position}
      aria-valuetext={readout}
      class={cn("block touch-none outline-none select-none", {
        "cursor-grabbing": isDragging,
        "cursor-pointer": !isDragging,
      })}
      {width}
      height={HEIGHT}
      viewBox="0 0 {width} {HEIGHT}"
      onpointerdown={(event) => {
        isPlaying = false;
        isDragging = true;
        event.currentTarget.setPointerCapture(event.pointerId);
        position = nearest(
          event.clientX,
          event.currentTarget.getBoundingClientRect(),
        );
      }}
      onpointermove={(event) => {
        if (isDragging) {
          position = nearest(
            event.clientX,
            event.currentTarget.getBoundingClientRect(),
          );
        }
      }}
      onpointerup={() => {
        isDragging = false;
      }}
      onpointercancel={() => {
        isDragging = false;
      }}
      onkeydown={(event) => {
        const moves: Record<string, number> = {
          ArrowLeft: -1,
          ArrowDown: -1,
          ArrowRight: 1,
          ArrowUp: 1,
          Home: -last,
          End: last,
        };
        const by = moves[event.key];
        if (by != null) {
          event.preventDefault();
          step(by);
        }
      }}
    >
      {#each hours as mark (mark)}
        <line
          x1={x(mark)}
          x2={x(mark)}
          y1={BASE}
          y2={BASE + 5}
          class="stroke-muted-foreground/60"
        />
        <text
          x={x(mark)}
          y={BASE + 18}
          text-anchor={mark === start
            ? "start"
            : mark === end && !hasFinal
              ? "end"
              : "middle"}
          class="fill-muted-foreground font-figures text-[0.6875rem] tabular-nums"
          >{hour.format(mark)}</text
        >
      {/each}
      <line
        x1={PAD}
        x2={right}
        y1={BASE}
        y2={BASE}
        class="stroke-border"
        stroke-width="2"
      />
      <line
        x1={PAD}
        x2={Math.min(right, xs[position] ?? PAD)}
        y1={BASE}
        y2={BASE}
        class="stroke-foreground transition-[x2] duration-300 ease-out motion-reduce:transition-none"
        stroke-width="2"
      />
      {#each times as time, index (index)}
        <line
          x1={x(time)}
          x2={x(time)}
          y1={BASE - 3 - ((counts[index] ?? 0) / most) * 9}
          y2={BASE}
          class={cn("transition-colors", {
            "stroke-foreground": index <= position,
            "stroke-muted-foreground/45": index > position,
          })}
          stroke-width="1.5"
        />
      {/each}
      {#if trace.length > 1}
        <polyline
          points={trace
            .map((point) => `${x(Date.parse(point.at))},${y(point.margin)}`)
            .join(" ")}
          fill="none"
          class="stroke-muted-foreground/50"
          stroke-width="1"
          stroke-dasharray="2 2"
        />
      {/if}
      {#each trace as point (point.at)}
        {const isPast = $derived(
          Date.parse(point.at) <= (times[position] ?? Infinity) || isFinal,
        )}
        <circle
          cx={x(Date.parse(point.at))}
          cy={y(point.margin)}
          r={isPast ? 3.5 : 2.5}
          fill={isPast ? point.color : "var(--card)"}
          stroke={point.color}
          stroke-width="1.5"
        />
      {/each}
      {#if hasFinal}
        <line
          x1={right + 8}
          x2={right + 8}
          y1={BASE - 14}
          y2={BASE + 6}
          class="stroke-border"
        />
        <path
          d="M {finalX} {BASE - 5} l 5 5 l -5 5 l -5 -5 z"
          class={cn({
            "fill-foreground": isFinal,
            "fill-card stroke-foreground": !isFinal,
          })}
        />
        <text
          x={finalX}
          y={BASE + 18}
          text-anchor="middle"
          class={cn("text-[0.6875rem]", {
            "fill-foreground font-semibold": isFinal,
            "fill-muted-foreground": !isFinal,
          })}>{m.live_final()}</text
        >
      {/if}
      {#if !isFinal}
        <g
          class="transition-transform duration-300 ease-out motion-reduce:transition-none"
          style:transform="translateX({xs[position] ?? PAD}px)"
        >
          <line
            y1={4}
            y2={BASE + 4}
            class="stroke-foreground"
            stroke-width="2"
          />
          <circle
            cy={BASE}
            r="6.5"
            class="fill-foreground stroke-card"
            stroke-width="2.5"
          />
        </g>
      {/if}
    </svg>
  </div>
</div>
