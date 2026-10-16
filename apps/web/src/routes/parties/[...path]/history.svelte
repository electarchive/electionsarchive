<script lang="ts">
  import { resolve } from "$app/paths";
  import { page } from "$app/state";
  import Export from "#lib/components/export.svelte";
  import { count, percent } from "#lib/format.js";
  import { keep } from "#lib/linked.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";
  import type { PageData } from "./$types";

  const {
    history,
    name,
    color,
  }: { history: PageData["history"]; name: string; color: string } = $props();

  const PAD = { top: 16, right: 18, bottom: 26, left: 36 };

  let width = $state(720);
  let chosen = $state<string | null>(page.url.searchParams.get("series"));

  $effect(() => {
    keep({ series: chosen });
  });
  let hovered = $state<number | null>(null);

  const series = $derived(
    history
      .map((entry) => ({
        ...entry,
        points: entry.points.map((point) => ({
          ...point,
          at:
            point.year +
            (Date.parse(`${point.date}T00:00:00Z`) -
              Date.UTC(Number(point.date.slice(0, 4)), 0, 1)) /
              31_622_400_000,
          share:
            point.votes == null || point.total == null
              ? null
              : point.votes / point.total,
          held:
            point.seats == null || point.filled == null || point.filled === 0
              ? null
              : point.seats / point.filled,
        })),
      }))
      .filter((entry) => entry.points.some((point) => point.share != null))
      .toSorted((a, b) => b.points.length - a.points.length),
  );
  const active = $derived(
    series.find((entry) => entry.id === chosen) ?? series[0],
  );
  const isOffice = $derived(
    active?.points.every((point) => (point.filled ?? 0) <= 1) ?? false,
  );
  const range = $derived.by(() => {
    let low = Infinity;
    let high = -Infinity;
    for (const entry of series) {
      for (const point of entry.points) {
        low = Math.min(low, point.at);
        high = Math.max(high, point.at);
      }
    }
    return high - low < 4
      ? { low: low - 2, high: high + 2 }
      : { low: low - 0.6, high: high + 0.6 };
  });
  const top = $derived.by(() => {
    let most = 0.3;
    for (const entry of series) {
      for (const point of entry.points) {
        most = Math.max(most, point.share ?? 0);
      }
    }
    for (const point of active?.points ?? []) {
      most = Math.max(most, isOffice ? 0 : (point.held ?? 0));
    }
    return Math.min(1, Math.ceil(most * 10 + 0.3) / 10);
  });
  const height = $derived(
    Math.round(Math.min(340, Math.max(220, width * 0.42))),
  );
  const sx = (value: number) =>
    PAD.left +
    ((value - range.low) / (range.high - range.low)) *
      (width - PAD.left - PAD.right);
  const sy = (value: number) =>
    PAD.top + (1 - value / top) * (height - PAD.top - PAD.bottom);
  const years = $derived.by(() => {
    const span = range.high - range.low;
    const step = span > 60 ? 20 : span > 30 ? 10 : span > 12 ? 5 : 2;
    const found: Array<number> = [];
    for (
      let year = Math.ceil(range.low / step) * step;
      year <= range.high;
      year += step
    ) {
      found.push(year);
    }
    return found;
  });
  const levels = $derived.by(() => {
    const step = top > 0.6 ? 0.2 : 0.1;
    const found: Array<number> = [];
    for (let level = 0; level <= top + 0.0001; level += step) {
      found.push(Math.round(level * 10) / 10);
    }
    return found;
  });
  const gaps = $derived.by(() => {
    const found: Array<number> = [];
    for (const entry of series) {
      for (const [index, point] of entry.points.entries()) {
        const next = entry.points[index + 1];
        if (next != null) {
          found.push(next.at - point.at);
        }
      }
    }
    return found.toSorted((a, b) => a - b);
  });
  const usual = $derived(gaps[Math.floor(gaps.length / 2)] ?? 4);
  const band = $derived(
    Math.max(
      4,
      Math.min(
        22,
        usual *
          0.55 *
          ((width - PAD.left - PAD.right) / (range.high - range.low)),
      ),
    ),
  );
  const traced = $derived(
    (active?.points ?? []).filter((point) => point.share != null),
  );
  const best = $derived(
    traced.toSorted((a, b) => (b.share ?? 0) - (a.share ?? 0))[0],
  );
  const latest = $derived(traced.at(-1));
  const lit = $derived(
    hovered == null ? null : (active?.points[hovered] ?? null),
  );

  function line(points: Array<{ at: number; share: number | null }>) {
    let path = "";
    let last = -Infinity;
    for (const point of points) {
      if (point.share == null) {
        continue;
      }
      const isOpen = point.at - last <= Math.max(usual * 2.5, 6);
      last = point.at;
      path += `${isOpen ? "L" : "M"}${sx(point.at).toFixed(1)},${sy(point.share).toFixed(1)}`;
    }
    return path;
  }

  function seek(event: PointerEvent) {
    const box = (event.currentTarget as SVGElement).getBoundingClientRect();
    const x = event.clientX - box.left;
    let nearest: number | null = null;
    let distance = Infinity;
    for (const [index, point] of (active?.points ?? []).entries()) {
      const gap = Math.abs(sx(point.at) - x);
      if (gap < distance) {
        distance = gap;
        nearest = index;
      }
    }
    hovered = distance < 40 ? nearest : null;
  }
