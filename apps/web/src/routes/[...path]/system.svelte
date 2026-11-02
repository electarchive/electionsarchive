<script lang="ts">
  import { resolve } from "$app/paths";
  import { createQuery } from "@tanstack/svelte-query";
  import Measured from "#lib/components/measured.svelte";
  import { Segmented } from "#lib/components/ui/segmented/index.js";
  import { caveatLabel, figure } from "#lib/indices.js";
  import { m } from "#lib/paraglide/messages.js";
  import { orpc } from "#lib/rpc.js";

  const { id }: { id: string } = $props();

  const uid = $props.id();
  const PAD = { top: 10, bottom: 24, start: 34, end: 64 };
  const HEIGHT = 220;

  let isNear = $state(false);
  let view = $state<"effective" | "gallagher" | "volatility">("effective");
  let chosen = $state<string | null>(null);
  let width = $state(0);
  let hovered = $state<number | null>(null);

  const indices = createQuery(() => ({
    ...orpc.indices.polity.queryOptions({ input: { id } }),
    enabled: isNear,
  }));

  const near = (node: HTMLElement) => {
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          isNear = true;
          observer.disconnect();
        }
      },
      { rootMargin: "600px 0px" },
    );
    observer.observe(node);
    return () => observer.disconnect();
  };

  const series = $derived(
    (indices.data?.series ?? []).filter((entry) => entry.points.length > 1),
  );
  const current = $derived(
    series.find((entry) => entry.body === chosen) ?? series[0] ?? null,
  );
  const points = $derived(current?.points ?? []);

  const lines = $derived(
    view === "effective"
      ? [
          {
            key: "effectiveVotes" as const,
            label: m.indices_by_votes(),
            dash: null,
          },
          {
            key: "effectiveSeats" as const,
            label: m.indices_by_seats(),
            dash: "5 3",
          },
        ]
      : [
          {
            key: view,
            label:
              view === "gallagher"
                ? m.indices_gallagher()
                : m.indices_volatility(),
            dash: null,
          },
        ],
  );

  const viewOptions = $derived([
    { value: "effective", label: m.indices_effective() },
    { value: "gallagher", label: m.indices_gallagher() },
    { value: "volatility", label: m.indices_volatility() },
  ]);
  const chamberOptions = $derived(
    series.map((entry) => ({ value: entry.body, label: entry.name })),
  );

  const time = (date: string) => {
    const [year = 0, month = 1, day = 1] = date.split("-").map(Number);
    return year + (month - 1) / 12 + (day - 1) / 365;
  };

  const chart = $derived.by(() => {
    const inner = {
      w: Math.max(width - PAD.start - PAD.end, 1),
      h: HEIGHT - PAD.top - PAD.bottom,
    };
    const first = time(points[0]?.date ?? "0");
    const last = time(points.at(-1)?.date ?? "0");
    const x = (date: string) =>
      PAD.start +
      (last === first
        ? inner.w / 2
        : ((time(date) - first) / (last - first)) * inner.w);
    let top = 0;
    for (const point of points) {
      for (const line of lines) {
        top = Math.max(top, point[line.key] ?? 0);
      }
    }
    const raw = top / 4;
    const magnitude = 10 ** Math.floor(Math.log10(raw > 0 ? raw : 1));
    const step =
      [1, 2, 2.5, 5, 10]
        .map((factor) => factor * magnitude)
        .find((candidate) => candidate >= raw) ?? magnitude * 10;
    const ceiling = Math.max(step * Math.ceil(top / step), step);
    const y = (value: number) =>
      PAD.top + inner.h - (value / ceiling) * inner.h;
    const ticks: Array<number> = [];
    for (let tick = 0; tick <= ceiling + step / 2; tick += step) {
      ticks.push(Number(tick.toFixed(6)));
    }
    const xs = points.map((point) => x(point.date));
    const columns = points.map((point, index) => {
      const before = xs[index - 1];
      const after = xs[index + 1];
      const at = xs[index] ?? 0;
      const from = before == null ? PAD.start - 6 : (before + at) / 2;
      const to = after == null ? PAD.start + inner.w + 6 : (after + at) / 2;
      return { index, x: at, from, w: Math.max(to - from, 1), point };
    });
    const paths = lines.map((line) => {
      let d = "";
      let isOpen = false;
      for (const [index, point] of points.entries()) {
        const value = point[line.key];
        if (value == null) {
          isOpen = false;
          continue;
        }
        d += `${isOpen ? "L" : "M"}${(xs[index] ?? 0).toFixed(1)} ${y(value).toFixed(1)}`;
        isOpen = true;
      }
      const end = points.findLastIndex((point) => point[line.key] != null);
      const value = end < 0 ? null : (points[end]?.[line.key] ?? null);
      return {
        ...line,
        d,
        end: end < 0 || value == null ? null : { x: xs[end] ?? 0, y: y(value) },
      };
    });
    const span = Math.max(last - first, 1);
    const every =
      [1, 2, 4, 5, 10, 20, 25, 50, 100].find(
        (candidate) => (candidate / span) * inner.w >= 48,
      ) ?? 100;
    const years: Array<{ year: number; x: number }> = [];
    for (
      let year = Math.ceil(first / every) * every;
      year <= last;
      year += every
    ) {
      years.push({ year, x: x(`${year}-01-01`) });
    }
    return { inner, y, ticks, columns, paths, years };
  });

  const labels = $derived.by(() => {
    const placed = chart.paths.flatMap((path) =>
      path.end == null
        ? []
        : [{ key: path.key, label: path.label, y: path.end.y }],
    );
    if (
      placed.length === 2 &&
      Math.abs((placed[0]?.y ?? 0) - (placed[1]?.y ?? 0)) < 14
    ) {
      const [upper, lower] = placed.toSorted((a, b) => a.y - b.y);
      if (upper != null && lower != null) {
        const middle = (upper.y + lower.y) / 2;
        upper.y = middle - 7;
        lower.y = middle + 7;
      }
    }
    return placed;
  });

  const lit = $derived(
    hovered == null ? null : (chart.columns[hovered] ?? null),
  );
  const recent = $derived(points.toReversed());
  const reading = $derived(
    {
      effective: m.indices_series_read_effective,
      gallagher: m.indices_series_read_gallagher,
      volatility: m.indices_series_read_volatility,
    }[view](),
  );
