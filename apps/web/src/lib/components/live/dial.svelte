<script lang="ts">
  import type { LiveEstimate, LiveOption } from "@ea/analytics/live";
  import { prefersReducedMotion, Spring } from "svelte/motion";
  import { count, percent } from "#lib/format.js";
  import { phi } from "#lib/live/calibrate.js";
  import { compact, lead, surname, tone } from "#lib/live/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";
  import MethodLink from "./method-link.svelte";

  const {
    options,
    pair,
    estimate,
    history,
    counted,
    isWaiting,
  }: {
    options: Array<LiveOption>;
    pair: [number, number];
    estimate: LiveEstimate | null;
    history: Array<{ at: string; p: number }>;
    counted: number | null;
    isWaiting: boolean;
  } = $props();

  const DOTS = 100;
  const PLOT = 132;

  let width = $state(352);

  const a = $derived(options[pair[0]]);
  const b = $derived(options[pair[1]]);
  const toneA = $derived(tone(a));
  const toneB = $derived(tone(b));
  const isLive = $derived(
    estimate != null &&
      (estimate.status === "estimating" || estimate.status === "settled"),
  );
  const isSettled = $derived(estimate?.status === "settled");
  const idle = $derived.by(() => {
    if (estimate?.status === "unavailable") {
      return m.night_dial_unavailable();
    }
    return isWaiting ? m.night_dial_waiting() : m.night_dial_early();
  });
  const p = $derived(
    estimate == null || a == null
      ? 0.5
      : (estimate.winProbability[a.id] ?? 0.5),
  );
  const band = $derived.by(() => {
    if (estimate == null || a == null) {
      return { low: 0, mid: 0, high: 0 };
    }
    const flip = estimate.leader === a.id ? 1 : -1;
    const low = estimate.margin.low * flip;
    const high = estimate.margin.high * flip;
    return {
      low: Math.min(low, high),
      mid: estimate.margin.mid * flip,
      high: Math.max(low, high),
    };
  });

  const split = new Spring(0.5, { stiffness: 0.08, damping: 0.6 });
  $effect(() => {
    void split.set(isLive ? p : 0.5, { instant: prefersReducedMotion.current });
  });

  const outcomes = $derived.by(() => {
    if (!isLive || isSettled) {
      return [];
    }
    const below = Math.max(0.0015, (band.mid - band.low) / 1.2816);
    const above = Math.max(0.0015, (band.high - band.mid) / 1.2816);
    const weight = below / (below + above);
    const cdf = (x: number) =>
      x < band.mid
        ? 2 * weight * phi((x - band.mid) / below)
        : weight + 2 * (1 - weight) * (phi((x - band.mid) / above) - 0.5);
    const inverse = (q: number) => {
      let low = band.mid - 8 * below;
      let high = band.mid + 8 * above;
      for (let step = 0; step < 40; step += 1) {
        const middle = (low + high) / 2;
        if (cdf(middle) < q) {
          low = middle;
        } else {
          high = middle;
        }
      }
      return (low + high) / 2;
    };
    const wins = Math.round(p * DOTS);
    const zero = Math.min(1, Math.max(0, cdf(0)));
    const list: Array<number> = [];
    for (let index = 0; index < DOTS - wins; index += 1) {
      list.push(
        Math.min(-0.00001, inverse(((index + 0.5) / (DOTS - wins)) * zero)),
      );
    }
    for (let index = 0; index < wins; index += 1) {
      list.push(
        Math.max(0.00001, inverse(zero + ((index + 0.5) / wins) * (1 - zero))),
      );
    }
    return list;
  });

  const reach = $derived.by(() => {
    let top = 0.02;
    for (const value of [...outcomes, band.low, band.high, counted ?? 0]) {
      top = Math.max(top, Math.abs(value));
    }
    const step = top > 0.2 ? 0.1 : top > 0.08 ? 0.05 : top > 0.04 ? 0.02 : 0.01;
    return Math.ceil((top * 1.1) / step) * step;
  });
  const x = (value: number) =>
    ((reach - Math.max(-reach, Math.min(reach, value))) / (2 * reach)) * width;

  const dots = $derived.by(() => {
    if (outcomes.length === 0) {
      return {
        size: 6,
        list: [] as Array<{ id: number; cx: number; cy: number; isA: boolean }>,
      };
    }
    let size = 9;
    let placed: Array<{ id: number; cx: number; cy: number; isA: boolean }> =
      [];
    while (size >= 3) {
      const columns = new Map<number, number>();
      placed = [];
      let tallest = 0;
      const origin = x(0);
      for (const [id, value] of outcomes.entries()) {
        const isA = value >= 0;
        const column = Math.max(
          0,
          Math.floor(Math.abs(x(value) - origin) / size),
        );
        const key = isA ? column + 1 : -column - 1;
        const height = columns.get(key) ?? 0;
        columns.set(key, height + 1);
        tallest = Math.max(tallest, height + 1);
        const center = origin + (isA ? -1 : 1) * (column + 0.5) * size;
        placed.push({
          id,
          cx: Math.min(width - size / 2, Math.max(size / 2, center)),
          cy: PLOT - size / 2 - height * size,
          isA,
        });
      }
      if (tallest * size <= PLOT - 8) {
        break;
      }
      size -= 0.5;
    }
    return { size, list: placed };
  });

  const ticks = $derived.by(() => {
    const step =
      reach > 0.2 ? 0.1 : reach > 0.08 ? 0.05 : reach > 0.04 ? 0.02 : 0.01;
    const every = Math.max(1, Math.ceil(34 / ((step / (2 * reach)) * width)));
    const marks: Array<{ value: number; isLabeled: boolean }> = [];
    for (let index = 1; index * step <= reach + 1e-9; index += 1) {
      const isLabeled = index % every === 0;
      marks.push(
        { value: index * step, isLabeled },
        { value: -index * step, isLabeled },
      );
    }
    return marks;
  });

  const favored = $derived(p >= 0.5 ? a : b);
  const chance = $derived(Math.max(p, 1 - p));
  const winsA = $derived(Math.round(p * DOTS));
  const name = (value: number) => (value >= 0 ? surname(a) : surname(b));
  const signed = (value: number) => `${name(value)} +${lead(value)}`;
  const range = $derived.by(() => {
    if (band.low >= 0 || band.high <= 0) {
      const near = Math.min(Math.abs(band.low), Math.abs(band.high));
      const far = Math.max(Math.abs(band.low), Math.abs(band.high));
      const side = band.high <= 0 ? -1 : 1;
      return { low: signed(side * near), high: signed(side * far) };
    }
    return { low: signed(band.low), high: signed(band.high) };
  });

  const spark = $derived.by(() => {
    if (history.length < 2) {
      return { line: "", fill: "" };
    }
    const first = Date.parse(history[0]?.at ?? "");
    const span = Math.max(1, Date.parse(history.at(-1)?.at ?? "") - first);
    const points = history.map(
      (point) =>
        [
          ((Date.parse(point.at) - first) / span) * 100,
          38.5 - point.p * 37,
        ] as const,
    );
    const line = points
      .map(
        ([px, py], index) =>
          `${index === 0 ? "M" : "L"}${px.toFixed(2)} ${py.toFixed(2)}`,
      )
      .join(" ");
    return { line, fill: `M0 20 ${line.replace(/^M/u, "L")} L100 20 Z` };
  });
