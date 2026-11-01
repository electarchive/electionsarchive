<script lang="ts">
  import { count } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { signed } from "./scale.js";

  const {
    values,
    blue,
    red,
    names,
  }: {
    values: Array<number>;
    blue: string;
    red: string;
    names: [string, string];
  } = $props();

  const LEFT = 12;
  const RIGHT = 12;
  const BOTTOM = 26;
  const RADIUS = 2.6;

  let width = $state(640);

  const low = $derived(Math.min(-0.02, ...values) - 0.005);
  const high = $derived(Math.max(0.02, ...values) + 0.005);
  const columns = $derived(
    Math.max(20, Math.floor((width - LEFT - RIGHT) / (RADIUS * 2 + 1))),
  );
  const step = $derived((high - low) / columns);
  const x = (margin: number) =>
    LEFT + ((margin - low) / (high - low)) * (width - LEFT - RIGHT);
  const dots = $derived.by(() => {
    const stacks = new Map<number, number>();
    const placed: Array<{ cx: number; level: number; value: number }> = [];
    for (const value of values) {
      const column = Math.floor((value - low) / step);
      const level = stacks.get(column) ?? 0;
      stacks.set(column, level + 1);
      placed.push({ cx: x(low + (column + 0.5) * step), level, value });
    }
    return placed;
  });
  const levels = $derived(Math.max(1, ...dots.map((dot) => dot.level + 1)));
  const height = $derived(levels * (RADIUS * 2 + 1) + BOTTOM + 30);
  const wins = $derived(values.filter((value) => value > 0).length);
  const ticks = [-0.05, 0, 0.05];
</script>

<figure class="grid gap-3">
  <div bind:clientWidth={width}>
    <svg
      viewBox="0 0 {width} {height}"
      class="block inline-full"
      style:block-size="{height}px"
      role="img"
      aria-label={m.method_ranges_caption()}
    >
      <text
        x={width - RIGHT}
        y="12"
        text-anchor="end"
        class="text-[0.8125rem] font-semibold"
        fill={blue}
        >{m.method_ranges_wins({ name: names[0], count: count(wins) })}</text
      >
      <text x={LEFT} y="12" class="text-[0.8125rem] font-semibold" fill={red}
        >{m.method_ranges_wins({
          name: names[1],
          count: count(values.length - wins),
        })}</text
      >
      {#each dots as dot, position (position)}
        <circle
          cx={dot.cx}
          cy={height - BOTTOM - RADIUS - 1 - dot.level * (RADIUS * 2 + 1)}
          r={RADIUS}
          fill={dot.value > 0 ? blue : red}
        />
      {/each}
      <line
        x1={x(0)}
        x2={x(0)}
        y1="22"
        y2={height - BOTTOM}
        class="stroke-foreground"
        stroke-dasharray="3 3"
      />
      <line
        x1={LEFT}
        x2={width - RIGHT}
        y1={height - BOTTOM}
        y2={height - BOTTOM}
        class="stroke-foreground"
      />
      {#each ticks as tick (tick)}
        {#if tick > low && tick < high}
          <text
            x={x(tick)}
            y={height - 8}
            text-anchor="middle"
            class="font-figures text-[0.6875rem] tabular-nums"
            fill={tick === 0
              ? "var(--muted-foreground)"
              : tick > 0
                ? blue
                : red}>{signed(tick, m.method_even())}</text
          >
        {/if}
      {/each}
    </svg>
  </div>
  <figcaption class="text-xs text-muted-foreground max-inline-[64ch]">
    {m.method_ranges_caption()}
  </figcaption>
</figure>
