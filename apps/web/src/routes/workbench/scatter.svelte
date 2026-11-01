<script lang="ts">
  import type { SvelteSet } from "svelte/reactivity";
  import type { orpc } from "#lib/rpc.js";
  import { count, percent, plural, points } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";
  import { cn } from "#lib/utils.js";

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

  const PAD = { left: 40, right: 14, top: 12, bottom: 26 };

  let width = $state(0);
  let height = $state(0);
  let brush = $state<{ x0: number; y0: number; x1: number; y1: number } | null>(
    null,
  );

  const at = $derived(
    contest.places?.legend.findIndex((entry) => entry.key === focus?.key) ?? -1,
  );
  const tint = $derived(
    focus == null ? "var(--foreground)" : swatch(focus.color, focus.key),
  );
  const dots = $derived(
    (contest.places?.rows ?? [])
      .flatMap((row) => {
        const earlier = row.earlier?.[at];
        const share = row.shares[at];
        return earlier == null || share == null
          ? []
          : [
              {
                k: row.k,
                name: row.name,
                x: earlier,
                y: share - earlier,
                share,
                valid: row.valid,
                lead:
                  row.leader == null
                    ? null
                    : (contest.places?.legend[row.leader] ?? null),
              },
            ];
      })
      .toSorted((a, b) => b.valid - a.valid),
  );
  const stats = $derived.by(() => {
    let weight = 0;
    let sx = 0;
    let sy = 0;
    let most = 1;
    let gained = 0;
    for (const dot of dots) {
      weight += dot.valid;
      sx += dot.x * dot.valid;
      sy += dot.y * dot.valid;
      most = Math.max(most, dot.valid);
      if (dot.y > 0) {
        gained += 1;
      }
    }
    const mx = weight === 0 ? 0 : sx / weight;
    const my = weight === 0 ? 0 : sy / weight;
    let sxy = 0;
    let sxx = 0;
    for (const dot of dots) {
      sxy += dot.valid * (dot.x - mx) * (dot.y - my);
      sxx += dot.valid * (dot.x - mx) ** 2;
    }
    const slope = sxx === 0 ? 0 : sxy / sxx;
    return { mx, my, slope, most, gained };
  });
  const domain = $derived.by(() => {
    let left = 1;
    let right = 0;
    let low = 0;
    let high = 0;
    for (const dot of dots) {
      left = Math.min(left, dot.x);
      right = Math.max(right, dot.x);
      low = Math.min(low, dot.y);
      high = Math.max(high, dot.y);
    }
    const across = Math.max((right - left) * 0.04, 0.01);
    const tall = Math.max((high - low) * 0.08, 0.005);
    return {
      x: [Math.max(0, left - across), Math.min(1, right + across)] as const,
      y: [low - tall, high + tall] as const,
    };
  });
  const inner = $derived({
    w: Math.max(1, width - PAD.left - PAD.right),
    h: Math.max(1, height - PAD.top - PAD.bottom),
  });
  const sx = (value: number) =>
    PAD.left + ((value - domain.x[0]) / (domain.x[1] - domain.x[0])) * inner.w;
  const sy = (value: number) =>
    PAD.top +
    (1 - (value - domain.y[0]) / (domain.y[1] - domain.y[0])) * inner.h;
  const radius = (valid: number) => 1.75 + 9 * Math.sqrt(valid / stats.most);
  const steps = (low: number, high: number, room: number) => {
    const span = high - low;
    const wanted = Math.max(2, Math.floor(room / 64));
    const raw = span / wanted;
    const magnitude = 10 ** Math.floor(Math.log10(raw));
    const step =
      [1, 2, 2.5, 5, 10]
        .map((factor) => factor * magnitude)
        .find((value) => value >= raw) ?? raw;
    const found: Array<number> = [];
    for (let tick = Math.ceil(low / step) * step; tick <= high; tick += step) {
      found.push(Math.round(tick * 1e6) / 1e6);
    }
    return found;
  };
  const xs = $derived(steps(domain.x[0], domain.x[1], inner.w));
  const ys = $derived(steps(domain.y[0], domain.y[1], inner.h * 1.3));
  const labeled = $derived.by(() => {
    if (width === 0) {
      return [];
    }
    const floor =
      dots[Math.min(dots.length - 1, Math.floor(dots.length * 0.75))]?.valid ??
      0;
    const ranked = dots
      .filter((dot) => dot.valid >= floor)
      .toSorted((a, b) => b.y - a.y);
    const wanted = [
      ...ranked.slice(0, 3),
      ...ranked.slice(-3).filter((dot) => dot.y < stats.my),
      ...dots.filter((dot) => picked.has(dot.name)).slice(0, 6),
    ];
    const placed: Array<{ x: number; y: number; name: string; k: number }> = [];
    for (const dot of wanted) {
      const x = sx(dot.x);
      const y = sy(dot.y) - radius(dot.valid) - 3;
      const isClear = placed.every(
        (other) =>
          other.k !== dot.k &&
          (Math.abs(other.y - y) > 11 || Math.abs(other.x - x) > 90),
      );
      if (isClear) {
        placed.push({ x, y, name: dot.name, k: dot.k });
      }
    }
    return placed;
  });
  const shown = $derived(dots.find((dot) => dot.name === hovered) ?? null);
  const trend = $derived(
    Math.abs(stats.slope) < 0.012 || stats.my <= 0
      ? null
      : stats.slope > 0
        ? m.workbench_shift_trend_up({ name: focus?.label ?? "" })
        : m.workbench_shift_trend_down({ name: focus?.label ?? "" }),
  );
  const noun = $derived(
    plural(
      contest.places?.levels.find(
        (level) => level.code === contest.places?.level,
      )?.noun ?? "",
    ),
  );
  const isDimmed = $derived(picked.size > 0);
  const point = (svg: SVGSVGElement, event: PointerEvent) => {
    const box = svg.getBoundingClientRect();
    return { x: event.clientX - box.left, y: event.clientY - box.top };
  };
  const nearest = (x: number, y: number) => {
    let best: (typeof dots)[number] | null = null;
    let distance = 24;
    for (const dot of dots) {
      const away =
        Math.hypot(sx(dot.x) - x, sy(dot.y) - y) - radius(dot.valid) * 0.5;
      if (away < distance) {
        distance = away;
        best = dot;
      }
    }
    return best;
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
      const near = nearest(left, top);
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
  <figure
    class="grid grid-rows-[auto_minmax(0,1fr)_auto] gap-2 p-3.5 lg:block-full"
  >
    <div
      class="flex flex-wrap items-baseline gap-x-4 gap-y-0.5 text-[0.8125rem]"
    >
      <span class="font-semibold"
        >{m.workbench_shift_gained({
          name: focus?.label ?? "",
          count: count(stats.gained),
          total: count(dots.length),
          noun,
        })}</span
      >
      <span class="font-figures font-semibold tabular-nums" style:color={tint}
        >{m.workbench_shift_overall({ change: points(stats.my) })}</span
      >
      {#if trend != null}
        <span class="text-muted-foreground">{trend}</span>
      {/if}
    </div>
    <div
      class="relative aspect-10/7 min-block-0 lg:aspect-auto lg:min-block-48"
      bind:clientWidth={width}
      bind:clientHeight={height}
    >
      {#if width > 0 && height > 0}
        <svg
          {width}
          {height}
          class="absolute inset-0 touch-none select-none"
          role="img"
          aria-label={m.workbench_scatter_note({ name: focus?.label ?? "" })}
          onpointerdown={(event) => {
            const start = point(event.currentTarget, event);
            event.currentTarget.setPointerCapture(event.pointerId);
            brush = { x0: start.x, y0: start.y, x1: start.x, y1: start.y };
          }}
          onpointermove={(event) => {
            const next = point(event.currentTarget, event);
            if (brush == null) {
              hovered = nearest(next.x, next.y)?.name ?? null;
            } else {
              brush = { ...brush, x1: next.x, y1: next.y };
            }
          }}
          onpointerleave={() => {
            if (brush == null) {
              hovered = null;
            }
          }}
          onpointerup={release}
          onlostpointercapture={release}
        >
          <rect
            x={PAD.left}
            y={PAD.top}
            width={inner.w}
            height={Math.max(0, sy(0) - PAD.top)}
            style:fill={tint}
            fill-opacity="0.045"
          />
          {#each xs as tick (tick)}
            <line
              x1={sx(tick)}
              x2={sx(tick)}
              y1={PAD.top}
              y2={PAD.top + inner.h}
              class="stroke-border"
              stroke-dasharray="2 3"
            />
            <text
              x={sx(tick)}
              y={height - 8}
              text-anchor="middle"
              class="fill-muted-foreground font-figures text-[0.625rem] tabular-nums"
              >{percent(tick, 0)}</text
            >
          {/each}
          {#each ys as tick (tick)}
            <line
              x1={PAD.left}
              x2={PAD.left + inner.w}
              y1={sy(tick)}
              y2={sy(tick)}
              class={cn({
                "stroke-foreground/50": tick === 0,
                "stroke-border": tick !== 0,
              })}
              stroke-dasharray={tick === 0 ? undefined : "2 3"}
            />
            <text
              x={PAD.left - 6}
              y={sy(tick)}
              text-anchor="end"
              dominant-baseline="middle"
              class="fill-muted-foreground font-figures text-[0.625rem] tabular-nums"
              >{tick === 0
                ? "0"
                : points(tick, Math.abs(tick) < 0.01 ? 1 : 0)}</text
            >
          {/each}
          {#each dots as dot (dot.k)}
            {const isPicked = $derived(picked.has(dot.name))}
            <circle
              cx={sx(dot.x)}
              cy={sy(dot.y)}
              r={radius(dot.valid)}
              style:fill={dot.lead == null
                ? "var(--muted-foreground)"
                : swatch(dot.lead.color, dot.lead.key)}
              fill-opacity={isDimmed && !isPicked ? 0.12 : 0.62}
              class={cn({
                "stroke-foreground": isPicked,
                "stroke-card": !isPicked,
              })}
              stroke-width={isPicked ? 1.5 : 0.75}
            />
          {/each}
          <line
            x1={PAD.left}
            x2={PAD.left + inner.w}
            y1={sy(stats.my)}
            y2={sy(stats.my)}
            style:stroke={tint}
            stroke-width="1.5"
            stroke-dasharray="5 4"
          />
          <line
            x1={sx(domain.x[0])}
            x2={sx(domain.x[1])}
            y1={sy(stats.my + stats.slope * (domain.x[0] - stats.mx))}
            y2={sy(stats.my + stats.slope * (domain.x[1] - stats.mx))}
            class="stroke-foreground"
            stroke-width="1.25"
            stroke-opacity="0.75"
          />
          {#each labeled as label (label.k)}
            <text
              x={label.x}
              y={label.y}
              text-anchor="middle"
              class="fill-foreground stroke-card text-[0.6875rem] font-semibold [paint-order:stroke]"
              stroke-width="3"
              stroke-linejoin="round">{label.name}</text
            >
          {/each}
          {#if shown != null}
            {const r = $derived(radius(shown.valid))}
            <line
              x1={PAD.left}
              x2={sx(shown.x) - r}
              y1={sy(shown.y)}
              y2={sy(shown.y)}
              class="stroke-foreground/60"
            />
            <line
              x1={sx(shown.x)}
              x2={sx(shown.x)}
              y1={sy(shown.y) + r}
              y2={PAD.top + inner.h}
              class="stroke-foreground/60"
            />
            <circle
              cx={sx(shown.x)}
              cy={sy(shown.y)}
              r={r + 2}
              class="fill-none stroke-foreground"
              stroke-width="1.5"
            />
          {/if}
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
        {#if shown != null}
          <div
            class="pointer-events-none absolute inset-e-1 inset-bs-1 grid gap-0.5 border bg-background/95 px-2 py-1.5 text-xs shadow-sm max-inline-60"
          >
            <span class="truncate font-semibold">{shown.name}</span>
            <span class="font-figures tabular-nums"
              >{percent(shown.x)} → {percent(shown.share)}
              <span class="font-semibold">{points(shown.y)}</span></span
            >
            <span class="font-figures text-muted-foreground tabular-nums"
              >{m.workbench_results_total({ count: count(shown.valid) })}</span
            >
          </div>
        {/if}
      {/if}
    </div>
    <figcaption
      class="flex flex-wrap justify-between gap-x-4 gap-y-1 text-xs text-muted-foreground"
    >
      <span
        >{m.workbench_scatter_x({ year: String(contest.target.year ?? "") })} →
        {m.workbench_scatter_y({ year: String(contest.target.year ?? "") })} ↑</span
      >
      <span>{m.workbench_shift_key()}</span>
    </figcaption>
  </figure>
{/if}
