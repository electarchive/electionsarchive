<script lang="ts">
  import type { Family } from "#lib/live/nation.js";
  import { count, percent } from "#lib/format.js";
  import { clock, compact, lead } from "#lib/live/format.js";
  import { m } from "#lib/paraglide/messages.js";

  const {
    points,
    sides,
  }: {
    points: Array<{ at: number; left: number; right: number; other: number }>;
    sides: [Family, Family];
  } = $props();

  const left = $derived(sides[0]);
  const right = $derived(sides[1]);
  const id = $props.id();
  const GUTTER = 36;
  const TOP = 112;
  const BOTTOM = 32;
  const GAP = 10;

  let probe = $state<number | null>(null);
  let measured = $state(0);
  const WIDTH = $derived(Math.max(120, measured - GUTTER));

  const series = $derived(
    points.map((point) => {
      const total = point.left + point.right + point.other;
      return {
        ...point,
        total,
        margin: total > 0 ? (point.left - point.right) / total : 0,
      };
    }),
  );
  const counted = $derived(series.filter((point) => point.total > 0));
  const bounds = $derived.by(() => {
    let low = 0;
    let high = 0;
    for (const point of counted) {
      low = Math.min(low, point.margin);
      high = Math.max(high, point.margin);
    }
    const span = Math.max(0.04, high - low);
    const pad = span * 0.15;
    const grow = (span - (high - low)) / 2;
    return { low: low - pad - grow, high: high + pad + grow };
  });
  const zero = $derived(y(0));
  const most = $derived(Math.max(1, ...series.map((point) => point.total)));
  const start = $derived(series[0]?.at ?? 0);
  const end = $derived(series.at(-1)?.at ?? 1);

  function x(at: number) {
    return end === start ? WIDTH : ((at - start) / (end - start)) * WIDTH;
  }

  function y(margin: number) {
    return ((bounds.high - margin) / (bounds.high - bounds.low)) * TOP;
  }

  const line = $derived(
    counted
      .map(
        (point, index) =>
          `${index === 0 ? "M" : "L"}${x(point.at).toFixed(1)},${y(point.margin).toFixed(1)}`,
      )
      .join(""),
  );
  const area = $derived(
    counted.length === 0
      ? ""
      : `${line}L${x(counted.at(-1)?.at ?? 0).toFixed(1)},${zero.toFixed(1)}L${x(counted[0]?.at ?? 0).toFixed(1)},${zero.toFixed(1)}Z`,
  );
  const volume = $derived(
    series.length === 0
      ? ""
      : `M0,${TOP + GAP + BOTTOM}${series
          .map(
            (point) =>
              `L${x(point.at).toFixed(1)},${(TOP + GAP + BOTTOM - (point.total / most) * BOTTOM).toFixed(1)}`,
          )
          .join("")}L${WIDTH},${TOP + GAP + BOTTOM}Z`,
  );
  const shown = $derived(
    (probe == null ? null : series[probe]) ?? series.at(-1) ?? null,
  );
  const ticks = $derived.by(() => {
    const span = bounds.high - bounds.low;
    const step =
      span > 0.3 ? 0.1 : span > 0.15 ? 0.05 : span > 0.06 ? 0.02 : 0.01;
    const found: Array<number> = [];
    for (let value = step; value < bounds.high; value += step) {
      found.push(value);
    }
    for (let value = -step; value > bounds.low; value -= step) {
      found.push(value);
    }
    return found;
  });

  function track(event: PointerEvent) {
    const box = (event.currentTarget as SVGElement).getBoundingClientRect();
    const at = start + ((event.clientX - box.left) / WIDTH) * (end - start);
    let best = 0;
    for (const [index, point] of series.entries()) {
      if (Math.abs(point.at - at) < Math.abs((series[best]?.at ?? 0) - at)) {
        best = index;
      }
    }
    probe = best;
  }
</script>

<section
  class="grid grid-cols-[minmax(0,1fr)] content-start gap-3 bg-background px-4 py-5 sm:px-8"
