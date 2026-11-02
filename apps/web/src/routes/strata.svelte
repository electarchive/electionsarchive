<script lang="ts">
  import { resolve } from "$app/paths";
  import { count } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";
  import { cn } from "#lib/utils.js";
  import type { PageData } from "./$types";

  const { strata, today }: { strata: PageData["strata"]; today: string } =
    $props();

  let at = $state<number | null>(null);
  let isTouch = false;
  let before: number | null = null;

  const toYear = (value: string) => {
    const time = Date.parse(`${value}T00:00:00Z`);
    const year = new Date(time).getUTCFullYear();
    const start = Date.UTC(year, 0, 1);
    return year + (time - start) / (Date.UTC(year + 1, 0, 1) - start);
  };
  const round = (value: number, places = 100) =>
    Math.round(value * places) / places;

  const now = $derived(toYear(today));
  const from = $derived(
    Math.floor(
      Math.min(
        ...strata.rows.map((row) => toYear(row.points[0]?.date ?? today)),
      ),
    ),
  );
  const span = $derived(Math.max(1, now - from));
  const along = (year: number) => ((year - from) / span) * 100;
  const ticks = $derived(
    [1850, 1900, 1950, 2000].filter((year) => year > from + 20),
  );
  const rows = $derived(
    strata.rows.map((row) => {
      const columns = row.points.map((point, index) => {
        const x0 = toYear(point.date);
        const next = row.points[index + 1];
        return {
          ...point,
          x0,
          x1: Math.min(next == null ? now : toYear(next.date), x0 + 10),
        };
      });
      const shapes = new Map<number, string>();
      let seams = "";
      for (const [index, column] of columns.entries()) {
        const x = round(column.x0 - from);
        const w = round(Math.max(0.05, column.x1 - column.x0));
        if (index > 0) {
          seams += `M${x} 0V100`;
        }
        let below = 0;
        for (const [key, seats] of column.seats) {
          const y = round(100 - ((below + seats) / column.total) * 100, 10);
          const h = round((seats / column.total) * 100, 10);
          shapes.set(
            key,
            `${shapes.get(key) ?? ""}M${x} ${y}h${w}v${h}h${-w}z`,
          );
          below += seats;
        }
      }
      return { ...row, columns, shapes: [...shapes], seams };
    }),
  );
  const nations = $derived(rows.filter((row) => row.isNation));
  const below = $derived(rows.filter((row) => !row.isNation));

  function tint(key: number) {
    if (key === -3) {
      return "color-mix(in oklab, var(--muted-foreground) 42%, var(--card))";
    }
    if (key === -2) {
      return swatch(null, "independent");
    }
    const party = strata.parties[key];
    return party == null
      ? swatch(null, "")
      : swatch(party.color, `party:${party.id}`);
  }

  function label(key: number) {
    if (key === -3) {
      return m.option_others();
    }
    if (key === -2) {
      return m.option_independents();
    }
    const party = strata.parties[key];
    return party == null
      ? m.home_strata_unknown()
      : (party.short ?? party.label);
  }

  function held(row: (typeof rows)[number], year: number | null) {
    if (year == null) {
      return row.columns.at(-1) ?? null;
    }
    const column = row.columns.findLast((entry) => entry.x0 <= year);
    return column != null && year <= column.x1 ? column : null;
  }

  function track(event: PointerEvent) {
    const box = (event.currentTarget as HTMLElement).getBoundingClientRect();
    const x = Math.max(
      0,
      Math.min(1, (event.clientX - box.left) / Math.max(box.width, 1)),
    );
    at = from + x * span;
  }

  function press(event: PointerEvent) {
    isTouch = event.pointerType !== "mouse";
    before = at;
    track(event);
  }

  function tap(event: MouseEvent, row: (typeof rows)[number]) {
    if (isTouch && held(row, before) !== held(row, at)) {
      event.preventDefault();
    }
    isTouch = false;
  }
