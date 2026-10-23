<script lang="ts">
  import { resolve } from "$app/paths";
  import Export from "#lib/components/export.svelte";
  import { percent, points } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { getLocale } from "#lib/paraglide/runtime.js";
  import { swatch } from "#lib/party.js";
  import { cn } from "#lib/utils.js";
  import type { PageData } from "./$types";

  const {
    data,
    first,
  }: {
    data: NonNullable<PageData["parties"]>;
    first: { id: string; name: string } | undefined;
  } = $props();

  const PAD = { top: 12, right: 12, bottom: 24, left: 36 };

  let width = $state(720);
  let hovered = $state<{ series: number; point: number } | null>(null);
  let opened = $state<Array<string>>([]);

  const sides = $derived(
    data.parties.map((party) => ({
      ...party,
      label: party.short ?? party.name,
      tone: swatch(party.color, `party:${party.id}`),
    })),
  );
  const charts = $derived(
    data.series.map((series) => {
      const shares = series.points.flatMap((point) =>
        point.sides.flatMap((side) => (side == null ? [] : [side.share])),
      );
      const placed = series.points.map((point) => ({
        ...point,
        at:
          point.year +
          (Date.parse(`${point.date}T00:00:00Z`) - Date.UTC(point.year, 0, 1)) /
            31_622_400_000,
        label:
          series.points.filter((other) => other.year === point.year).length > 1
            ? new Intl.DateTimeFormat(getLocale(), {
                month: "short",
                year: "numeric",
                timeZone: "UTC",
              }).format(Date.parse(`${point.date}T00:00:00Z`))
            : String(point.year),
      }));
      return {
        ...series,
        points: placed,
        top: Math.max(0.1, Math.ceil(Math.max(...shares) * 10) / 10),
        low: Math.min(...placed.map((point) => point.at)),
        high: Math.max(...placed.map((point) => point.at)),
      };
    }),
  );
  const height = $derived(
    Math.round(Math.min(300, Math.max(200, width * 0.34))),
  );
  const sx = (chart: { low: number; high: number }, year: number) =>
    chart.high === chart.low
      ? PAD.left + (width - PAD.left - PAD.right) / 2
      : PAD.left +
        ((year - chart.low) / (chart.high - chart.low)) *
          (width - PAD.left - PAD.right);
  const sy = (chart: { top: number }, value: number) =>
    PAD.top +
    ((chart.top - value) / chart.top) * (height - PAD.top - PAD.bottom);
  const ticks = $derived(
    charts.map((chart) => {
      const shown: Array<number> = [];
      let last = -Infinity;
      for (const [point, row] of chart.points.entries()) {
        const x = sx(chart, row.at);
        if (x - last >= 30) {
          shown.push(point);
          last = x;
        }
      }
      return shown;
    }),
  );
  const line = (chart: (typeof charts)[number], side: number) => {
    let last: number | null = null;
    let text = "";
    for (const point of chart.points) {
      const value = point.sides[side]?.share;
      if (value != null) {
        text += `${last == null || point.at - last > 8 ? "M" : "L"}${sx(chart, point.at).toFixed(1)},${sy(chart, value).toFixed(1)}`;
        last = point.at;
      }
    }
    return text;
  };
</script>

