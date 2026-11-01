<script lang="ts">
  import { percent } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";

  const { bins }: { bins: Array<{ p: number; o: number; n: number }> } =
    $props();

  const PAD = 34;

  const size = 360;
  const x = (value: number) => PAD + value * (size - PAD - 8);
  const y = (value: number) => 8 + (1 - value) * (size - PAD - 8);
  const largest = $derived(Math.max(...bins.map((bin) => bin.n)));
  const radius = (n: number) => 3 + 13 * Math.sqrt(n / largest);
  const ticks = [0, 0.5, 1];
</script>

<figure class="grid content-start gap-2">
  <figcaption class="grid gap-0.5">
    <span class="text-sm font-semibold">{m.method_calibration_chart()}</span>
    <span class="text-xs text-muted-foreground"
      >{m.method_calibration_note()}</span
    >
  </figcaption>
  <div class="grid gap-1">
    <span class="text-[0.6875rem] text-muted-foreground" aria-hidden="true"
      >{m.method_calibration_observed()}</span
    >
    <svg
      viewBox="0 0 {size} {size}"
      class="block aspect-square inline-full max-inline-90"
      aria-hidden="true"
    >
      {#each ticks as tick (tick)}
        <line
          x1={x(0)}
          x2={x(1)}
          y1={y(tick)}
          y2={y(tick)}
          class="stroke-border"
        />
        <line
          x1={x(tick)}
          x2={x(tick)}
          y1={y(0)}
          y2={y(1)}
          class="stroke-border"
        />
        <text
          x={x(0) - 6}
          y={y(tick)}
          dy="0.32em"
          text-anchor="end"
          class="fill-muted-foreground font-figures text-[0.6875rem] tabular-nums"
          >{percent(tick, 0)}</text
        >
        <text
          x={x(tick)}
          y={y(0) + 14}
          text-anchor={tick === 0 ? "start" : tick === 1 ? "end" : "middle"}
          class="fill-muted-foreground font-figures text-[0.6875rem] tabular-nums"
          >{percent(tick, 0)}</text
        >
      {/each}
      <line
        x1={x(0)}
        y1={y(0)}
        x2={x(1)}
        y2={y(1)}
        class="stroke-foreground"
        stroke-dasharray="3 3"
      />
      <text
        x={x(0.62)}
        y={y(0.58)}
        dy="0.9em"
        class="fill-muted-foreground text-[0.6875rem]"
        transform="rotate(-45 {x(0.62)} {y(0.58)})">{m.method_perfect()}</text
      >
      {#each bins as bin (bin.p)}
        <circle
          cx={x(bin.p)}
          cy={y(bin.o)}
          r={radius(bin.n)}
          class="fill-foreground/15 stroke-foreground"
          stroke-width="1.5"
        />
      {/each}
      <text
        x={x(0.5)}
        y={size - 2}
        text-anchor="middle"
        class="fill-muted-foreground text-[0.6875rem]"
        >{m.method_calibration_stated()}</text
      >
    </svg>
  </div>
  <div class="sr-only">
    <table>
      <thead>
        <tr>
          <th scope="col">{m.method_calibration_stated()}</th>
          <th scope="col">{m.method_calibration_observed()}</th>
        </tr>
      </thead>
      <tbody>
        {#each bins as bin (bin.p)}
          <tr>
            <td>{percent(bin.p, 0)}</td>
            <td>{percent(bin.o, 0)}</td>
          </tr>
        {/each}
      </tbody>
    </table>
  </div>
</figure>
