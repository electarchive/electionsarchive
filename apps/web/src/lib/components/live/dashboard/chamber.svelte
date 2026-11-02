<script lang="ts">
  import type { Snippet } from "svelte";
  import type { Point } from "#lib/live/hover.js";
  import type { ChamberView, Family, Seat } from "#lib/live/nation.js";
  import type { Summary } from "#lib/live/summary.js";
  import { numbers, percent } from "#lib/format.js";
  import { pointAt } from "#lib/live/hover.js";
  import { odds } from "#lib/live/nation.js";
  import { isNarrowLayout } from "#lib/narrow-layout.svelte.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";
  import Card from "./card.svelte";
  import Sheet from "./sheet.svelte";

  const {
    view,
    sides,
    title,
    forecasts,
    label,
    name,
    href,
    shade,
    detail,
    line,
  }: {
    view: ChamberView;
    sides: [Family, Family];
    title: string;
    forecasts: boolean;
    label: (seat: Seat) => string;
    name: (seat: Seat) => string;
    href: (seat: Seat) => string;
    shade: (seat: Seat) => string;
    detail: Snippet<[Seat]>;
    line: Snippet<[Summary | undefined]>;
  } = $props();

  let point = $state<Point | null>(null);
  let isOpen = $state(false);
  const narrow = $derived(isNarrowLayout());

  const left = $derived(sides[0]);
  const right = $derived(sides[1]);
  const OTHER =
    "color-mix(in oklab, var(--muted-foreground) 55%, var(--background))";

  const others = $derived({
    id: "other",
    name: m.night_chamber_others(),
    short: "",
    color: OTHER,
  });

  type Cell =
    | { kind: "hold"; family: Family; count: number; key: string }
    | { kind: "race"; seat: Seat; key: string };

  const cells = $derived.by(() => {
    const found: Array<Cell> = [];
    const hold = (count: number, family: Family, prefix: string) => {
      for (let index = 0; index < count; index += 1) {
        found.push({ kind: "hold", family, count, key: `${prefix}${index}` });
      }
    };
    hold(view.tallies.left.holdover, left, "l");
    const leaning = view.order.filter((seat) => seat.lean >= 0);
    const trailing = view.order.filter((seat) => seat.lean < 0);
    for (const seat of leaning) {
      found.push({ kind: "race", seat, key: seat.summary.race.id });
    }
    hold(view.tallies.other.holdover, others, "o");
    for (const seat of trailing) {
      found.push({ kind: "race", seat, key: seat.summary.race.id });
    }
    hold(view.tallies.right.holdover, right, "r");
    return found;
  });

  const rows = $derived(view.size > 200 ? 5 : view.size > 60 ? 2 : 1);
  const columns = $derived(Math.ceil(cells.length / rows));

  const leftSeats = $derived(
    view.tallies.left.won +
      view.tallies.left.ahead +
      view.tallies.left.holdover,
  );
  const rightSeats = $derived(
    view.tallies.right.won +
      view.tallies.right.ahead +
      view.tallies.right.holdover,
  );
  const net = $derived(view.tallies.left.gains - view.tallies.left.losses);
  const families = $derived([
    { family: left, tally: view.tallies.left },
    { family: right, tally: view.tallies.right },
    ...(view.tallies.other.won +
      view.tallies.other.ahead +
      view.tallies.other.holdover >
    0
      ? [{ family: others, tally: view.tallies.other }]
      : []),
  ]);

  const span = $derived.by(() => {
    let low = view.pmf.length - 1;
    let high = 0;
    for (const [index, value] of view.pmf.entries()) {
      if (value > 0.0005) {
        low = Math.min(low, index);
        high = Math.max(high, index);
      }
    }
    const pad = Math.max(2, Math.round((high - low) * 0.15));
    return {
      low: Math.max(0, Math.min(low, view.majority - 1) - pad),
      high: Math.min(view.size, Math.max(high, view.majority) + pad),
    };
  });
  const peak = $derived(
    Math.max(...view.pmf.slice(span.low, span.high + 1), 0.0001),
  );
  const isInformed = $derived(
    view.order.some(
      (seat) => seat.rating !== "counting" && seat.rating !== "waiting",
    ) && !view.order.every((seat) => seat.rating === "won"),
  );

  function signed(value: number) {
    return value > 0 ? `+${value}` : value < 0 ? `−${Math.abs(value)}` : "0";
  }
