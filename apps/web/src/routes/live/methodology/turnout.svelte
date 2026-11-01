<script lang="ts">
  import { percent } from "#lib/format.js";
  import { compact } from "#lib/live/format.js";
  import { m } from "#lib/paraglide/messages.js";

  const COUNTED = 80_000;
  const EXPECTED = 100_000;
  const DONE = 0.4;
  const MIN = 70_000;
  const MAX = 140_000;
  const LEFT = 12;
  const RIGHT = 12;
  const TOP = 34;
  const BOTTOM = 46;
  const HEIGHT = 236;

  let width = $state(640);

  const x = (votes: number) =>
    LEFT + ((votes - MIN) / (MAX - MIN)) * (width - LEFT - RIGHT);
  const curve = $derived.by(() => {
    const steps: Array<[number, number]> = [];
    for (let votes = COUNTED; votes <= MAX; votes += 500) {
      const z = Math.log(votes / EXPECTED) / 0.12;
      steps.push([votes, Math.exp(-0.5 * z * z)]);
    }
    let top = 0;
    for (const [, density] of steps) {
      top = Math.max(top, density);
    }
    const base = HEIGHT - BOTTOM;
    const scale = (base - TOP) * 0.62;
    const points = steps
      .map(
        ([votes, density]) => `${x(votes)},${base - (density / top) * scale}`,
      )
      .join("L");
    return `M${x(COUNTED)},${base}L${points}L${x(MAX)},${base}Z`;
  });
  const ticks = [70_000, 80_000, 100_000, 120_000, 140_000];
</script>

<figure class="grid gap-3">
  <div bind:clientWidth={width}>
    <svg
      viewBox="0 0 {width} {HEIGHT}"
      class="block inline-full"
      style:block-size="{HEIGHT}px"
      role="img"
      aria-label={m.method_turnout_caption()}
    >
      <path
        d={curve}
        class="fill-foreground/12 stroke-foreground"
        stroke-width="1.5"
      />
      <rect
        x={x(COUNTED)}
        y={TOP}
        width="8"
        height={HEIGHT - BOTTOM - TOP}
        class="fill-foreground"
      />
      <text
        x={x(COUNTED) + 16}
        y={TOP + 4}
        dy="0.6em"
        class="fill-foreground text-[0.8125rem] font-semibold"
        >{m.method_turnout_done()}
        <tspan class="font-figures tabular-nums">{percent(DONE, 0)}</tspan
        ></text
      >
      <text
        x={x(EXPECTED)}
        y={TOP + 44}
        text-anchor="middle"
        class="fill-foreground text-[0.8125rem] font-semibold"
        >{m.method_turnout_open()}
        <tspan class="font-figures tabular-nums">{percent(1 - DONE, 0)}</tspan
        ></text
      >
      <line
        x1={x(EXPECTED)}
        x2={x(EXPECTED)}
        y1={TOP + 54}
        y2={HEIGHT - BOTTOM}
        class="stroke-foreground"
        stroke-dasharray="3 3"
      />
      <line
        x1={LEFT}
        x2={width - RIGHT}
        y1={HEIGHT - BOTTOM}
        y2={HEIGHT - BOTTOM}
        class="stroke-foreground"
      />
      {#each ticks as tick (tick)}
        <text
          x={x(tick)}
          y={HEIGHT - BOTTOM + 16}
          text-anchor={tick === MIN ? "start" : tick === MAX ? "end" : "middle"}
          class="fill-muted-foreground font-figures text-[0.6875rem] tabular-nums"
          >{compact(tick)}</text
        >
      {/each}
      <text
        x={x(COUNTED) - 6}
        y={TOP}
        dy="0.6em"
        text-anchor="end"
        class="fill-muted-foreground text-[0.6875rem]"
        >{m.method_turnout_counted()}</text
      >
      <text
        x={x(EXPECTED) + 6}
        y={HEIGHT - BOTTOM - 8}
        class="fill-muted-foreground text-[0.6875rem]"
        >{m.method_turnout_expected()}</text
      >
      <text
        x={(LEFT + width - RIGHT) / 2}
        y={HEIGHT - 4}
        text-anchor="middle"
        class="fill-muted-foreground text-[0.6875rem]"
        >{m.method_turnout_axis()}</text
      >
    </svg>
  </div>
  <figcaption class="text-xs text-muted-foreground max-inline-[64ch]">
    {m.method_turnout_caption()}
  </figcaption>
</figure>
