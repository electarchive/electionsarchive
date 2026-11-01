<script lang="ts">
  import type { Snippet } from "svelte";

  type Point = {
    key: string;
    x: number;
    a: number | null;
    b: number | null;
    ref: number | null;
    marks: [string | null, string | null];
  };

  let {
    points,
    colors,
    fills,
    bands = null,
    low,
    high,
    format,
    active = $bindable(),
    readout,
  }: {
    points: Array<Point>;
    colors: [string, string];
    fills: [string, string];
    bands?: [string, string] | null;
    low: number;
    high: number;
    format: (value: number) => string;
    active?: string | null;
    readout: Snippet<[Point]>;
  } = $props();

  const PAD = { top: 10, right: 10, left: 38 };
  const STRIP = 46;
  const AXIS = 18;

  let width = $state(0);

  const height = $derived(
    Math.round(Math.min(300, Math.max(190, width * 0.3))),
  );
  const plot = $derived(height - PAD.top);
  const first = $derived(Math.min(...points.map((point) => point.x)));
  const last = $derived(Math.max(...points.map((point) => point.x)));
  const sx = (x: number) =>
    last === first
      ? PAD.left + (width - PAD.left - PAD.right) / 2
      : PAD.left +
        ((x - first) / (last - first)) * (width - PAD.left - PAD.right);
  const sy = (value: number) =>
    PAD.top + ((high - value) / (high - low)) * (plot - PAD.top);
  const both = $derived(
    points.filter(
      (point): point is Point & { a: number; b: number } =>
        point.a != null && point.b != null,
    ),
  );
  const reach = $derived(
    Math.max(0.01, ...both.map((point) => Math.abs(point.a - point.b))),
  );
  const leap = $derived.by(() => {
    const steps = points
      .slice(1)
      .map((point, index) => point.x - (points[index]?.x ?? point.x))
      .toSorted((x, y) => x - y);
    return Math.max(6, (steps[Math.floor(steps.length / 2)] ?? 4) * 2.5);
  });
  const ribbons = $derived.by(() => {
    const shapes: Array<{ points: string; side: number }> = [];
    for (const [index, one] of both.entries()) {
      const two = both[index + 1];
      if (two == null) {
        break;
      }
      if (two.x - one.x > leap) {
        continue;
      }
      const d1 = one.a - one.b;
      const d2 = two.a - two.b;
      const corner = (x: number, value: number) => `${sx(x)},${sy(value)}`;
      if (d1 * d2 >= 0) {
        shapes.push({
          side: d1 + d2 >= 0 ? 0 : 1,
          points: [
            corner(one.x, one.a),
            corner(two.x, two.a),
            corner(two.x, two.b),
            corner(one.x, one.b),
          ].join(" "),
        });
        continue;
      }
      const t = d1 / (d1 - d2);
      const x = one.x + t * (two.x - one.x);
      const y = one.a + t * (two.a - one.a);
      shapes.push(
        {
          side: d1 > 0 ? 0 : 1,
          points: [
            corner(one.x, one.a),
            corner(x, y),
            corner(one.x, one.b),
          ].join(" "),
        },
        {
          side: d2 > 0 ? 0 : 1,
          points: [
            corner(x, y),
            corner(two.x, two.a),
            corner(two.x, two.b),
          ].join(" "),
        },
      );
    }
    return shapes;
  });
  const lines = $derived(
    (["a", "b", "ref"] as const).map((field) => {
      let last: number | null = null;
      let path = "";
      for (const point of points) {
        const value = point[field];
        if (value != null) {
          path += `${last == null || point.x - last > leap ? "M" : "L"}${sx(point.x).toFixed(1)},${sy(value).toFixed(1)}`;
          last = point.x;
        }
      }
      return path;
    }),
  );
  const ticks = $derived.by(() => {
    const span = high - low;
    const raw = span / 4;
    const unit = 10 ** Math.floor(Math.log10(raw));
    const step =
      [1, 2, 2.5, 5, 10]
        .map((factor) => factor * unit)
        .find((value) => value >= raw) ?? unit * 10;
    const values: Array<number> = [];
    for (
      let value = Math.ceil(low / step) * step;
      value <= high + 1e-9;
      value += step
    ) {
      values.push(Math.round(value * 1e6) / 1e6);
    }
    return values;
  });
  const years = $derived.by(() => {
    const span = last - first;
    const count = Math.max(2, Math.min(9, Math.floor(width / 90)));
    const step = Math.max(1, Math.ceil(span / count / 2) * 2);
    const values: Array<number> = [];
    for (
      let year = Math.ceil(first / step) * step;
      year <= last;
      year += step
    ) {
      values.push(year);
    }
    return values;
  });
  const gap = $derived(
    Math.max(
      2,
      Math.min(
        9,
        ((width - PAD.left - PAD.right) / Math.max(1, points.length)) * 0.62,
      ),
    ),
  );
  const shown = $derived(points.find((point) => point.key === active) ?? null);

  function hover(event: PointerEvent) {
    const box = (event.currentTarget as SVGElement).getBoundingClientRect();
    const x = event.clientX - box.left;
    let best: Point | null = null;
    let distance = Infinity;
    for (const point of points) {
      const away = Math.abs(sx(point.x) - x);
      if (away < distance) {
        distance = away;
        best = point;
      }
    }
    active = best?.key ?? null;
  }
