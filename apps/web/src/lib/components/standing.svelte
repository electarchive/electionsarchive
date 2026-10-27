<script lang="ts">
  import Export from "#lib/components/export.svelte";
  import Measured from "#lib/components/measured.svelte";
  import { count, definite, plural, points } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";

  const {
    sides,
    place,
    marks: given,
    within,
    siblings,
    year,
    isAbroad = false,
    isAllied = false,
    isCompact = false,
    class: className,
  }: {
    sides: [{ label: string; tone: string }, { label: string; tone: string }];
    place: { name: string; margin: number; before: number | null };
    marks: Array<{ name: string; margin: number }>;
    within: string;
    siblings: {
      noun: string;
      count: number;
      total: number;
      rank: number;
      places: Array<{ name: string; margin: number; isOwn: boolean }>;
    };
    year: number | null;
    isAbroad?: boolean;
    isAllied?: boolean;
    isCompact?: boolean;
    class?: string;
  } = $props();

  const TOP = 40;
  const EDGE = 14;

  let width = $state(0);
  let focus = $state<number | null>(null);
  let isAll = $state(false);

  const marks = $derived(given.filter((mark) => mark.name !== place.name));
  const step = $derived.by(() => {
    let widest = Math.max(Math.abs(place.margin), Math.abs(place.before ?? 0));
    for (const mark of marks) {
      widest = Math.max(widest, Math.abs(mark.margin));
    }
    for (const entry of siblings.places) {
      widest = Math.max(widest, Math.abs(entry.margin));
    }
    const size =
      widest > 0.5 ? 0.25 : widest > 0.2 ? 0.1 : widest > 0.08 ? 0.05 : 0.02;
    return {
      size,
      reach: Math.min(1, Math.ceil((widest + 0.01) / size) * size),
    };
  });
  const ticks = $derived.by(() => {
    const found: Array<number> = [];
    for (
      let tick = -step.reach;
      tick <= step.reach + 0.0001;
      tick += step.size
    ) {
      found.push(Math.round(tick / step.size) * step.size);
    }
    return found;
  });
  const band = $derived(
    siblings.places.length > 120 ? 56 : siblings.places.length > 30 ? 36 : 20,
  );
  const x = (value: number) =>
    EDGE + ((value + step.reach) / (2 * step.reach)) * (width - 2 * EDGE);
  const layout = $derived.by(() => {
    const own = siblings.places.findIndex((entry) => entry.isOwn);
    const order = [
      ...(own < 0 ? [] : [own]),
      ...siblings.places.keys().filter((index) => index !== own),
    ];
    const start =
      siblings.places.length > 250 ? 3 : siblings.places.length > 60 ? 4 : 5.5;
    const sizes = Array.from(
      { length: Math.round((start - 1.5) / 0.5) + 1 },
      (_, at) => start - at * 0.5,
    );
    let found = {
      radius: start,
      placed: [] as Array<{ index: number; cx: number; dy: number }>,
    };
    for (const radius of sizes) {
      const gap = radius * 2 + 1;
      const columns: Array<number> = [];
      let most = 0;
      const placed: Array<{ index: number; cx: number; dy: number }> = [];
      for (const index of order) {
        const slot = Math.round(
          (x(siblings.places[index]?.margin ?? 0) - EDGE) / gap,
        );
        const stacked = columns[slot] ?? 0;
        columns[slot] = stacked + 1;
        most = Math.max(most, stacked + 1);
        placed.push({
          index,
          cx: EDGE + slot * gap,
          dy: Math.ceil(stacked / 2) * gap * (stacked % 2 === 1 ? -1 : 1),
        });
      }
      found = { radius, placed };
      if (Math.ceil(most / 2) * gap <= band) {
        break;
      }
    }
    const tallest = Math.max(
      1,
      ...found.placed.map((dot) => Math.abs(dot.dy) + found.radius),
    );
    const squeeze = Math.min(1, band / tallest);
    return {
      radius: found.radius,
      swarm: found.placed
        .map((dot) => ({ ...dot, dy: dot.dy * squeeze }))
        .toSorted((p, q) => p.index - q.index),
    };
  });
  const radius = $derived(layout.radius);
  const swarm = $derived(layout.swarm);
  const middle = $derived(TOP + band + 4);
  const floor = $derived(middle + band + 4);
  const rows = $derived.by(() => {
    const [first, second] = marks.map((mark) => x(mark.margin));
    return first != null && second != null && Math.abs(first - second) < 120
      ? [0, 1]
      : [0, 0];
  });
  const height = $derived(floor + 18 + (marks.length > 1 ? 16 : 0) + 22);
  const ranked = $derived.by(() => {
    const indexed = siblings.places.map((entry, index) => ({
      ...entry,
      index,
    }));
    return (place.margin >= 0 ? indexed.toReversed() : indexed).map(
      (entry, at) => ({ ...entry, at: at + 1 }),
    );
  });
  const shown = $derived.by(() => {
    if (isAll || ranked.length <= 12) {
      return ranked.map((entry) => ({ entry, isGap: false }));
    }
    const own = ranked.findIndex((entry) => entry.isOwn);
    const kept = ranked.filter(
      (_, at) => at < 3 || at >= ranked.length - 3 || Math.abs(at - own) <= 2,
    );
    return kept.map((entry, at) => ({
      entry,
      isGap: at > 0 && entry.at - (kept[at - 1]?.at ?? 0) > 1,
    }));
  });
  const widest = $derived(
    Math.max(0.0001, ...siblings.places.map((entry) => Math.abs(entry.margin))),
  );
  const spot = $derived(focus == null ? null : (swarm[focus] ?? null));
  const lit = $derived(focus == null ? null : (siblings.places[focus] ?? null));
  const lede = $derived.by(() => {
    const versus = marks[0];
    if (versus == null) {
      return null;
    }
    const gap = place.margin - versus.margin;
    const text =
      Math.abs(gap) < 0.0005
        ? m.standing_lede_even({
            place: place.name,
            other: definite(versus.name),
          })
        : m.standing_lede({
            place: place.name,
            points: points(Math.abs(gap)).replace("+", ""),
            name: (gap >= 0 ? sides[0] : sides[1]).label,
            other: definite(versus.name),
          });
    const shift = place.before == null ? null : place.margin - place.before;
    return shift == null || year == null || Math.abs(shift) < 0.0005
      ? text
      : `${text} ${m.standing_shift({
          year,
          points: points(Math.abs(shift)).replace("+", ""),
          name: (shift >= 0 ? sides[0] : sides[1]).label,
        })}`;
  });

  function tone(value: number) {
    return (value >= 0 ? sides[0] : sides[1]).tone;
  }

  function lead(value: number) {
    return Math.abs(value) < 0.0005
      ? m.atlas_even()
      : m.atlas_lead({
          name: (value >= 0 ? sides[0] : sides[1]).label,
          points: points(Math.abs(value)).replace("+", ""),
        });
  }
