<script lang="ts">
  import type { Snippet } from "svelte";
  import type { Point } from "#lib/live/hover.js";
  import type { ChamberView, Family, Seat } from "#lib/live/nation.js";
  import type { Summary } from "#lib/live/summary.js";
  import { numbers, percent } from "#lib/format.js";
  import { hatch, ink, swing } from "#lib/live/flips.js";
  import { pointAt } from "#lib/live/hover.js";
  import {
    allied,
    blocName,
    breakdown,
    caucusNote,
    isSettled,
    lane,
    odds,
    seatsOf,
  } from "#lib/live/nation.js";
  import { isNarrowLayout } from "#lib/narrow-layout.svelte.js";
  import { m } from "#lib/paraglide/messages.js";
  import { getLocale } from "#lib/paraglide/runtime.js";
  import { cn } from "#lib/utils.js";
  import Called from "./called.svelte";
  import Card from "./card.svelte";
  import SeatKey from "./seat-key.svelte";
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
    | {
        kind: "hold";
        family: Family;
        count: number;
        key: string;
        ally: { name: string; names: Array<string> } | null;
      }
    | { kind: "race"; seat: Seat; key: string };

  const cells = $derived.by(() => {
    const found: Array<Cell> = [];
    const hold = (
      count: number,
      family: Family,
      prefix: string,
      ally: { name: string; names: Array<string> } | null = null,
    ) => {
      for (let index = 0; index < count; index += 1) {
        found.push({
          kind: "hold",
          family,
          count,
          key: `${prefix}${index}`,
          ally,
        });
      }
    };
    const allies = (side: "left" | "right", family: Family) =>
      allied(view.blocs, side).map((bloc, index) => ({
        count: bloc.tally.holdover,
        family,
        prefix: `${side}${index}-`,
        ally: { name: blocName(bloc, family), names: bloc.names },
      }));
    const leftAllies = allies("left", left);
    const rightAllies = allies("right", right);
    let leftOwn = view.tallies.left.holdover;
    for (const entry of leftAllies) {
      leftOwn -= entry.count;
    }
    let rightOwn = view.tallies.right.holdover;
    for (const entry of rightAllies) {
      rightOwn -= entry.count;
    }
    hold(leftOwn, left, "l");
    for (const entry of leftAllies) {
      hold(entry.count, entry.family, entry.prefix, entry.ally);
    }
    const leaning = view.order.filter((seat) => seat.lean >= 0);
    const trailing = view.order.filter((seat) => seat.lean < 0);
    for (const seat of leaning) {
      found.push({ kind: "race", seat, key: seat.summary.race.id });
    }
    hold(view.tallies.other.holdover, others, "o");
    for (const seat of trailing) {
      found.push({ kind: "race", seat, key: seat.summary.race.id });
    }
    for (const entry of rightAllies.toReversed()) {
      hold(entry.count, entry.family, entry.prefix, entry.ally);
    }
    hold(rightOwn, right, "r");
    return found;
  });

  const ALLY =
    "light-dark(color-mix(in oklab, var(--muted-foreground) 14%, var(--background)), color-mix(in oklab, var(--muted-foreground) 30%, var(--background)))";

  function ring(color: string) {
    return `inset 0 0 0 1.5px ${color}`;
  }

  const lines = $derived(view.size > 200 ? 5 : view.size > 60 ? 2 : 1);
  const columns = $derived(Math.ceil(cells.length / lines));

  const net = $derived(view.tallies.left.gains - view.tallies.left.losses);
  const changing = $derived(swing(view.order.map((seat) => seat.shift)));
  const families = $derived([
    { family: left, tally: view.tallies.left },
    { family: right, tally: view.tallies.right },
    ...(seatsOf(view.tallies.other) > 0
      ? [{ family: others, tally: view.tallies.other }]
      : []),
  ]);
  const caucuses = $derived(
    [left, right].filter(
      (family, index) =>
        allied(view.blocs, index === 0 ? "left" : "right").length > 0,
    ),
  );
  const headings = $derived(
    [left, right].map((family, index) => {
      const side = index === 0 ? "left" : "right";
      return {
        family,
        label:
          allied(view.blocs, side).length > 0
            ? m.night_chamber_caucus({ party: family.short })
            : family.short,
        note: caucusNote(view.blocs, side),
        seats: seatsOf(view.tallies[side]),
      };
    }),
  );
  const rows = $derived.by(() => {
    const out: Array<{
      key: string;
      name: string;
      names: Array<string>;
      color: string;
      isAllied: boolean;
      text: string;
      net: number | null;
    }> = [];
    for (const [side, family] of [
      ["left", left],
      ["right", right],
    ] as const) {
      const own = view.blocs.filter(
        (bloc) => bloc.side === side && !bloc.isAllied,
      );
      const tally = { ...view.tallies[side] };
      for (const bloc of allied(view.blocs, side)) {
        for (const field of [
          "won",
          "projected",
          "unopposed",
          "ahead",
          "holdover",
        ] as const) {
          tally[field] -= bloc.tally[field];
        }
      }
      out.push({
        key: side,
        name: own[0]?.label ?? family.name,
        names: [],
        color: family.color,
        isAllied: false,
        text: breakdown(tally),
        net: tally.gains - tally.losses,
      });
      for (const [index, bloc] of allied(view.blocs, side).entries()) {
        out.push({
          key: `${side}-ally-${index}`,
          name: blocName(bloc, family),
          names: bloc.names,
          color: family.color,
          isAllied: true,
          text: breakdown(bloc.tally),
          net: null,
        });
      }
    }
    if (seatsOf(view.tallies.other) > 0) {
      out.push({
        key: "other",
        name: others.name,
        names: [],
        color: others.color,
        isAllied: false,
        text: breakdown(view.tallies.other),
        net: view.tallies.other.gains - view.tallies.other.losses,
      });
    }
    return out;
  });
  const tossups = $derived(
    view.order.filter((seat) => lane(seat, sides) === "tossup"),
  );

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
      low: Math.max(0, Math.min(low, view.needs.left - 1) - pad),
      high: Math.min(view.size, Math.max(high, view.needs.left) + pad),
    };
  });
  const peak = $derived(
    Math.max(...view.pmf.slice(span.low, span.high + 1), 0.0001),
  );
  const isInformed = $derived(
    view.order.some(
      (seat) =>
        seat.rating !== "counting" &&
        seat.rating !== "waiting" &&
        seat.rating !== "later",
    ) && !view.order.every((seat) => isSettled(seat.rating)),
  );

  function look(seat: Seat): { fill: string; stripes?: string } {
    const found = seat.shift;
    if (found == null || found.flip === "held") {
      return { fill: shade(seat) };
    }
    return hatch(
      sides.find((family) => family.id === found.to)?.color ?? OTHER,
      found.flip,
    );
  }

  function edge(seat: Seat) {
    return seat.ally == null
      ? undefined
      : ring(sides.find((family) => family.id === seat.side)?.color ?? OTHER);
  }

  function gained(family: Family) {
    return (
      changing.parties.find((party) => party.id === family.id)?.flipped ?? 0
    );
  }

  function list(names: Array<string>) {
    return new Intl.ListFormat(getLocale(), { type: "conjunction" }).format(
      names,
    );
  }

  function signed(value: number) {
    return value > 0 ? `+${value}` : value < 0 ? `−${Math.abs(value)}` : "0";
  }