</script>

<div class="relative overflow-hidden" bind:clientWidth={width}>
  {#if width > 0}
    <svg
      {width}
      height={height + STRIP + AXIS}
      class="block touch-pan-y"
      role="img"
      aria-hidden="true"
      onpointermove={hover}
      onpointerdown={hover}
      onpointerleave={() => (active = null)}
    >
      {#if bands != null && low < 0 && high > 0}
        <rect
          x={PAD.left}
          y={PAD.top}
          width={width - PAD.left - PAD.right}
          height={sy(0) - PAD.top}
          style:fill={bands[0]}
          fill-opacity="0.07"
        />
        <rect
          x={PAD.left}
          y={sy(0)}
          width={width - PAD.left - PAD.right}
          height={plot - sy(0)}
          style:fill={bands[1]}
          fill-opacity="0.07"
        />
      {/if}
      {#each ticks as tick (tick)}
        <line
          x1={PAD.left}
          x2={width - PAD.right}
          y1={sy(tick)}
          y2={sy(tick)}
          class={tick === 0 ? "stroke-muted-foreground" : "stroke-border"}
          stroke-dasharray={tick === 0 ? null : "2 3"}
        />
        <text
          x={PAD.left - 6}
          y={sy(tick)}
          text-anchor="end"
          dominant-baseline="middle"
          class="fill-muted-foreground font-figures text-[0.625rem] tabular-nums"
          >{format(tick)}</text
        >
      {/each}
      {#each ribbons as ribbon, index (index)}
        <polygon
          points={ribbon.points}
          style:fill={fills[ribbon.side]}
          fill-opacity="0.2"
        />
      {/each}
      {#if shown != null}
        <line
          x1={sx(shown.x)}
          x2={sx(shown.x)}
          y1={PAD.top}
          y2={height + STRIP}
          class="stroke-foreground"
          stroke-opacity="0.35"
        />
      {/if}
      <path
        d={lines[2]}
        fill="none"
        class="stroke-muted-foreground"
        stroke-width="1.25"
        stroke-dasharray="1 3"
        stroke-linecap="round"
      />
      <path
        d={lines[1]}
        fill="none"
        style:stroke={colors[1]}
        stroke-width="1.75"
        stroke-dasharray="5 3"
        stroke-linejoin="round"
      />
      <path
        d={lines[0]}
        fill="none"
        style:stroke={colors[0]}
        stroke-width="2"
        stroke-linejoin="round"
      />
      {#each points as point (point.key)}
        {const isShown = $derived(point.key === shown?.key)}
        {#if point.b != null}
          {const r = $derived(isShown ? 4.5 : 3)}
          <rect
            x={sx(point.x) - r}
            y={sy(point.b) - r}
            width={r * 2}
            height={r * 2}
            style:fill={point.marks[1] ?? colors[1]}
            class="stroke-background"
          />
        {/if}
        {#if point.a != null}
          <circle
            cx={sx(point.x)}
            cy={sy(point.a)}
            r={isShown ? 5 : 3.5}
            style:fill={point.marks[0] ?? colors[0]}
            class="stroke-background"
          />
        {/if}
      {/each}
      <line
        x1={PAD.left}
        x2={width - PAD.right}
        y1={height + STRIP / 2}
        y2={height + STRIP / 2}
        class="stroke-border"
      />
      {#each both as point (point.key)}
        {const d = $derived(point.a - point.b)}
        {const h = $derived((Math.abs(d) / reach) * (STRIP / 2 - 3))}
        <rect
          x={sx(point.x) - gap / 2}
          y={d >= 0 ? height + STRIP / 2 - h : height + STRIP / 2}
          width={gap}
          height={Math.max(1, h)}
          style:fill={fills[d >= 0 ? 0 : 1]}
          fill-opacity={point.key === shown?.key ? 1 : 0.75}
        />
      {/each}
      {#each years as year (year)}
        <text
          x={sx(year)}
          y={height + STRIP + AXIS - 4}
          text-anchor="middle"
          class="fill-muted-foreground font-figures text-[0.625rem] tabular-nums"
          >{year}</text
        >
      {/each}
    </svg>
    {#if shown != null}
      <div
        class="pointer-events-none absolute inset-bs-1 border border-border bg-background/95 px-2 py-1.5 text-xs shadow-sm max-inline-64"
        style:inset-inline-start={sx(shown.x) > width / 2
          ? `${PAD.left + 4}px`
          : null}
        style:inset-inline-end={sx(shown.x) > width / 2
          ? null
          : `${PAD.right + 4}px`}
      >
        {@render readout(shown)}
      </div>
    {/if}
  {/if}
</div>
