<script lang="ts">
  import type { SvelteSet } from "svelte/reactivity";
  import type { orpc } from "#lib/rpc.js";
  import { percent } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";

  let {
    contest,
    focus,
    picked,
    hovered = $bindable(),
  }: {
    contest: Awaited<ReturnType<typeof orpc.contests.get.call>>;
    focus: { key: string; label: string; color: string | null } | null;
    picked: SvelteSet<string>;
    hovered: string | null;
  } = $props();

  const WIDTH = 480;
  const HEIGHT = 360;
  const PAD = 40;

  let brush = $state<{ x0: number; y0: number; x1: number; y1: number } | null>(
    null,
  );

  const at = $derived(
    contest.places?.legend.findIndex((entry) => entry.key === focus?.key) ?? -1,
  );
  const dots = $derived(
    (contest.places?.rows ?? []).flatMap((row) => {
      const earlier = row.earlier?.[at];
      const share = row.shares[at];
      return earlier == null || share == null
        ? []
        : [
            {
              name: row.name,
              x: earlier,
              y: share,
              lead:
                row.leader == null
                  ? null
                  : (contest.places?.legend[row.leader] ?? null),
            },
          ];
    }),
  );
  const domain = $derived.by(() => {
    let low = 1;
    let high = 0;
    for (const dot of dots) {
      low = Math.min(low, dot.x, dot.y);
      high = Math.max(high, dot.x, dot.y);
    }
    const pad = Math.max((high - low) * 0.05, 0.01);
    return [Math.max(0, low - pad), Math.min(1, high + pad)] as const;
  });
  const sx = (value: number) =>
    PAD + ((value - domain[0]) / (domain[1] - domain[0])) * (WIDTH - PAD * 1.5);
  const sy = (value: number) =>
    HEIGHT -
    PAD -
    ((value - domain[0]) / (domain[1] - domain[0])) * (HEIGHT - PAD * 1.5);
  const ticks = $derived.by(() => {
    const span = domain[1] - domain[0];
    const step =
      span > 0.5 ? 0.2 : span > 0.2 ? 0.1 : span > 0.08 ? 0.05 : 0.02;
    const found: Array<number> = [];
    for (
      let tick = Math.ceil(domain[0] / step) * step;
      tick <= domain[1];
      tick += step
    ) {
      found.push(Math.round(tick * 1000) / 1000);
    }
    return found;
  });
  const isDimmed = $derived(picked.size > 0);
  const point = (svg: SVGSVGElement, event: PointerEvent) => {
    const matrix = svg.getScreenCTM()?.inverse();
    const found = new DOMPoint(event.clientX, event.clientY).matrixTransform(
      matrix,
    );
    return { x: found.x, y: found.y };
  };
  const release = () => {
    if (brush == null) {
      return;
    }
    const left = Math.min(brush.x0, brush.x1);
    const right = Math.max(brush.x0, brush.x1);
    const top = Math.min(brush.y0, brush.y1);
    const bottom = Math.max(brush.y0, brush.y1);
    if (right - left > 4 || bottom - top > 4) {
      picked.clear();
      for (const dot of dots) {
        const x = sx(dot.x);
        const y = sy(dot.y);
        if (x >= left && x <= right && y >= top && y <= bottom) {
          picked.add(dot.name);
        }
      }
    } else {
      const near = dots.find(
        (dot) => Math.hypot(sx(dot.x) - left, sy(dot.y) - top) < 8,
      );
      if (near == null) {
        picked.clear();
      } else if (picked.has(near.name)) {
        picked.delete(near.name);
      } else {
        picked.add(near.name);
      }
    }
    brush = null;
  };
</script>

{#if contest.target == null}
  <p class="p-3.5 text-sm text-muted-foreground">
    {m.workbench_scatter_needs()}
  </p>
{:else if dots.length === 0}
  <p class="p-3.5 text-sm text-muted-foreground">{m.workbench_no_places()}</p>
{:else}
  <figure class="grid gap-2 p-3.5">
    <svg
      viewBox="0 0 {WIDTH} {HEIGHT}"
      class="touch-none select-none inline-full"
      role="img"
      aria-label={m.workbench_scatter_note({ name: focus?.label ?? "" })}
      onpointerdown={(event) => {
        const start = point(event.currentTarget, event);
        event.currentTarget.setPointerCapture(event.pointerId);
        brush = { x0: start.x, y0: start.y, x1: start.x, y1: start.y };
      }}
      onpointermove={(event) => {
        if (brush != null) {
          const next = point(event.currentTarget, event);
          brush = { ...brush, x1: next.x, y1: next.y };
        }
      }}
      onpointerup={release}
      onlostpointercapture={release}
    >
      {#each ticks as tick (tick)}
        <line
          x1={sx(tick)}
          x2={sx(tick)}
          y1={PAD / 2}
          y2={HEIGHT - PAD}
          class="stroke-border"
        />
        <line
          x1={PAD}
          x2={WIDTH - PAD / 2}
          y1={sy(tick)}
          y2={sy(tick)}
          class="stroke-border"
        />
        <text
          x={sx(tick)}
          y={HEIGHT - PAD + 16}
          text-anchor="middle"
          class="fill-muted-foreground font-mono text-[10px]"
          >{percent(tick)}</text
        >
        <text
          x={PAD - 6}
          y={sy(tick) + 3}
          text-anchor="end"
          class="fill-muted-foreground font-mono text-[10px]"
          >{percent(tick)}</text
        >
      {/each}
      <line
        x1={sx(domain[0])}
        y1={sy(domain[0])}
        x2={sx(domain[1])}
        y2={sy(domain[1])}
        class="stroke-foreground"
        stroke-dasharray="4 4"
      />
      {#each dots as dot (dot.name)}
        {const isPicked = $derived(picked.has(dot.name))}
        <circle
          cx={sx(dot.x)}
          cy={sy(dot.y)}
          r={hovered === dot.name ? 6 : isPicked ? 4.5 : 3.5}
          fill={dot.lead == null
            ? "currentColor"
            : swatch(dot.lead.color, dot.lead.key)}
          fill-opacity={isDimmed && !isPicked ? 0.2 : 0.85}
          class:stroke-foreground={hovered === dot.name || isPicked}
          role="presentation"
          onpointerenter={() => (hovered = dot.name)}
          onpointerleave={() => (hovered = null)}
        >
          <title>{dot.name}: {percent(dot.x)}, {percent(dot.y)}</title>
        </circle>
      {/each}
      {#if brush != null}
        <rect
          x={Math.min(brush.x0, brush.x1)}
          y={Math.min(brush.y0, brush.y1)}
          width={Math.abs(brush.x1 - brush.x0)}
          height={Math.abs(brush.y1 - brush.y0)}
          class="fill-foreground/10 stroke-foreground"
        />
      {/if}
    </svg>
    <figcaption
      class="flex flex-wrap justify-between gap-x-4 gap-y-1 text-xs text-muted-foreground"
    >
      <span
        >{m.workbench_scatter_x({ year: String(contest.target.year ?? "") })} →
        {m.workbench_scatter_y({ year: String(contest.election.year ?? "") })} ↑</span
      >
      <span>{m.workbench_scatter_note({ name: focus?.label ?? "" })}</span>
    </figcaption>
  </figure>
{/if}
