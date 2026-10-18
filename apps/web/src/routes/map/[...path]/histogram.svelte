<script lang="ts">
  import { Slider } from "#lib/components/ui/slider/index.js";

  let {
    bars,
    domain,
    step,
    marker,
    ticks,
    label,
    range = $bindable(null),
  }: {
    bars: Array<{ count: number; color: string; isShown: boolean }>;
    domain: [number, number];
    step: number;
    marker: number | null;
    ticks: Array<{ at: number; label: string }>;
    label: string;
    range?: [number, number] | null;
  } = $props();

  const tallest = $derived(Math.max(1, ...bars.map((bar) => bar.count)));
  const span = $derived(domain[1] - domain[0]);
  const place = (value: number) =>
    Math.min(1, Math.max(0, (value - domain[0]) / span));
  let anchor: number | null = null;
  let isMoved = false;

  const pick = (event: PointerEvent, node: SVGSVGElement) => {
    const box = node.getBoundingClientRect();
    const at = (event.clientX - box.left) / Math.max(1, box.width);
    const value = domain[0] + Math.min(1, Math.max(0, at)) * span;
    return Math.round(value / step) * step;
  };
</script>

<div class="grid gap-1.5">
  <svg
    class="cursor-crosshair touch-none overflow-visible block-14 inline-full"
    viewBox="0 0 {bars.length} 40"
    preserveAspectRatio="none"
    role="presentation"
    onpointerdown={(event) => {
      const node = event.currentTarget;
      node.setPointerCapture(event.pointerId);
      anchor = pick(event, node);
      isMoved = false;
    }}
    onpointermove={(event) => {
      if (anchor == null) {
        return;
      }
      const value = pick(event, event.currentTarget);
      if (value !== anchor) {
        isMoved = true;
        range = [Math.min(anchor, value), Math.max(anchor, value)];
      }
    }}
    onpointerup={() => {
      if (!isMoved) {
        range = null;
      }
      anchor = null;
    }}
  >
    {#each bars as bar, index (index)}
      <rect
        x={index + 0.08}
        y={40 - (bar.count / tallest) * 38}
        width="0.84"
        height={Math.max(bar.count > 0 ? 0.8 : 0, (bar.count / tallest) * 38)}
        style:fill={bar.color}
        opacity={bar.isShown ? 1 : 0.22}
      />
    {/each}
    <line
      x1="0"
      x2={bars.length}
      y1="40"
      y2="40"
      class="stroke-border"
      stroke-width="1"
      vector-effect="non-scaling-stroke"
    />
    {#if marker != null}
      <line
        x1={place(marker) * bars.length}
        x2={place(marker) * bars.length}
        y1="-3"
        y2="40"
        class="stroke-foreground"
        stroke-width="1.5"
        vector-effect="non-scaling-stroke"
      />
    {/if}
  </svg>
  <Slider
    type="multiple"
    min={domain[0]}
    max={domain[1]}
    {step}
    value={range ?? [domain[0], domain[1]]}
    onValueChange={(value: Array<number>) => {
      const [low = domain[0], high = domain[1]] = value;
      range =
        Math.abs(low - domain[0]) < step / 2 &&
        Math.abs(high - domain[1]) < step / 2
          ? null
          : [low, high];
    }}
    aria-label={label}
    class="-mbs-1"
  />
  <div class="relative text-[0.6875rem] text-muted-foreground block-4">
    {#each ticks as tick, index (index)}
      <span
        class="absolute font-figures whitespace-nowrap tabular-nums"
        style:inset-inline-start="{place(tick.at) * 100}%"
        style:translate={index === 0
          ? "0"
          : index === ticks.length - 1
            ? "-100%"
            : "-50%"}>{tick.label}</span
      >
    {/each}
  </div>
</div>