</script>

{#snippet band(row: (typeof rows)[number])}
  {const column = $derived(held(row, at))}
  {const leader = $derived(
    column == null
      ? null
      : (column.seats.toSorted((a, b) => b[1] - a[1])[0] ?? null),
  )}
  <li
    class="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1 lg:grid-cols-[12.5rem_minmax(0,1fr)_11rem]"
  >
    <a
      class="grid leading-tight min-inline-0 lg:col-start-1 lg:row-start-1"
      href={resolve("/[...path]", { path: row.path })}
    >
      <span
        class="truncate text-[0.9375rem] font-semibold underline decoration-transparent underline-offset-4 hover:decoration-foreground"
        >{row.name}</span
      >
      <span class="truncate text-xs text-muted-foreground max-lg:hidden"
        >{row.body}</span
      >
    </a>
    <span
      class="flex items-baseline justify-end gap-2 text-[0.8125rem] min-inline-0 lg:col-start-3 lg:row-start-1 lg:justify-start"
    >
      {#if column != null && leader != null}
        <a
          class="font-figures font-bold tabular-nums underline decoration-border underline-offset-4 hover:decoration-foreground"
          href={resolve("/[...path]", { path: column.path })}
          aria-label={m.home_strata_open({
            year: column.date.slice(0, 4),
            body: row.body,
          })}>{column.date.slice(0, 4)}</a
        >
        <span class="flex items-baseline gap-1.5 min-inline-0">
          <span
            class="shrink-0 self-center rounded-[1px] block-2.5 inline-2.5"
            style:background-color={tint(leader[0])}
            aria-hidden="true"
          ></span>
          <span class="truncate">{label(leader[0])}</span>
          <span
            class="font-figures whitespace-nowrap text-muted-foreground tabular-nums"
            >{m.home_strata_seats({
              seats: count(leader[1]),
              total: count(column.total),
            })}</span
          >
        </span>
      {/if}
    </span>
    <div
      class={cn(
        "relative col-span-2 touch-pan-y select-none [-webkit-tap-highlight-color:transparent] lg:col-span-1 lg:col-start-2 lg:row-start-1",
        { "block-9": row.isNation, "block-6": !row.isNation },
      )}
      role="presentation"
      onpointermove={track}
      onpointerdown={press}
      onclick={(event) => tap(event, row)}
    >
      <span
        class="absolute inset-x-0 inset-be-0 bg-border block-px"
        aria-hidden="true"
      ></span>
      <svg
        class="sweep absolute inset-0 block block-full inline-full [&_a]:outline-none"
        viewBox="0 0 {round(span)} 100"
        preserveAspectRatio="none"
        aria-hidden="true"
      >
        {#each row.shapes as [key, d] (key)}
          <path {d} fill={tint(key)} shape-rendering="crispEdges" />
        {/each}
        <path
          d={row.seams}
          class="stroke-card max-sm:hidden"
          stroke-width="1"
          vector-effect="non-scaling-stroke"
          shape-rendering="crispEdges"
        />
        {#each row.columns as entry (entry.path)}
          <a
            href={resolve("/[...path]", { path: entry.path })}
            tabindex="-1"
            aria-label={entry.date.slice(0, 4)}
          >
            <rect
              x={round(entry.x0 - from)}
              y="0"
              width={round(Math.max(0.05, entry.x1 - entry.x0))}
              height="100"
              fill="transparent"
            />
          </a>
        {/each}
      </svg>
      {#if column != null && at != null}
        <span
          class="pointer-events-none absolute inset-y-0 bg-foreground/10"
          style:inset-inline-start="{along(column.x0)}%"
          style:inline-size="{along(column.x1) - along(column.x0)}%"
          aria-hidden="true"
        ></span>
      {/if}
      {#if at != null}
        <span
          class="pointer-events-none absolute -inset-y-1 -translate-x-1/2 bg-foreground inline-px"
          style:inset-inline-start="{along(at)}%"
          aria-hidden="true"
        ></span>
      {/if}
    </div>
  </li>
{/snippet}

<div class="grid gap-3" onpointerleave={() => (at = null)} role="presentation">
  <div
    class="grid grid-cols-[minmax(0,1fr)_auto] items-end gap-x-3 lg:grid-cols-[12.5rem_minmax(0,1fr)_11rem]"
  >
    <span
      class="font-figures text-[2rem] leading-none font-[780] tracking-[-0.03em] tabular-nums max-lg:hidden"
      aria-hidden="true">{at == null ? "" : Math.floor(at)}</span
    >
    <div
      class="relative col-span-2 cursor-ew-resize touch-pan-y rounded-xs outline-offset-4 select-none block-6 lg:col-span-1"
      role="slider"
      tabindex="0"
      aria-label={m.home_strata_slider()}
      aria-valuemin={from}
      aria-valuemax={Math.floor(now)}
      aria-valuenow={Math.floor(at ?? now)}
      onpointermove={track}
      onpointerdown={(event) => {
        event.currentTarget.setPointerCapture(event.pointerId);
        track(event);
      }}
      onblur={() => (at = null)}
      onkeydown={(event) => {
        const moves: Record<string, number> = {
          ArrowLeft: -1,
          ArrowDown: -1,
          ArrowRight: 1,
          ArrowUp: 1,
          PageDown: -10,
          PageUp: 10,
          Home: -Infinity,
          End: Infinity,
        };
        const move = moves[event.key];
        if (move != null) {
          event.preventDefault();
          at = Math.max(
            from,
            Math.min(now, Math.floor(at ?? now) + move + 0.99),
          );
        }
      }}
    >
      <span
        class="absolute inset-x-0 inset-be-0 bg-foreground block-px"
        aria-hidden="true"
      ></span>
      {#each [from, ...ticks] as tick (tick)}
        <span
          class={cn(
            "absolute inset-be-0 flex items-end font-figures text-[0.6875rem] text-muted-foreground tabular-nums block-full",
            { "-translate-x-1/2": tick !== from },
          )}
          style:inset-inline-start="{along(tick)}%"
          aria-hidden="true"
          ><span class="border-s border-foreground ps-1 leading-none block-2.5"
          ></span><span class="pbe-3">{tick}</span></span
        >
      {/each}
      {#if at != null}
        <span
          class="pointer-events-none absolute inset-bs-0 -translate-x-1/2 rounded-xs bg-foreground px-1 font-figures text-xs font-semibold text-background tabular-nums lg:hidden"
          style:inset-inline-start="{Math.min(94, Math.max(6, along(at)))}%"
          aria-hidden="true">{Math.floor(at)}</span
        >
      {/if}
    </div>
  </div>
  <div class="grid gap-1">
    <h3 class="text-xs font-semibold text-muted-foreground">
      {m.home_strata_nations()}
    </h3>
    <ol class="grid gap-y-2.5 lg:gap-y-1.5">
      {#each nations as row (row.path)}
        {@render band(row)}
      {/each}
    </ol>
  </div>
  {#if below.length > 0}
    <div class="grid gap-1 pbs-2">
      <h3 class="text-xs font-semibold text-muted-foreground">
        {m.home_strata_below()}
      </h3>
      <ol class="grid gap-y-2.5 lg:gap-y-1.5">
        {#each below as row (row.path)}
          {@render band(row)}
        {/each}
      </ol>
    </div>
  {/if}
</div>

<style>
  .sweep {
    animation: strata-sweep 1600ms cubic-bezier(0.45, 0.1, 0.25, 1) both;
  }

  @keyframes strata-sweep {
    from {
      clip-path: inset(0 100% 0 0);
    }
    to {
      clip-path: inset(0 0 0 0);
    }
  }

  @media (prefers-reduced-motion: reduce) {
    .sweep {
      animation: none;
    }
  }
</style>