</script>

{#snippet grid(isLinked: boolean)}
  <div
    class="grid gap-px"
    style:grid-template-rows="repeat({rows}, minmax(0, 1fr))"
    style:grid-template-columns="repeat({columns}, minmax(0, 1fr))"
    style:grid-auto-flow="column"
  >
    {#each cells as cell, index (cell.key)}
      {#if cell.kind === "hold"}
        <span
          data-cell={index}
          class="rounded-[1px] block-3.5"
          style:background-image="repeating-linear-gradient(135deg, {cell.family
            .color}
          0 1.5px, color-mix(in oklab, {cell.family.color} 30%, var(--background))
          1.5px 3px)"
          aria-hidden="true"
        ></span>
      {:else if isLinked}
        <a
          href={href(cell.seat)}
          data-cell={index}
          class="rounded-[1px] block-3.5 hover:outline-1 hover:outline-offset-1 hover:outline-foreground focus-visible:outline-2 focus-visible:outline-foreground"
          style:background-color={shade(cell.seat)}
          aria-label={label(cell.seat)}
        ></a>
      {:else}
        <span
          class="rounded-[1px] block-3.5"
          style:background-color={shade(cell.seat)}
        ></span>
      {/if}
    {/each}
  </div>
  <span
    class="pointer-events-none absolute -inset-y-1 inset-s-1/2 border-s-2 border-foreground"
    aria-hidden="true"
  ></span>
{/snippet}

{#snippet legend()}
  <dl class="grid grid-cols-[1fr_auto_auto] gap-x-4 gap-y-0.5 text-xs">
    {#each families as row (row.family.id)}
      <dt class="flex items-center gap-1.5">
        <span
          class="rounded-full block-2 inline-2"
          style:background-color={row.family.color}
        ></span>{row.family.name}
      </dt>
      <dd class="font-figures text-muted-foreground tabular-nums">
        {m.night_chamber_breakdown({
          won: row.tally.won,
          ahead: row.tally.ahead,
          holdover: row.tally.holdover,
        })}
      </dd>
      <dd
        class={cn("text-end font-figures font-semibold tabular-nums", {
          "text-muted-foreground": row.tally.gains === row.tally.losses,
        })}
      >
        {signed(row.tally.gains - row.tally.losses)}
      </dd>
    {/each}
  </dl>
{/snippet}

<section class="grid content-start gap-3 bg-background px-4 py-5 sm:px-6">
  <header class="flex items-baseline justify-between gap-3">
    <h2 class="heading text-lg!">{title}</h2>
    <p class="text-xs text-muted-foreground">
      {m.night_chamber_majority({ count: view.majority })}
    </p>
  </header>

  <div class="flex items-end justify-between gap-3">
    <p class="grid">
      <span class="text-xs font-semibold" style:color={left.color}
        >{left.short}</span
      >
      <span
        class="font-figures text-4xl leading-none font-[780] tabular-nums"
        style:color={left.color}>{leftSeats}</span
      >
    </p>
    <p class="grid justify-items-center pbe-0.5 text-center">
      {#if view.waiting > 0}
        <span
          class="font-figures text-lg leading-none font-semibold tabular-nums"
          >{view.waiting}</span
        >
        <span class="text-xs text-muted-foreground"
          >{m.night_nation_waiting()}</span
        >
      {/if}
    </p>
    <p class="grid justify-items-end">
      <span class="text-xs font-semibold" style:color={right.color}
        >{right.short}</span
      >
      <span
        class="font-figures text-4xl leading-none font-[780] tabular-nums"
        style:color={right.color}>{rightSeats}</span
      >
    </p>
  </div>

  {#if narrow}
    <button
      type="button"
      class="relative grid gap-2 text-start"
      aria-label={m.night_chamber_open({ title })}
      onclick={() => {
        isOpen = true;
      }}
    >
      <span class="relative grid">{@render grid(false)}</span>
      <span class="text-xs text-muted-foreground">{m.night_chamber_tap()}</span>
    </button>
  {:else}
    <div
      class="relative"
      role="presentation"
      onpointermove={(event) => {
        point = pointAt(event);
      }}
      onpointerleave={() => {
        point = null;
      }}
    >
      {@render grid(true)}
      {#if point != null}
        {const cell = $derived(cells[point.index])}
        {#if cell != null}
          <Card {point}>
            {#if cell.kind === "race"}
              {@render detail(cell.seat)}
            {:else}
              <span class="text-sm font-semibold">{cell.family.name}</span>
              <span class="text-muted-foreground"
                >{m.night_chamber_not_up({ count: cell.count })}</span
              >
            {/if}
          </Card>
        {/if}
      {/if}
    </div>
  {/if}

  {@render legend()}

  {#if forecasts && isInformed}
    <div class="grid gap-1.5 border-bs border-border pbs-3">
      <div
        class="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5 text-xs"
      >
        <span class="font-semibold">{m.night_chamber_odds()}</span>
        <span class="flex gap-3 font-figures whitespace-nowrap tabular-nums">
          <span style:color={left.color}
            >{left.short} {odds(view.control.left)}</span
          >
          <span style:color={right.color}
            >{right.short} {odds(view.control.right)}</span
          >
          {#if view.control.none >= 0.005}
            <span class="text-muted-foreground"
              >{m.night_chamber_split({ share: odds(view.control.none) })}</span
            >
          {/if}
        </span>
      </div>
      <svg
        viewBox="{span.low - 0.5} 0 {span.high - span.low + 1} 40"
        preserveAspectRatio="none"
        class="overflow-visible block-14 inline-full"
        role="img"
        aria-label={m.night_chamber_histogram({ party: left.name })}
      >
        {#each { length: span.high - span.low + 1 } as _, offset (offset)}
          {const count = $derived(span.low + offset)}
          {const value = $derived(view.pmf[count] ?? 0)}
          <rect
            x={count - 0.42}
            y={40 - (value / peak) * 38}
            width="0.84"
            height={(value / peak) * 38}
            fill={count >= view.majority
              ? left.color
              : view.size - count >= view.majority
                ? right.color
                : "var(--muted-foreground)"}
            opacity={count >= view.range[0] && count <= view.range[1] ? 1 : 0.4}
          >
            <title>{left.short} {count}: {percent(value, 1)}</title>
          </rect>
        {/each}
        <line
          x1={view.majority - 0.5}
          x2={view.majority - 0.5}
          y1="-2"
          y2="40"
          stroke="var(--foreground)"
          stroke-width="1.5"
          vector-effect="non-scaling-stroke"
        />
      </svg>
      <p
        class="flex justify-between font-figures text-xs text-muted-foreground tabular-nums"
      >
        <span>{span.low}</span>
        <span
          >{m.night_chamber_expected({
            party: left.short.slice(0, 1),
            seats: numbers({ maximumFractionDigits: 1 }).format(
              view.expected.left,
            ),
            low: view.range[0],
            high: view.range[1],
          })}</span
        >
        <span>{span.high}</span>
      </p>
    </div>
  {/if}
  <p class="text-xs text-muted-foreground">
    {net === 0
      ? m.night_chamber_net_none()
      : m.night_chamber_net({
          party: net > 0 ? left.name : right.name,
          count: Math.abs(net),
        })}
  </p>
</section>

{#if narrow}
  <Sheet
    bind:isOpen
    {title}
    note={m.night_chamber_majority({ count: view.majority })}
    seats={view.order}
    {name}
    {href}
    {shade}
    {line}
    summary={legend}
  />
{/if}