{#snippet share(
  side: { share: number; seats: number | null; filled: number | null } | null,
  isStrong = false,
)}
  {#if side == null}
    <span class="text-muted-foreground">–</span>
  {:else}
    <span class={cn({ "font-semibold": isStrong })}>{percent(side.share)}</span>
    {#if side.seats != null && (side.filled ?? 0) > 1}
      <span class="ms-1 text-xs text-muted-foreground max-sm:hidden"
        >{m.lists_seats({ count: side.seats })}</span
      >
    {/if}
  {/if}
{/snippet}

{#if charts.length === 0}
  <p class="col-span-12 bg-card px-3.5 py-4 text-sm text-muted-foreground">
    {m.versus_parties_none()}
    {#if first != null}
      <a
        class="mbs-1 block font-semibold text-foreground underline decoration-border underline-offset-4 hover:decoration-foreground"
        href={resolve(`/compare?a=${first.id}`)}
        >{m.versus_none_rivals({ name: first.name })}</a
      >
    {/if}
  </p>
{/if}

{#each charts as chart, index (chart.id)}
  {const focus = $derived(
    hovered?.series === index ? (chart.points[hovered.point] ?? null) : null,
  )}
  <section
    class="relative col-span-12 grid grid-cols-[minmax(0,1fr)] gap-3 bg-card px-3.5 pbs-3.5 pbe-3"
    aria-labelledby="party-series-{index}"
    data-export
  >
    <Export name="parties" class="absolute inset-e-2 inset-bs-2" />
    <div class="grid gap-0.5 pe-8">
      <h2 id="party-series-{index}" class="heading">
        {chart.name}
      </h2>
      <p class="text-[0.8125rem] text-muted-foreground">
        {m.versus_parties_key()}
      </p>
    </div>
    <div class="flex flex-wrap gap-x-5 gap-y-1 text-[0.8125rem]">
      {#each sides as side, at (side.id)}
        <span class="inline-flex items-center gap-2" style:--party={side.tone}>
          <svg
            class="block-2.5 inline-6"
            viewBox="0 0 24 10"
            aria-hidden="true"
          >
            <line
              x1="0"
              x2="24"
              y1="5"
              y2="5"
              class="stroke-(--party)"
              stroke-width="2"
              stroke-dasharray={at === 0 ? "" : "5 3"}
            />
            {#if at === 0}
              <circle cx="12" cy="5" r="3.5" class="fill-(--party)" />
            {:else}
              <rect x="9" y="2" width="6" height="6" class="fill-(--party)" />
            {/if}
          </svg>
          <span class="font-semibold">{side.label}</span>
          {#if focus != null}
            <span class="font-figures text-xs tabular-nums"
              >{@render share(focus.sides[at] ?? null)}</span
            >
          {/if}
        </span>
      {/each}
      {#if focus != null}
        <span class="ms-auto font-figures text-xs text-muted-foreground"
          >{focus.label}</span
        >
      {/if}
    </div>
    <div bind:clientWidth={width} class="min-inline-0">
      <svg
        viewBox="0 0 {width} {height}"
        class="block overflow-visible inline-full"
        style:block-size="{height}px"
        role="img"
        aria-label={chart.name}
        onpointerleave={() => (hovered = null)}
        onpointermove={(event) => {
          const box = event.currentTarget.getBoundingClientRect();
          const x = event.clientX - box.left;
          let best = { point: 0, distance: Infinity };
          for (const [point, row] of chart.points.entries()) {
            const distance = Math.abs(sx(chart, row.at) - x);
            if (distance < best.distance) {
              best = { point, distance };
            }
          }
          hovered = { series: index, point: best.point };
        }}
      >
        {#each [chart.top, chart.top / 2, 0] as tick (tick)}
          <line
            x1={PAD.left}
            x2={width - PAD.right}
            y1={sy(chart, tick)}
            y2={sy(chart, tick)}
            class={cn("stroke-border", { "stroke-foreground/40": tick === 0 })}
          />
          <text
            x={PAD.left - 6}
            y={sy(chart, tick) + 3.5}
            text-anchor="end"
            class="fill-muted-foreground font-figures text-[0.625rem]"
            >{Math.round(tick * 100)}</text
          >
        {/each}
        {#each chart.points as row, point (row.date)}
          <text
            x={sx(chart, row.at)}
            y={height - 6}
            text-anchor="middle"
            class={cn("fill-muted-foreground font-figures text-[0.625rem]", {
              hidden: !(ticks[index] ?? []).includes(point),
              "fill-foreground": focus === row,
            })}>{row.year}</text
          >
        {/each}
        {#if focus != null}
          <line
            x1={sx(chart, focus.at)}
            x2={sx(chart, focus.at)}
            y1={PAD.top}
            y2={height - PAD.bottom}
            class="stroke-foreground/30"
          />
        {/if}
        {#each sides as side, at (side.id)}
          <g style:--party={side.tone}>
            <path
              d={line(chart, at)}
              fill="none"
              class="stroke-(--party)"
              stroke-width="2"
              stroke-dasharray={at === 0 ? "" : "5 3"}
              stroke-linejoin="round"
            />
            {#each chart.points as row (row.date)}
              {const value = $derived(row.sides[at]?.share ?? null)}
              {const r = $derived(focus === row ? 5 : 3.5)}
              {#if value != null}
                {#if at === 0}
                  <circle
                    cx={sx(chart, row.at)}
                    cy={sy(chart, value)}
                    {r}
                    class="fill-(--party)"
                  />
                {:else}
                  <rect
                    x={sx(chart, row.at) - r * 0.9}
                    y={sy(chart, value) - r * 0.9}
                    width={r * 1.8}
                    height={r * 1.8}
                    class="fill-(--party)"
                  />
                {/if}
              {/if}
            {/each}
          </g>
        {/each}
      </svg>
    </div>
    <div class="overflow-x-auto">
      <table class="border-collapse text-sm tabular-nums inline-full">
        <thead>
          <tr class="border-be text-xs text-muted-foreground">
            <th scope="col" class="py-1.5 pe-3 text-start font-semibold"
              >{m.versus_year()}</th
            >
            {#each sides as side (side.id)}
              <th scope="col" class="px-2 py-1.5 text-end font-semibold"
                >{side.label}</th
              >
            {/each}
            <th scope="col" class="py-1.5 ps-2 text-end font-semibold"
              >{m.versus_gap()}</th
            >
          </tr>
        </thead>
        <tbody>
          {#each opened.includes(chart.id) ? chart.points.toReversed() : chart.points
                .toReversed()
                .slice(0, 12) as row (row.date)}
            {const a = $derived(row.sides[0]?.share ?? null)}
            {const b = $derived(row.sides[1]?.share ?? null)}
            {const lead = $derived(
              a == null || b == null ? null : (sides[a >= b ? 0 : 1] ?? null),
            )}
            <tr
              class={cn("border-be border-border/60", {
                "bg-secondary": focus === row,
              })}
            >
              <th
                scope="row"
                class="py-1.5 pe-3 text-start font-normal whitespace-nowrap"
              >
                {#if row.path == null}
                  <span class="font-figures">{row.label}</span>
                {:else}
                  <a
                    class="font-figures hover:highlighter"
                    href={resolve("/[...path]", { path: row.path })}
                    title={row.name}>{row.label}</a
                  >
                {/if}
              </th>
              {#each sides as side, at (side.id)}
                <td
                  class="px-2 py-1.5 text-end font-figures text-[0.8125rem] whitespace-nowrap"
                  >{@render share(row.sides[at] ?? null, true)}</td
                >
              {/each}
              <td
                class="py-1.5 ps-2 text-end font-figures text-[0.8125rem] whitespace-nowrap"
              >
                {#if a == null || b == null || lead == null}
                  <span class="text-muted-foreground">–</span>
                {:else if Math.abs(a - b) < 0.0005}
                  {m.versus_even()}
                {:else}
                  <span
                    class="inline-flex items-center justify-end gap-1.5"
                    style:--party={lead.tone}
                  >
                    <span
                      class="shrink-0 rounded-xs bg-(--party) block-2 inline-2"
                    ></span>
                    <span
                      class="font-sans text-xs text-muted-foreground max-sm:sr-only"
                      >{lead.label}</span
                    >
                    {points(Math.abs(a - b)).replace(/^-/u, "+")}
                  </span>
                {/if}
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
    {#if chart.points.length > 12}
      {const isOpen = $derived(opened.includes(chart.id))}
      <button
        type="button"
        class="justify-self-start rounded-sm border border-input bg-card px-2.5 text-[0.8125rem] font-semibold keycap block-7 hover:bg-secondary"
        aria-expanded={isOpen}
        onclick={() =>
          (opened = isOpen
            ? opened.filter((id) => id !== chart.id)
            : [...opened, chart.id])}
        >{isOpen
          ? m.polity_results_fewer()
          : m.polity_results_more({
              count: String(chart.points.length),
            })}</button
      >
    {/if}
  </section>
{/each}