>
  <div class="flex flex-wrap items-end justify-between gap-x-6 gap-y-2">
    <div class="grid gap-0.5">
      <h2 class="heading text-lg!">{m.night_pulse_title()}</h2>
      <p class="text-xs text-muted-foreground">{m.night_pulse_help()}</p>
    </div>
    {#if shown != null && shown.total > 0}
      <dl class="flex gap-5 font-figures text-sm tabular-nums">
        <div class="grid">
          <dt class="text-xs" style:color={left.color}>{left.short}</dt>
          <dd class="font-semibold">{percent(shown.left / shown.total, 1)}</dd>
        </div>
        <div class="grid">
          <dt class="text-xs" style:color={right.color}>{right.short}</dt>
          <dd class="font-semibold">{percent(shown.right / shown.total, 1)}</dd>
        </div>
        <div class="grid">
          <dt class="text-xs text-muted-foreground">{m.night_pulse_other()}</dt>
          <dd>{percent(shown.other / shown.total, 1)}</dd>
        </div>
        <div class="grid">
          <dt class="text-xs text-muted-foreground">
            {m.night_pulse_margin()}
          </dt>
          <dd
            class="font-semibold"
            style:color={shown.margin >= 0 ? left.color : right.color}
          >
            {shown.margin >= 0
              ? left.short.slice(0, 1)
              : right.short.slice(0, 1)}+{lead(shown.margin)}
          </dd>
        </div>
        <div class="grid">
          <dt class="text-xs text-muted-foreground">{clock(shown.at)}</dt>
          <dd>{m.night_pulse_votes({ count: count(shown.total) })}</dd>
        </div>
      </dl>
    {/if}
  </div>
  {#if counted.length < 2}
    <p
      class="grid place-items-center rounded-sm border border-dashed border-border text-sm text-muted-foreground block-40"
    >
      {m.night_pulse_empty()}
    </p>
  {:else}
    <div class="overflow-hidden" bind:clientWidth={measured}>
      <svg
        width={WIDTH + GUTTER}
        height={TOP + GAP + BOTTOM + 22}
        viewBox="0 -4 {WIDTH + GUTTER} {TOP + GAP + BOTTOM + 22}"
        class="block overflow-visible select-none"
        role="img"
        aria-label={m.night_pulse_label()}
        onpointermove={track}
        onpointerleave={() => (probe = null)}
      >
        <defs>
          <clipPath id="{id}-top"
            ><rect x="0" y="-4" width={WIDTH} height={zero + 4} /></clipPath
          >
          <clipPath id="{id}-bottom"
            ><rect
              x="0"
              y={zero}
              width={WIDTH}
              height={TOP - zero + 4}
            /></clipPath
          >
        </defs>
        {#each ticks as tick (tick)}
          <line
            x1="0"
            x2={WIDTH}
            y1={y(tick)}
            y2={y(tick)}
            stroke="var(--border)"
            stroke-dasharray="2 3"
          />
          <text
            x={WIDTH + 4}
            y={y(tick) + 3}
            class="fill-muted-foreground font-figures text-[9px] tabular-nums"
            >{tick > 0
              ? left.short.slice(0, 1)
              : right.short.slice(0, 1)}+{lead(tick, 0)}</text
          >
        {/each}
        <path
          d={area}
          fill={left.color}
          opacity="0.16"
          clip-path="url(#{id}-top)"
        />
        <path
          d={area}
          fill={right.color}
          opacity="0.16"
          clip-path="url(#{id}-bottom)"
        />
        <line
          x1="0"
          x2={WIDTH}
          y1={zero}
          y2={zero}
          stroke="var(--foreground)"
          stroke-width="1"
        />
        <path
          d={line}
          fill="none"
          stroke={left.color}
          stroke-width="2"
          stroke-linejoin="round"
          clip-path="url(#{id}-top)"
        />
        <path
          d={line}
          fill="none"
          stroke={right.color}
          stroke-width="2"
          stroke-linejoin="round"
          clip-path="url(#{id}-bottom)"
        />
        <path d={volume} fill="var(--foreground)" opacity="0.14" />
        <text
          x={WIDTH + 4}
          y={TOP + GAP + 8}
          class="fill-muted-foreground font-figures text-[9px] tabular-nums"
          >{compact(most)}</text
        >
        {#if shown != null}
          <line
            x1={x(shown.at)}
            x2={x(shown.at)}
            y1="-4"
            y2={TOP + GAP + BOTTOM}
            stroke="var(--foreground)"
            stroke-opacity={probe == null ? 0 : 0.5}
          />
          {#if shown.total > 0}
            <circle
              cx={x(shown.at)}
              cy={y(shown.margin)}
              r="3.5"
              fill={shown.margin >= 0 ? left.color : right.color}
              stroke="var(--background)"
              stroke-width="1.5"
            />
          {/if}
        {/if}
        <text
          x="0"
          y={TOP + GAP + BOTTOM + 16}
          class="fill-muted-foreground font-figures text-[10px] tabular-nums"
          >{clock(start)}</text
        >
        <text
          x={WIDTH}
          y={TOP + GAP + BOTTOM + 16}
          text-anchor="end"
          class="fill-muted-foreground font-figures text-[10px] tabular-nums"
          >{clock(end)}</text
        >
      </svg>
    </div>
  {/if}
</section>
