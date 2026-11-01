<script lang="ts">
  import { numbers, percent } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";

  const {
    title,
    note,
    series,
    low,
    high,
    target,
    ticks,
    kind,
  }: {
    title: string;
    note: string;
    series: Array<{ q: number; current: number; previous: number }>;
    low: number;
    high: number;
    target?: number;
    ticks: Array<number>;
    kind: "score" | "share";
  } = $props();

  const LEFT = 42;
  const RIGHT = 10;
  const TOP = 10;
  const BOTTOM = 30;
  const HEIGHT = 200;

  let width = $state(320);

  const x = (q: number) => LEFT + q * (width - LEFT - RIGHT);
  const y = (value: number) =>
    TOP + ((high - value) / (high - low)) * (HEIGHT - TOP - BOTTOM);
  const line = (pick: (row: { current: number; previous: number }) => number) =>
    `M${series.map((row) => `${x(row.q)},${y(Math.max(low, Math.min(high, pick(row))))}`).join("L")}`;
  const current = $derived(line((row) => row.current));
  const previous = $derived(line((row) => row.previous));
  const label = (value: number) =>
    kind === "share"
      ? percent(value, 0)
      : numbers({ minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(
          value,
        );
</script>

<figure class="grid content-start gap-2">
  <figcaption class="grid gap-0.5">
    <span class="text-sm font-semibold">{title}</span>
    <span class="text-xs text-muted-foreground">{note}</span>
  </figcaption>
  <div bind:clientWidth={width}>
    <svg
      viewBox="0 0 {width} {HEIGHT}"
      class="block inline-full"
      style:block-size="{HEIGHT}px"
      aria-hidden="true"
    >
      {#each ticks as tick (tick)}
        <line
          x1={LEFT}
          x2={width - RIGHT}
          y1={y(tick)}
          y2={y(tick)}
          class="stroke-border"
        />
        <text
          x={LEFT - 6}
          y={y(tick)}
          dy="0.32em"
          text-anchor="end"
          class="fill-muted-foreground font-figures text-[0.6875rem] tabular-nums"
          >{label(tick)}</text
        >
      {/each}
      {#if target != null}
        <line
          x1={LEFT}
          x2={width - RIGHT}
          y1={y(target)}
          y2={y(target)}
          class="stroke-foreground"
          stroke-dasharray="2 3"
        />
        <text
          x={width - RIGHT}
          y={y(target) - 5}
          text-anchor="end"
          class="fill-foreground text-[0.6875rem]">{m.method_target()}</text
        >
      {/if}
      {#each [0, 0.5, 1] as share (share)}
        <text
          x={x(share)}
          y={HEIGHT - 10}
          text-anchor={share === 0 ? "start" : share === 1 ? "end" : "middle"}
          class="fill-muted-foreground font-figures text-[0.6875rem] tabular-nums"
          >{percent(share, 0)}</text
        >
      {/each}
      <path
        d={previous}
        fill="none"
        class="stroke-muted-foreground"
        stroke-width="1.75"
        stroke-dasharray="5 4"
      />
      <path
        d={current}
        fill="none"
        class="stroke-foreground"
        stroke-width="2.5"
      />
      {#each series as row (row.q)}
        <circle
          cx={x(row.q)}
          cy={y(Math.max(low, Math.min(high, row.current)))}
          r="3"
          class="fill-foreground"
        />
      {/each}
    </svg>
  </div>
  <div class="sr-only">
    <table>
      <thead>
        <tr>
          <th scope="col">{m.method_counted_axis()}</th>
          <th scope="col">{m.method_current()}</th>
          <th scope="col">{m.method_previous()}</th>
        </tr>
      </thead>
      <tbody>
        {#each series as row (row.q)}
          <tr>
            <th scope="row">{percent(row.q, 0)}</th>
            <td>{label(row.current)}</td>
            <td>{label(row.previous)}</td>
          </tr>
        {/each}
      </tbody>
    </table>
  </div>
</figure>