</script>

<section
  class="relative col-span-12 grid grid-cols-[minmax(0,1fr)] gap-3 bg-card px-3.5 pbs-3.5 pbe-3"
  aria-labelledby="{uid}-title"
  hidden={(indices.isSuccess || indices.isError) && current == null}
  {@attach near}
>
  <div class="flex flex-wrap items-end justify-between gap-x-6 gap-y-2">
    <div class="grid gap-0.5">
      <h2 id="{uid}-title" class="heading">{m.indices_series_title()}</h2>
      <p class="text-[0.8125rem] text-muted-foreground max-inline-[64ch]">
        {m.indices_series_lede()}
        <a
          class="font-semibold whitespace-nowrap text-foreground underline decoration-border underline-offset-4 hover:decoration-foreground"
          href={resolve("/[slug=project]", { slug: "indices" })}
          >{m.indices_method()}</a
        >
      </p>
    </div>
    <div class="flex flex-wrap items-center gap-2">
      {#if chamberOptions.length > 1}
        <Segmented
          options={chamberOptions}
          value={current?.body}
          label={m.indices_series_chamber()}
          onValueChange={(next: string) => {
            chosen = next;
            hovered = null;
          }}
        />
      {/if}
      <Segmented
        options={viewOptions}
        value={view}
        label={m.indices_series_views()}
        onValueChange={(next: string) => {
          if (
            next === "effective" ||
            next === "gallagher" ||
            next === "volatility"
          ) {
            view = next;
          }
        }}
      />
    </div>
  </div>
  {#if current == null}
    <div
      class="animate-pulse rounded-sm bg-muted motion-reduce:animate-none"
      style:block-size="{HEIGHT}px"
      aria-busy="true"
    ></div>
  {:else}
    <ul
      class="flex flex-wrap gap-x-4 gap-y-1 text-xs min-block-4"
      aria-hidden="true"
    >
      {#if view === "effective"}
        {#each lines as line (line.key)}
          <li class="flex items-center gap-1.5">
            <svg width="18" height="6" class="overflow-visible">
              <line
                x1="0"
                x2="18"
                y1="3"
                y2="3"
                stroke-width="2"
                stroke-dasharray={line.dash}
                class={line.dash == null
                  ? "stroke-foreground"
                  : "stroke-muted-foreground"}
              />
            </svg>
            {line.label}
          </li>
        {/each}
      {/if}
    </ul>
    <Measured
      bind:width
      class="relative min-inline-0 pointer-coarse:hidden"
      reserve="{HEIGHT}px"
    >
      <svg
        class="block overflow-visible max-inline-full"
        viewBox="0 0 {width} {HEIGHT}"
        {width}
        height={HEIGHT}
        role="group"
        aria-label={m.indices_series_chart({
          name:
            viewOptions.find((option) => option.value === view)?.label ?? "",
          chamber: current.name,
        })}
      >
        {#each chart.ticks as tick (tick)}
          <line
            x1={PAD.start}
            x2={PAD.start + chart.inner.w}
            y1={chart.y(tick)}
            y2={chart.y(tick)}
            class={tick === 0 ? "stroke-muted-foreground" : "stroke-border"}
            stroke-width="1"
          />
          <text
            x={PAD.start - 6}
            y={chart.y(tick) + 3.5}
            text-anchor="end"
            class="fill-muted-foreground font-figures text-[0.625rem] tabular-nums"
            >{tick}</text
          >
        {/each}
        {#each chart.years as year (year.year)}
          <text
            x={year.x}
            y={HEIGHT - 6}
            text-anchor="middle"
            class="fill-muted-foreground font-figures text-[0.625rem] tabular-nums"
            >{year.year}</text
          >
        {/each}
        {#if lit != null}
          <line
            x1={lit.x}
            x2={lit.x}
            y1={PAD.top}
            y2={PAD.top + chart.inner.h}
            class="stroke-muted-foreground"
            stroke-width="1"
          />
        {/if}
        {#each chart.paths as path (path.key)}
          <path
            d={path.d}
            fill="none"
            stroke-width="2"
            stroke-linejoin="round"
            stroke-linecap="round"
            stroke-dasharray={path.dash}
            class={path.dash == null
              ? "stroke-foreground"
              : "stroke-muted-foreground"}
          />
          {#each chart.columns as column (column.index)}
            {const value = $derived(column.point[path.key])}
            {#if value != null && (points.length <= 40 || hovered === column.index)}
              <circle
                cx={column.x}
                cy={chart.y(value)}
                r={hovered === column.index ? 4 : 2.5}
                class={path.dash == null
                  ? "fill-foreground"
                  : "fill-muted-foreground"}
                stroke-width="2"
                stroke="var(--card)"
              />
            {/if}
          {/each}
        {/each}
        {#each labels as label (label.key)}
          {const end = $derived(
            chart.paths.find((path) => path.key === label.key)?.end,
          )}
          {#if end != null && view === "effective"}
            <text
              x={end.x + 8}
              y={label.y + 3.5}
              class="fill-foreground text-[0.6875rem]">{label.label}</text
            >
          {/if}
        {/each}
        {#each chart.columns as column (column.index)}
          <a
            href={column.point.path == null
              ? undefined
              : resolve("/[...path]", { path: column.point.path })}
            aria-label="{column.point.name}: {lines
              .map((line) => {
                const value = column.point[line.key];
                return value == null
                  ? null
                  : `${line.label} ${figure(line.key, value)}`;
              })
              .filter((part) => part != null)
              .join(', ')}"
            class="outline-none"
            onpointerenter={() => (hovered = column.index)}
            onpointerleave={() => (hovered = null)}
            onfocus={() => (hovered = column.index)}
            onblur={() => (hovered = null)}
          >
            <rect
              x={column.from}
              y={PAD.top}
              width={column.w}
              height={chart.inner.h}
              fill="transparent"
            />
          </a>
        {/each}
      </svg>
      {#if lit != null}
        <div
          class="pointer-events-none absolute grid gap-1 rounded-sm bg-card/95 px-2.5 py-2 text-xs shadow-menu ring-1 ring-border inline-60"
          style:inset-inline-start="{lit.x + 252 > width
            ? Math.max(0, lit.x - 252)
            : lit.x + 12}px"
          style:inset-block-start="{PAD.top}px"
        >
          <span class="text-[0.8125rem] font-semibold">{lit.point.name}</span>
          {#each lines as line (line.key)}
            {const value = $derived(lit.point[line.key])}
            {#if value != null}
              <span class="flex justify-between gap-3">
                <span class="text-muted-foreground">{line.label}</span>
                <span class="font-figures font-medium tabular-nums"
                  >{figure(line.key, value)}</span
                >
              </span>
            {/if}
          {/each}
          {#each lit.point.caveats.filter((caveat) => caveat !== "pooled_independents") as caveat (caveat)}
            <span class="text-pretty text-muted-foreground"
              >{caveatLabel(caveat)}</span
            >
          {/each}
        </div>
      {/if}
    </Measured>
    <div class="hidden overflow-y-auto max-block-80 pointer-coarse:block">
      <table class="text-sm inline-full">
        <thead>
          <tr class="border-be border-border text-xs text-muted-foreground">
            <th class="py-1.5 text-start font-normal">{m.indices_election()}</th
            >
            {#each lines as line (line.key)}
              <th class="py-1.5 text-end font-normal">{line.label}</th>
            {/each}
          </tr>
        </thead>
        <tbody>
          {#each recent as point (point.date)}
            <tr class="border-be border-border/60 last:border-be-0">
              <td class="py-1">
                {#if point.path == null}
                  <span class="font-figures tabular-nums">{point.year}</span>
                {:else}
                  <a
                    class="inline-flex items-center font-figures tabular-nums underline decoration-border underline-offset-4 min-block-11"
                    href={resolve("/[...path]", { path: point.path })}
                    aria-label={point.name}>{point.year}</a
                  >
                {/if}
              </td>
              {#each lines as line (line.key)}
                {const value = $derived(point[line.key])}
                <td class="py-1 text-end font-figures tabular-nums"
                  >{value == null ? "" : figure(line.key, value)}</td
                >
              {/each}
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
    <p
      class="text-xs/relaxed text-pretty text-muted-foreground max-inline-[70ch]"
    >
      {reading}
    </p>
  {/if}
</section>