</script>

{#if active != null}
  <section
    class="relative col-span-12 grid grid-cols-[minmax(0,1fr)] gap-3 bg-card px-3.5 pbs-3.5 pbe-3"
    style:--party={color}
    aria-labelledby="history-title"
    data-export
  >
    <Export name="history" class="absolute inset-e-2 inset-bs-2" />
    <div class="flex flex-wrap items-end justify-between gap-x-6 gap-y-2 pe-8">
      <div class="grid gap-0.5">
        <h2
          id="history-title"
          class="font-serif text-xl/tight font-[640] tracking-[-0.005em]"
        >
          {m.party_history_title()}
        </h2>
        <p class="text-[0.8125rem] text-muted-foreground max-inline-[64ch]">
          {m.party_history_lede({ name })}
        </p>
      </div>
      <dl class="flex gap-6">
        {#if latest?.share != null}
          <div class="grid">
            <dt class="text-xs text-muted-foreground">
              {m.party_history_latest()}, {latest.year}
            </dt>
            <dd class="font-mono text-xl tracking-[-0.02em] tabular-nums">
              {percent(latest.share)}
            </dd>
          </div>
        {/if}
        {#if best?.share != null && best !== latest}
          <div class="grid">
            <dt class="text-xs text-muted-foreground">
              {m.party_history_best()}, {best.year}
            </dt>
            <dd class="font-mono text-xl tracking-[-0.02em] tabular-nums">
              {percent(best.share)}
            </dd>
          </div>
        {/if}
      </dl>
    </div>
    {#if series.length > 1}
      <div class="flex flex-wrap gap-1.5" role="group">
        {#each series as entry (entry.id)}
          <button
            type="button"
            class={cn(
              "flex keycap items-center gap-1.5 rounded-sm border border-input bg-card px-2 text-[0.8125rem] block-7 hover:bg-secondary",
              { "marked bg-secondary font-semibold": entry === active },
            )}
            aria-pressed={entry === active}
            onclick={() => {
              chosen = entry.id;
              hovered = null;
            }}
          >
            {entry.name}
            <span class="font-mono text-xs text-muted-foreground tabular-nums"
              >{entry.points.length}</span
            >
          </button>
        {/each}
      </div>
    {/if}
    <div class="relative min-inline-0" bind:clientWidth={width}>
      <svg
        class="block touch-pan-y overflow-visible block-auto max-inline-full"
        viewBox="0 0 {width} {height}"
        {width}
        {height}
        role="img"
        aria-label={m.party_history_title()}
        onpointermove={seek}
        onpointerdown={seek}
        onpointerleave={() => (hovered = null)}
      >
        {#each levels as level (level)}
          <line
            x1={PAD.left}
            x2={width - PAD.right}
            y1={sy(level)}
            y2={sy(level)}
            class={cn("stroke-border", {
              "stroke-muted-foreground/70": level === 0,
            })}
          />
          <text
            x={PAD.left - 6}
            y={sy(level) + 3.5}
            text-anchor="end"
            class="fill-muted-foreground font-mono text-[0.625rem] tabular-nums"
            >{Math.round(level * 100)}</text
          >
        {/each}
        {#if top >= 0.5}
          <line
            x1={PAD.left}
            x2={width - PAD.right}
            y1={sy(0.5)}
            y2={sy(0.5)}
            class="stroke-foreground/60"
            stroke-dasharray="3 3"
          />
          <text
            x={width - PAD.right}
            y={sy(0.5) - 4}
            text-anchor="end"
            class="fill-muted-foreground text-[0.625rem] font-semibold"
            >{m.party_history_majority()}</text
          >
        {/if}
        {#each years as year (year)}
          <text
            x={sx(year)}
            y={height - 8}
            text-anchor="middle"
            class="fill-muted-foreground font-mono text-[0.625rem] tabular-nums"
            >{year}</text
          >
        {/each}
        {#if !isOffice}
          {#each active.points as point, index (index)}
            {#if point.held != null}
              <rect
                x={sx(point.at) - band / 2}
                y={sy(point.held)}
                width={band}
                height={Math.max(0, sy(0) - sy(point.held))}
                fill="color-mix(in oklab, var(--party) {hovered === index
                  ? 34
                  : 20}%, transparent)"
              />
              <line
                x1={sx(point.at) - band / 2}
                x2={sx(point.at) + band / 2}
                y1={sy(point.held)}
                y2={sy(point.held)}
                stroke="var(--party)"
                stroke-width="2"
              />
            {/if}
          {/each}
        {/if}
        {#each series as entry (entry.id)}
          {#if entry !== active}
            <path
              d={line(entry.points)}
              fill="none"
              class="stroke-muted-foreground/70"
              stroke-width="1.25"
              stroke-dasharray="4 3"
            />
            {#each entry.points as point, index (index)}
              {#if point.share != null}
                <circle
                  cx={sx(point.at)}
                  cy={sy(point.share)}
                  r="2"
                  class="fill-muted-foreground/70"
                />
              {/if}
            {/each}
          {/if}
        {/each}
        <path
          d={line(active.points)}
          fill="none"
          stroke="var(--party)"
          stroke-width="2.5"
          stroke-linejoin="round"
        />
        {#each active.points as point, index (index)}
          {#if point.share != null}
            <circle
              cx={sx(point.at)}
              cy={sy(point.share)}
              r={hovered === index ? 5 : 3.5}
              fill={isOffice && (point.seats ?? 0) === 0
                ? "var(--card)"
                : "var(--party)"}
              stroke={isOffice ? "var(--party)" : "var(--card)"}
              stroke-width={isOffice ? 2 : 1.5}
            />
          {/if}
        {/each}
        {#if lit != null}
          <line
            x1={sx(lit.at)}
            x2={sx(lit.at)}
            y1={PAD.top}
            y2={sy(0)}
            class="stroke-foreground/40"
          />
        {/if}
      </svg>
      {#if lit != null}
        <div
          class="pointer-events-none absolute grid gap-0.5 rounded-sm bg-card/95 px-2.5 py-1.5 text-xs shadow-menu ring-1 ring-border max-inline-60"
          style:inset-inline-start="{Math.max(
            0,
            Math.min(width - 240, sx(lit.at) + 12),
          )}px"
          style:inset-block-start="{PAD.top}px"
        >
          <span class="text-[0.8125rem] font-semibold">{lit.name}</span>
          {#if lit.share != null}
            <span class="flex justify-between gap-4">
              <span class="text-muted-foreground"
                >{m.party_history_votes()}</span
              >
              <span class="font-mono font-semibold tabular-nums"
                >{percent(lit.share)}</span
              >
            </span>
          {/if}
          {#if lit.seats != null && lit.filled != null}
            <span class="flex justify-between gap-4">
              <span class="text-muted-foreground"
                >{isOffice
                  ? lit.seats > 0
                    ? m.person_won_mark()
                    : m.person_lost_mark()
                  : m.party_history_seats()}</span
              >
              {#if !isOffice}
                <span class="font-mono tabular-nums"
                  >{m.party_history_seats_of({
                    seats: count(lit.seats),
                    filled: count(lit.filled),
                  })}</span
                >
              {/if}
            </span>
          {/if}
        </div>
      {/if}
    </div>
    <ol class="flex gap-1 overflow-x-auto pbe-1 text-xs">
      {#each active.points.toReversed() as point, index (index)}
        <li class="shrink-0">
          <svelte:element
            this={point.path == null ? "span" : "a"}
            href={point.path == null
              ? undefined
              : resolve("/[...path]", { path: point.path })}
            class={cn(
              "grid rounded-xs border-s-2 border-(--party) bg-secondary/50 px-2 py-1",
              { "hover:bg-secondary": point.path != null },
            )}
          >
            <span class="font-mono font-semibold tabular-nums"
              >{point.year}</span
            >
            <span class="font-mono text-muted-foreground tabular-nums"
              >{point.share == null ? "–" : percent(point.share)}</span
            >
            {#if !isOffice && point.seats != null}
              <span class="text-muted-foreground"
                >{m.lists_seats({ count: point.seats })}</span
              >
            {/if}
          </svelte:element>
        </li>
      {/each}
    </ol>
  </section>
{/if}