</script>

{#snippet chart()}
  <Measured bind:width class="relative min-inline-0" reserve="{height}px">
    <svg
      class="block overflow-visible max-inline-full"
      {width}
      {height}
      viewBox="0 0 {width} {height}"
      role="img"
      aria-label={lede ?? m.lean_title({ place: place.name })}
    >
      <rect
        x={EDGE}
        y={TOP - 8}
        width={Math.max(0, x(0) - EDGE)}
        height={floor - TOP + 8}
        fill={sides[1].tone}
        fill-opacity="0.07"
      />
      <rect
        x={x(0)}
        y={TOP - 8}
        width={Math.max(0, width - EDGE - x(0))}
        height={floor - TOP + 8}
        fill={sides[0].tone}
        fill-opacity="0.07"
      />
      {#each ticks as tick (tick)}
        <line
          x1={x(tick)}
          x2={x(tick)}
          y1={TOP - 8}
          y2={floor}
          class={cn({
            "stroke-muted-foreground/25": tick !== 0,
            "stroke-muted-foreground/70": tick === 0,
          })}
          stroke-dasharray={tick === 0 ? "3 3" : undefined}
        />
        <text
          x={x(tick)}
          y={height - 6}
          text-anchor="middle"
          class="fill-muted-foreground font-figures text-[0.625rem] tabular-nums"
          >{tick === 0
            ? m.atlas_even()
            : Math.round(Math.abs(tick) * 100)}</text
        >
      {/each}
      {#each marks as mark, index (index)}
        {const at = $derived(x(mark.margin))}
        {const row = $derived(rows[index] ?? 0)}
        {const anchor = $derived(
          at < width * 0.15 ? "start" : at > width * 0.85 ? "end" : "middle",
        )}
        <line
          x1={at}
          x2={at}
          y1={TOP - 8}
          y2={floor + 6 + row * 16}
          class={cn({
            "stroke-foreground": index === 0,
            "stroke-muted-foreground": index > 0,
          })}
          stroke-width="1.5"
        />
        <text
          x={at}
          y={floor + 17 + row * 16}
          text-anchor={anchor}
          class="text-[0.6875rem]"
          ><tspan class="fill-foreground font-semibold">{mark.name}</tspan
          ><tspan
            class="font-figures font-semibold tabular-nums"
            fill={tone(mark.margin)}
            dx="4"
            >{Math.abs(mark.margin) < 0.0005
              ? m.atlas_even()
              : points(Math.abs(mark.margin)).replace("+", "")}</tspan
          ></text
        >
      {/each}
      {#if place.before != null}
        {const from = $derived(x(place.before))}
        {const to = $derived(x(place.margin))}
        <line
          x1={from}
          x2={to}
          y1={middle}
          y2={middle}
          class="stroke-foreground/55"
          stroke-width="1.5"
        />
        <circle
          cx={from}
          cy={middle}
          r={radius - 0.5}
          class="fill-card stroke-foreground/60"
          stroke-width="1.5"
        />
      {/if}
      {#each swarm as dot (dot.index)}
        {const entry = $derived(siblings.places[dot.index])}
        {#if entry != null && !entry.isOwn}
          <circle
            cx={dot.cx}
            cy={middle + dot.dy}
            r={radius}
            fill={tone(entry.margin)}
            fill-opacity={focus === dot.index ? 1 : 0.5}
            class={cn("stroke-card transition-[fill-opacity]", {
              "stroke-foreground": focus === dot.index,
            })}
            stroke-width={radius < 3 && focus !== dot.index ? 0 : 1}
            role="presentation"
            onpointerenter={() => (focus = dot.index)}
            onpointerleave={() => (focus = null)}
          />
        {/if}
      {/each}
      {#each swarm as dot (dot.index)}
        {const entry = $derived(siblings.places[dot.index])}
        {#if entry?.isOwn === true}
          {const anchor = $derived(
            dot.cx < width * 0.15
              ? "start"
              : dot.cx > width * 0.85
                ? "end"
                : "middle",
          )}
          <line
            x1={dot.cx}
            x2={dot.cx}
            y1={TOP - 6}
            y2={middle + dot.dy - radius - 3}
            class="stroke-foreground"
          />
          <text
            x={dot.cx}
            y={TOP - 13}
            text-anchor={anchor}
            class="text-[0.8125rem]"
            ><tspan class="fill-foreground font-bold">{place.name}</tspan><tspan
              class="font-figures font-bold tabular-nums"
              fill={tone(place.margin)}
              dx="5"
              >{Math.abs(place.margin) < 0.0005
                ? m.atlas_even()
                : points(Math.abs(place.margin)).replace("+", "")}</tspan
            ></text
          >
          <circle
            cx={dot.cx}
            cy={middle + dot.dy}
            r={radius + 3}
            fill={tone(place.margin)}
            class="stroke-foreground"
            stroke-width="2"
            role="presentation"
            onpointerenter={() => (focus = dot.index)}
            onpointerleave={() => (focus = null)}
          />
        {/if}
      {/each}
    </svg>
    {#if lit != null && spot != null}
      <div
        class="pointer-events-none absolute grid gap-0.5 rounded-sm bg-card/95 px-2.5 py-1.5 text-xs whitespace-nowrap shadow-menu ring-1 ring-border"
        style:inset-inline-start="{Math.max(
          0,
          Math.min(width - 190, spot.cx - 40),
        )}px"
        style:inset-block-start="{middle + spot.dy + radius + 8}px"
      >
        <span class="text-[0.8125rem] font-semibold">{lit.name}</span>
        <span
          class="font-figures font-semibold tabular-nums"
          style:color={tone(lit.margin)}>{lead(lit.margin)}</span
        >
      </div>
    {/if}
  </Measured>
  <div
    class="flex justify-between gap-4 text-[0.75rem] font-semibold"
    aria-hidden="true"
  >
    <span class="flex items-center gap-1.5 min-inline-0">
      <span
        class="shrink-0 rounded-xs block-2.5 inline-2.5"
        style:background-color={sides[1].tone}
      ></span>
      <span class="truncate">{sides[1].label}</span>
    </span>
    <span class="flex items-center gap-1.5 text-end min-inline-0">
      <span class="truncate">{sides[0].label}</span>
      <span
        class="shrink-0 rounded-xs block-2.5 inline-2.5"
        style:background-color={sides[0].tone}
      ></span>
    </span>
  </div>
{/snippet}

{#snippet rank()}
  <p class="text-xs text-pretty text-muted-foreground">
    {isAbroad
      ? m.lean_rank_abroad({
          rank: siblings.rank,
          name: (place.margin >= 0 ? sides[0] : sides[1]).label,
          count: count(siblings.count),
          nouns: plural(siblings.noun),
        })
      : m.lean_rank({
          rank: siblings.rank,
          name: (place.margin >= 0 ? sides[0] : sides[1]).label,
          count: count(siblings.count),
          nouns: plural(siblings.noun),
          place: definite(within),
        })}
  </p>
  {#if siblings.total > siblings.count && !isAllied}
    <p class="text-xs text-pretty text-muted-foreground max-inline-[64ch]">
      {m.standing_partial({
        count: count(siblings.count),
        total: count(siblings.total),
        nouns: plural(siblings.noun),
        first: sides[0].label,
        second: sides[1].label,
      })}
    </p>
  {/if}
  {#if isAllied}
    <p class="text-xs text-pretty text-muted-foreground max-inline-[64ch]">
      {m.standing_allied({ nouns: plural(siblings.noun) })}
    </p>
  {/if}
{/snippet}

{#snippet summary()}
  {#if lede != null}
    <p
      class="text-[0.8125rem] text-pretty text-muted-foreground max-inline-[64ch]"
    >
      {lede}
    </p>
  {/if}
{/snippet}

{#if isCompact}
  <div class={cn("grid grid-cols-[minmax(0,1fr)] gap-2", className)}>
    {@render summary()}
    {@render chart()}
    {#if siblings.places.length > 1}
      {@render rank()}
    {/if}
  </div>
{:else}
  <section
    class="relative col-span-12 grid grid-cols-12 gap-px bg-border"
    aria-labelledby="standing-title"
    data-export
  >
    <Export name="standing" class="absolute inset-e-2 inset-bs-2" />
    <div
      class={cn(
        "col-span-12 grid grid-cols-[minmax(0,1fr)] content-start gap-3 bg-card px-3.5 py-3 pe-8 min-inline-0",
        { "lg:col-span-7": siblings.places.length > 1 },
      )}
    >
      <div class="grid gap-0.5">
        <h2 id="standing-title" class="text-[0.8125rem] font-bold">
          {m.lean_title({ place: place.name })}
        </h2>
        {@render summary()}
      </div>
      {@render chart()}
      {#if siblings.places.length > 1}
        <p class="text-xs text-muted-foreground">
          {m.standing_key({ noun: siblings.noun })}
        </p>
      {/if}
    </div>
    {#if siblings.places.length > 1}
      <div
        class="col-span-12 grid content-start gap-2 bg-card px-3.5 py-3 min-inline-0 lg:col-span-5"
      >
        <h3 class="text-[0.8125rem] font-bold first-letter:uppercase">
          {isAbroad
            ? plural(siblings.noun)
            : m.scope_in({ noun: plural(siblings.noun), parent: within })}
        </h3>
        <ol class="grid">
          {#each shown as { entry, isGap } (entry.index)}
            {#if isGap}
              <li
                class="px-1.5 text-xs text-muted-foreground"
                aria-hidden="true"
              >
                …
              </li>
            {/if}
            <li
              class={cn(
                "grid grid-cols-[1.5rem_minmax(0,1fr)_minmax(0,1fr)_auto] items-center gap-2.5 rounded-xs px-1.5 py-1 text-[0.8125rem]",
                { "bg-secondary/70": focus === entry.index },
              )}
              onpointerenter={() => (focus = entry.index)}
              onpointerleave={() => (focus = null)}
            >
              <span
                class="text-end font-figures text-[0.6875rem] text-muted-foreground tabular-nums"
                >{entry.at}</span
              >
              <span
                class={cn("truncate", {
                  "font-bold": entry.isOwn,
                  "font-semibold": !entry.isOwn,
                })}>{entry.name}</span
              >
              <span class="grid grid-cols-2 items-center block-3">
                <span class="flex justify-end">
                  {#if entry.margin < 0}
                    <span
                      class="block rounded-s-xs block-3"
                      style:inline-size="{(Math.abs(entry.margin) / widest) *
                        100}%"
                      style:background-color={sides[1].tone}
                    ></span>
                  {/if}
                </span>
                <span class="flex border-s border-muted-foreground/60">
                  {#if entry.margin > 0}
                    <span
                      class="block rounded-e-xs block-3"
                      style:inline-size="{(entry.margin / widest) * 100}%"
                      style:background-color={sides[0].tone}
                    ></span>
                  {/if}
                </span>
              </span>
              <span
                class="text-end font-figures text-xs font-semibold tabular-nums inline-10"
                style:color={tone(entry.margin)}
                >{Math.abs(entry.margin) < 0.0005
                  ? m.atlas_even()
                  : points(Math.abs(entry.margin)).replace("+", "")}</span
              >
            </li>
          {/each}
        </ol>
        {#if ranked.length > 12}
          <button
            type="button"
            class="justify-self-start rounded-sm px-2.5 text-[0.8125rem] font-semibold ring-1 ring-input block-8 hover:bg-secondary"
            onclick={() => (isAll = !isAll)}
            >{isAll
              ? m.standing_fewer()
              : m.standing_all({ count: count(ranked.length) })}</button
          >
        {/if}
        {@render rank()}
      </div>
    {/if}
  </section>
{/if}