</script>

{#snippet grid(isLinked: boolean)}
  <div
    class="grid gap-px"
    style:grid-template-rows="repeat({lines}, minmax(0, 1fr))"
    style:grid-template-columns="repeat({columns}, minmax(0, 1fr))"
    style:grid-auto-flow="column"
  >
    {#each cells as cell, index (cell.key)}
      {#if cell.kind === "hold"}
        <span
          data-cell={index}
          class="rounded-[1px] block-3.5"
          style:background-color={cell.ally == null
            ? `light-dark(color-mix(in oklab, ${cell.family.color} 18%, var(--background)), color-mix(in oklab, ${cell.family.color} 38%, var(--background)))`
            : ALLY}
          style:box-shadow={cell.ally == null
            ? undefined
            : ring(cell.family.color)}
          aria-hidden="true"
        ></span>
      {:else if isLinked}
        <a
          href={href(cell.seat)}
          data-cell={index}
          class="grid place-items-center rounded-[1px] block-3.5 hover:outline-1 hover:outline-offset-1 hover:outline-foreground focus-visible:outline-2 focus-visible:outline-foreground"
          style:background-color={look(cell.seat).fill}
          style:background-image={look(cell.seat).stripes}
          style:box-shadow={edge(cell.seat)}
          aria-label={label(cell.seat)}>{@render pip(cell.seat)}</a
        >
      {:else}
        <span
          class="grid place-items-center rounded-[1px] block-3.5"
          style:background-color={look(cell.seat).fill}
          style:background-image={look(cell.seat).stripes}
          style:box-shadow={edge(cell.seat)}>{@render pip(cell.seat)}</span
        >
      {/if}
    {/each}
  </div>
  <span
    class="pointer-events-none absolute -inset-y-1 inset-s-1/2 border-s-2 border-foreground"
    aria-hidden="true"
  ></span>
{/snippet}

{#snippet pip(seat: Seat)}
  {#if seat.rating === "projected"}
    <span
      class="rounded-full block-1 inline-1"
      style:background-color={ink(look(seat).fill)}
    ></span>
  {/if}
{/snippet}

{#snippet legend()}
  <dl class="grid grid-cols-[1fr_auto_auto] gap-x-4 gap-y-1 text-xs">
    {#each rows as row (row.key)}
      <dt class="flex items-start gap-1.5">
        <span
          class="mbs-1 shrink-0 rounded-full block-2 inline-2"
          style:background-color={row.isAllied ? ALLY : row.color}
          style:box-shadow={row.isAllied ? ring(row.color) : undefined}
        ></span>
        <span class="grid">
          <span>{row.name}</span>
          {#if row.names.length > 0}
            <span class="text-muted-foreground">{list(row.names)}</span>
          {/if}
        </span>
      </dt>
      <dd class="font-figures text-muted-foreground tabular-nums">
        {row.text}
      </dd>
      <dd
        class={cn("text-end font-figures font-semibold tabular-nums", {
          "text-muted-foreground": row.net === 0,
        })}
      >
        {row.net == null ? "" : signed(row.net)}
      </dd>
    {/each}
    {#if tossups.length > 0}
      <dt class="flex items-start gap-1.5">
        <span
          class="mbs-1 shrink-0 rounded-full block-2 inline-2"
          style:background-color={OTHER}
        ></span>{m.night_ladder_tossup()}
      </dt>
      <dd class="text-muted-foreground">
        {list(tossups.map((seat) => name(seat)))}
      </dd>
      <dd></dd>
    {/if}
  </dl>
{/snippet}

<section class="grid content-start gap-3 bg-background px-4 py-5 sm:px-6">
  <header class="flex items-baseline justify-between gap-3">
    <h2 class="heading text-lg!">{title}</h2>
    <p class="grid justify-items-end text-end text-xs text-muted-foreground">
      <span>{m.night_chamber_majority({ count: view.majority })}</span>
      {#if view.tiebreak != null}
        {const breaker = $derived(view.tiebreak === "left" ? left : right)}
        <span class="text-pretty"
          >{m.night_chamber_tiebreak({
            count: view.size / 2,
            party: breaker.name,
          })}</span
        >
      {:else if view.isTiebreakPending}
        <span class="text-pretty"
          >{m.night_chamber_tiebreak_pending({ count: view.size / 2 })}</span
        >
      {/if}
    </p>
  </header>

  <div class="grid grid-cols-[1fr_auto_1fr] items-end gap-3">
    {#each headings as heading, index (heading.family.id)}
      <p
        class={cn("grid", {
          "col-start-1 justify-items-start": index === 0,
          "col-start-3 row-start-1 justify-items-end text-end": index === 1,
        })}
      >
        <span class="text-xs font-semibold" style:color={heading.family.color}
          >{heading.label}</span
        >
        <span
          class="font-figures text-4xl leading-none font-[780] tabular-nums"
          style:color={heading.family.color}>{heading.seats}</span
        >
        {#if heading.note != null}
          <span class="mbs-1 text-xs text-muted-foreground">{heading.note}</span
          >
        {/if}
      </p>
    {/each}
    <p
      class="col-start-2 row-start-1 grid justify-items-center gap-1.5 self-center text-center"
    >
      {#each [{ key: "tossup", count: tossups.length, text: m.night_ladder_tossup() }, { key: "waiting", count: view.waiting, text: m.night_nation_waiting() }, { key: "later", count: view.later, text: m.night_later_key() }].filter((entry) => entry.count > 0) as entry, index (entry.key)}
        <span class="grid">
          <span
            class={cn("font-figures leading-none font-semibold tabular-nums", {
              "text-lg": index === 0,
              "text-sm": index > 0,
            })}>{entry.count}</span
          >
          <span class="text-xs text-muted-foreground">{entry.text}</span>
        </span>
      {/each}
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
            {:else if cell.ally != null}
              <span class="text-sm font-semibold">{list(cell.ally.names)}</span>
              <span>{cell.ally.name}</span>
              <span class="text-muted-foreground"
                >{m.night_chamber_not_up({ count: cell.count })}</span
              >
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

  {#if gained(left) + gained(right) > 0}
    <p class="-mbs-1 flex justify-between gap-3 text-xs font-semibold">
      {#each [left, right] as family (family.id)}
        <span style:color={family.color}
          >{gained(family) > 0
            ? m.night_chamber_side_flipped({ count: gained(family) })
            : ""}</span
        >
      {/each}
    </p>
  {/if}
  {@render legend()}
  <SeatKey
    sides={families.map((row) => row.family)}
    {changing}
    {caucuses}
    isProjected={view.order.some((seat) => seat.rating === "projected")}
    notUp={view.tallies.left.holdover +
      view.tallies.right.holdover +
      view.tallies.other.holdover}
  />
  <Called seats={view.order} />

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
            fill={count >= view.needs.left
              ? left.color
              : view.size - count >= view.needs.right
                ? right.color
                : "var(--muted-foreground)"}
            opacity={count >= view.range[0] && count <= view.range[1] ? 1 : 0.4}
          >
            <title>{left.short} {count}: {percent(value, 1)}</title>
          </rect>
        {/each}
        <line
          x1={view.needs.left - 0.5}
          x2={view.needs.left - 0.5}
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
    {look}
    {line}
    summary={legend}
  />
{/if}