</script>

<div class="@container">
  <section
    class="grid gap-x-10 gap-y-6 @4xl:grid-cols-[minmax(0,17rem)_minmax(0,1fr)_minmax(0,14rem)]"
    aria-labelledby="dial-title"
  >
    <div class="grid content-start gap-2.5">
      <div class="flex items-center gap-1">
        <h2 id="dial-title" class="text-[0.8125rem] font-semibold">
          {m.night_dial_title()}
        </h2>
        <MethodLink section="ranges" />
      </div>
      <div
        class="grid gap-2"
        role="img"
        aria-label={isLive && favored != null
          ? m.night_dial_aria({
              name: favored.label,
              chance: percent(chance, 0),
            })
          : idle}
      >
        <div
          class="flex items-center justify-between gap-3 text-xs font-semibold"
        >
          <span class="flex items-center gap-1.5" style:color={toneA}
            ><span
              class="rounded-full block-2 inline-2"
              style:background-color={toneA}
            ></span>{surname(a)}</span
          >
          <span class="flex items-center gap-1.5" style:color={toneB}
            >{surname(b)}<span
              class="rounded-full block-2 inline-2"
              style:background-color={toneB}
            ></span></span
          >
        </div>
        <div class="flex items-baseline justify-between gap-3 block-10">
          {#each [{ value: p, color: toneA }, { value: 1 - p, color: toneB }] as side, position (position)}
            {const isAhead = $derived(isLive && side.value >= 0.5)}
            {#if isLive}
              <span
                class={cn(
                  "font-figures leading-none tracking-tight tabular-nums",
                  {
                    "text-[2.25rem] font-[780]": isAhead,
                    "text-xl font-semibold text-muted-foreground": !isAhead,
                  },
                )}
                style:color={isAhead ? side.color : undefined}
                >{percent(side.value, 0)}</span
              >
            {:else}
              <span
                class="font-figures text-xl leading-none font-semibold text-muted-foreground/50"
                aria-hidden="true">–</span
              >
            {/if}
          {/each}
        </div>
      </div>
      <div class="relative block-2.5" aria-hidden="true">
        {#if isLive}
          <span
            class="absolute inset-y-0 inset-s-0 rounded-s-full"
            style:inline-size="{split.current * 100}%"
            style:background-color={toneA}
          ></span>
          <span
            class="absolute inset-y-0 inset-e-0 rounded-e-full"
            style:inline-size="{(1 - split.current) * 100}%"
            style:background-color={toneB}
          ></span>
          <span
            class="absolute -inset-y-0.5 -translate-x-1/2 bg-background inline-0.5"
            style:left="{split.current * 100}%"
          ></span>
        {:else}
          <span
            class="absolute inset-0 rounded-full border border-dashed border-border"
          ></span>
        {/if}
        <span class="absolute -inset-y-1 inset-s-1/2 bg-foreground/50 inline-px"
        ></span>
      </div>
      <p class="truncate text-xs text-muted-foreground">
        {#if isLive && favored != null}
          {isSettled
            ? m.night_dial_settled({ name: favored.label })
            : m.night_dial_chance({ name: favored.label })}
        {:else}
          {idle}
        {/if}
      </p>
      <figure class="grid gap-1 pbs-1">
        <figcaption class="text-xs text-muted-foreground">
          {m.night_dial_history()}
        </figcaption>
        <div class="grid grid-cols-[minmax(0,1fr)_auto] gap-x-2">
          <div class="relative overflow-hidden rounded-xs bg-muted/40 block-14">
            {#if spark.line === ""}
              <span
                class="absolute inset-x-0 inset-bs-1/2 border-bs border-dashed border-foreground/25"
                aria-hidden="true"
              ></span>
              <span
                class="absolute inset-0 grid place-items-center text-[0.6875rem] text-muted-foreground"
                ><span class="bg-background/80 px-1.5"
                  >{m.night_dial_history_empty()}</span
                ></span
              >
            {:else}
              <svg
                viewBox="0 0 100 40"
                preserveAspectRatio="none"
                class="absolute inset-0 block-full inline-full"
                aria-hidden="true"
              >
                <defs>
                  <clipPath id="dial-above"
                    ><rect x="0" y="0" width="100" height="20" /></clipPath
                  >
                  <clipPath id="dial-below"
                    ><rect x="0" y="20" width="100" height="20" /></clipPath
                  >
                </defs>
                <path
                  d={spark.fill}
                  fill={toneA}
                  fill-opacity="0.18"
                  clip-path="url(#dial-above)"
                />
                <path
                  d={spark.fill}
                  fill={toneB}
                  fill-opacity="0.18"
                  clip-path="url(#dial-below)"
                />
                <line
                  x1="0"
                  x2="100"
                  y1="20"
                  y2="20"
                  stroke="var(--foreground)"
                  stroke-opacity="0.35"
                  stroke-dasharray="2 2"
                  vector-effect="non-scaling-stroke"
                />
                <path
                  d={spark.line}
                  fill="none"
                  stroke={toneA}
                  stroke-width="1.75"
                  vector-effect="non-scaling-stroke"
                  stroke-linejoin="round"
                  clip-path="url(#dial-above)"
                />
                <path
                  d={spark.line}
                  fill="none"
                  stroke={toneB}
                  stroke-width="1.75"
                  vector-effect="non-scaling-stroke"
                  stroke-linejoin="round"
                  clip-path="url(#dial-below)"
                />
              </svg>
            {/if}
          </div>
          <div
            class="grid content-between text-[0.6875rem] font-semibold"
            aria-hidden="true"
          >
            <span style:color={toneA}>{surname(a)}</span>
            <span style:color={toneB}>{surname(b)}</span>
          </div>
        </div>
      </figure>
    </div>

    <figure class="grid content-start gap-2.5">
      <figcaption
        class="flex items-baseline justify-between gap-3 text-[0.8125rem]"
      >
        <span class="font-semibold">{m.night_dial_margin()}</span>
        {#if isLive && !isSettled}
          <span
            class="font-figures font-semibold tabular-nums"
            style:color={band.mid >= 0 ? toneA : toneB}>{signed(band.mid)}</span
          >
        {/if}
      </figcaption>
      <div class="relative" bind:clientWidth={width}>
        <svg
          viewBox="0 0 {width} {PLOT + 30}"
          class="block overflow-visible inline-full"
          style:block-size="{PLOT + 30}px"
          role="img"
          aria-label={isLive && a != null
            ? m.night_dial_dots_aria({ count: count(winsA), name: a.label })
            : idle}
        >
          <rect
            x="0"
            y="0"
            width={x(0)}
            height={PLOT}
            fill={toneA}
            fill-opacity="0.04"
          />
          <rect
            x={x(0)}
            y="0"
            width={width - x(0)}
            height={PLOT}
            fill={toneB}
            fill-opacity="0.04"
          />
          {#each ticks as { value: tick, isLabeled } (tick)}
            <line
              x1={x(tick)}
              x2={x(tick)}
              y1="0"
              y2={PLOT}
              stroke="var(--border)"
              stroke-dasharray="2 3"
            />
            {#if isLabeled}
              <text
                x={x(tick)}
                y={PLOT + 22}
                text-anchor={Math.abs(tick) < reach - 1e-6
                  ? "middle"
                  : tick > 0
                    ? "start"
                    : "end"}
                class="font-figures text-[10px] tabular-nums"
                style:fill={tick >= 0 ? toneA : toneB}
                fill-opacity="0.75">+{lead(Math.abs(tick), 0)}</text
              >
            {/if}
          {/each}
          {#if isLive && !isSettled}
            <rect
              x={x(band.high)}
              y={PLOT + 2}
              width={Math.max(2, x(band.low) - x(band.high))}
              height="5"
              rx="2.5"
              fill={band.mid >= 0 ? toneA : toneB}
              fill-opacity="0.3"
            />
          {/if}
          {#if dots.list.length > 0}
            {#each dots.list as dot (dot.id)}
              <circle
                r={dots.size / 2 - 0.6}
                cx="0"
                cy="0"
                fill={dot.isA ? toneA : toneB}
                class="transition-transform duration-700 ease-out motion-reduce:transition-none"
                style:transform="translate({dot.cx}px, {dot.cy}px)"
                style:transition-delay="{prefersReducedMotion.current
                  ? 0
                  : (dot.id % 20) * 12}ms"
              />
            {/each}
          {/if}
          <line
            x1="0"
            x2={width}
            y1={PLOT}
            y2={PLOT}
            stroke="var(--foreground)"
            stroke-opacity="0.55"
          />
          <line
            x1={x(0)}
            x2={x(0)}
            y1="-4"
            y2={PLOT + 8}
            stroke="var(--foreground)"
            stroke-width="1.25"
          />
          {#if !isLive}
            <foreignObject x="0" y="0" {width} height={PLOT - 8}>
              <p
                class="grid place-items-center p-4 text-center text-xs/snug text-pretty text-muted-foreground block-full"
              >
                <span class="bg-background/85 px-2 py-1 max-inline-64"
                  >{m.night_dial_dots_empty()}</span
                >
              </p>
            </foreignObject>
          {/if}
          {#if isLive && !isSettled}
            <path
              d="M{x(band.mid)} {PLOT + 2} l-5 -8 h10 z"
              fill={band.mid >= 0 ? toneA : toneB}
              stroke="var(--background)"
              stroke-width="1.5"
            />
          {/if}
          {#if counted != null}
            <circle
              cx={x(counted)}
              cy={PLOT}
              r="4.5"
              fill="var(--background)"
              stroke="var(--foreground)"
              stroke-width="2"
            />
          {/if}
          <text
            x={x(0)}
            y={PLOT + 22}
            text-anchor="middle"
            class="fill-foreground text-[11px] font-semibold"
            >{m.night_dial_tie()}</text
          >
        </svg>
      </div>
      <p
        class="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground min-block-5"
      >
        <span class="flex items-center gap-1.5">
          <span class="flex gap-0.5"
            ><span
              class="rounded-full block-1.5 inline-1.5"
              style:background-color={toneA}
            ></span><span
              class="rounded-full block-1.5 inline-1.5"
              style:background-color={toneB}
            ></span></span
          >
          {m.night_dial_dots()}
        </span>
        <span class="flex items-center gap-1.5"
          ><span
            class="rounded-full border-2 border-foreground block-2.5 inline-2.5"
          ></span>{m.night_dial_counted()}</span
        >
        {#if isLive && !isSettled}
          <span class="flex items-center gap-1.5">
            <span class="rounded-full bg-foreground/30 block-1.5 inline-4"
            ></span>
            {m.night_dial_range(range)}
          </span>
        {/if}
      </p>
    </figure>

    <dl
      class="grid content-start text-[0.8125rem] @max-4xl:grid-cols-2 @4xl:divide-y @4xl:divide-border"
    >
      <div class="grid gap-1 pbe-3">
        <dt class="text-muted-foreground">{m.night_dial_expected()}</dt>
        <dd class="font-figures tabular-nums">
          {#if isLive && estimate != null && !isSettled}
            <span class="text-xl font-semibold"
              >{compact(estimate.expectedTotal.mid)}</span
            >
            <span class="block text-xs text-muted-foreground"
              >{compact(estimate.expectedTotal.low)}–{compact(
                estimate.expectedTotal.high,
              )}</span
            >
          {:else}
            <span
              class="block text-xl/tight font-semibold text-muted-foreground/50"
              aria-hidden="true">–</span
            >
          {/if}
        </dd>
      </div>
      <div
        class="grid gap-1 @max-4xl:border-s @max-4xl:border-border @max-4xl:ps-4 @4xl:pbs-3"
      >
        <dt class="text-muted-foreground">{m.night_dial_left()}</dt>
        <dd class="font-figures tabular-nums">
          {#if isLive && estimate != null && !isSettled}
            <span class="text-xl font-semibold"
              >{count(
                Math.max(
                  0,
                  Math.round(estimate.expectedTotal.mid - estimate.counted),
                ),
              )}</span
            >
          {:else}
            <span
              class="block text-xl/tight font-semibold text-muted-foreground/50"
              aria-hidden="true">–</span
            >
          {/if}
        </dd>
      </div>
    </dl>
  </section>
</div>
