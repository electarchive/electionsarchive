<script lang="ts">
  import type { LiveElection } from "@ea/analytics/live";
  import ArrowLeftIcon from "@lucide/svelte/icons/arrow-left";
  import ArrowUpRightIcon from "@lucide/svelte/icons/arrow-up-right";
  import ChevronRightIcon from "@lucide/svelte/icons/chevron-right";
  import MapPinnedIcon from "@lucide/svelte/icons/map-pinned";
  import type { Control } from "#lib/live/nation.js";
  import type { Summary } from "#lib/live/summary.js";
  import Flip from "#lib/components/live/dashboard/flip.svelte";
  import { numbers, percent } from "#lib/format.js";
  import { DOTS } from "#lib/live/format.js";
  import { liveLinks } from "#lib/live/links.js";
  import {
    allied,
    breakdown,
    caucusNote,
    isSettled,
    odds,
    seatsOf,
  } from "#lib/live/nation.js";
  import { closing, verdict } from "#lib/live/summary.js";
  import { m } from "#lib/paraglide/messages.js";
  import { marginShade, safety } from "#lib/shade.js";
  import { cn } from "#lib/utils.js";
  import type { Board, Office } from "./model.js";
  import {
    districtKey,
    flipped,
    holding,
    seatHatch,
    seatName,
    shade,
    urgency,
    WAITING,
  } from "./model.js";
  import RaceCard from "./race-card.svelte";

  const {
    election,
    found,
    office,
    paint,
    control,
    states,
    picked,
    clock,
    forecasts,
    onPick,
    onOpen,
    onHover,
  }: {
    election: LiveElection;
    found: Board;
    office: Office;
    paint: "lead" | "chance" | "counted";
    control: Control | null;
    states: (code: string) => string;
    picked: string | null;
    clock: number;
    forecasts: boolean;
    onPick: (key: string | null) => void;
    onOpen: (code: string) => void;
    onHover: (key: string | null) => void;
  } = $props();

  const toLive = liveLinks();
  const country = $derived(election.slug.split("/")[0] ?? "us");
  const date = $derived(election.slug.split("/")[1] ?? "");
  const TITLES: Record<Office, () => string> = {
    senate: m.night_nation_senate,
    governor: m.night_nation_governors,
    house: m.night_nation_house,
  };

  const view = $derived(found.chambers?.[office] ?? null);
  const pair = $derived(found.pair);
  const races = $derived(
    found.summaries.filter((summary) => summary.race.office === office),
  );
  const ordered = $derived(
    races
      .map((summary) => ({
        summary,
        rank: urgency(summary, closing(election, summary.race)),
        label: seatName(summary.race, states),
        key: keyOf(summary),
      }))
      .toSorted(
        (a, b) =>
          (a.rank[0] ?? 0) - (b.rank[0] ?? 0) ||
          (a.rank[1] ?? 0) - (b.rank[1] ?? 0) ||
          a.label.localeCompare(b.label, undefined, { numeric: true }),
      ),
  );
  const isCounting = $derived(races.some((summary) => summary.total > 0));
  const isInformed = $derived(
    view != null &&
      view.order.some(
        (seat) =>
          seat.rating !== "counting" &&
          seat.rating !== "waiting" &&
          seat.rating !== "later",
      ) &&
      !view.order.every((seat) => isSettled(seat.rating)),
  );
  const span = $derived.by(() => {
    if (view == null) {
      return { low: 0, high: 0 };
    }
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
    Math.max(...(view?.pmf.slice(span.low, span.high + 1) ?? []), 0.0001),
  );

  function chanceOf(summary: Summary) {
    const seat = found.seats.get(summary.race.id);
    if (
      !forecasts ||
      pair == null ||
      seat == null ||
      seat.rating === "waiting" ||
      seat.rating === "counting" ||
      seat.rating === "later" ||
      isSettled(seat.rating)
    ) {
      return null;
    }
    const isLeft = seat.left >= seat.right;
    return {
      value: odds(isLeft ? seat.left : seat.right),
      color: pair[isLeft ? 0 : 1].color,
    };
  }

  const code = $derived(picked == null ? null : (picked.split("-")[0] ?? null));
  const shown = $derived(
    picked == null
      ? []
      : races.filter((summary) =>
          picked.includes("-")
            ? districtKey(summary.race) === picked
            : summary.race.state === picked,
        ),
  );
  const isDistrictList = $derived(
    picked != null && !picked.includes("-") && office === "house",
  );

  function keyOf(summary: Summary) {
    return office === "house"
      ? (districtKey(summary.race) ?? summary.race.state)
      : summary.race.state;
  }

  function color(summary: Summary) {
    return shade(
      summary,
      found.seats.get(summary.race.id),
      paint,
      pair,
      control,
    );
  }

  const bar = $derived.by(() => {
    if (view == null || pair == null) {
      return null;
    }
    const { left, right, other } = view.tallies;
    const size = view.size;
    let leftAllies = 0;
    for (const bloc of allied(view.blocs, "left")) {
      leftAllies += bloc.tally.holdover;
    }
    let rightAllies = 0;
    for (const bloc of allied(view.blocs, "right")) {
      rightAllies += bloc.tally.holdover;
    }
    const parts = [
      {
        key: "lw",
        count: left.won + left.projected + left.unopposed,
        color: pair[0].color,
      },
      {
        key: "la",
        count: left.ahead,
        color: marginShade(pair[0].color, safety(0.55)),
      },
      {
        key: "lh",
        count: left.holdover - leftAllies,
        color: marginShade(pair[0].color, safety(0.3)),
        isHeld: true,
      },
      { key: "lc", count: leftAllies, color: pair[0].color, isAlly: true },
      {
        key: "o",
        count: seatsOf(other),
        color: marginShade("var(--muted-foreground)", safety(0.55)),
      },
      {
        key: "t",
        count: view.tossups,
        color: marginShade("var(--muted-foreground)", safety(0.3)),
      },
      { key: "w", count: view.waiting + view.later, color: WAITING },
      { key: "rc", count: rightAllies, color: pair[1].color, isAlly: true },
      {
        key: "rh",
        count: right.holdover - rightAllies,
        color: marginShade(pair[1].color, safety(0.3)),
        isHeld: true,
      },
      {
        key: "ra",
        count: right.ahead,
        color: marginShade(pair[1].color, safety(0.55)),
      },
      {
        key: "rw",
        count: right.won + right.projected + right.unopposed,
        color: pair[1].color,
      },
    ];
    return {
      parts: parts
        .filter((part) => part.count > 0)
        .map((part) => ({ ...part, share: part.count / size })),
      left: seatsOf(left),
      right: seatsOf(right),
      majority: (view.needs.left - 0.5) / size,
      heads: [0, 1].map((index) => {
        const side = index === 0 ? "left" : "right";
        const family = pair[index] ?? pair[0];
        return {
          label:
            allied(view.blocs, side).length > 0
              ? m.night_chamber_caucus({ party: family.short })
              : family.short,
          note: caucusNote(view.blocs, side),
        };
      }),
    };
  });
</script>

{#snippet row(entry: (typeof ordered)[number], isNested: boolean)}
  {const summary = $derived(entry.summary)}
  {const seat = $derived(found.seats.get(summary.race.id))}
  {const flip = $derived(flipped(seat, pair, control))}
  <li>
    <button
      type="button"
      class={cn(
        "-mx-2 grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-x-2.5 rounded-sm px-2 py-1.5 text-start inline-[calc(100%+1rem)] hover:bg-secondary focus-visible:bg-secondary",
        { "bg-secondary": picked === entry.key && !isNested },
      )}
      onclick={() => onPick(entry.key)}
      onpointerenter={() => onHover(entry.key)}
      onpointerleave={() => onHover(null)}
      onfocus={() => onHover(entry.key)}
      onblur={() => onHover(null)}
    >
      <span
        class="rounded-xs ring-1 ring-border/60 block-3.5 inline-3.5"
        style:background-color={color(summary)}
        style:background-image={summary.later == null
          ? seatHatch(summary, seat, paint, pair, control)?.stripes
          : DOTS}
      ></span>
      <span class="grid min-inline-0">
        <span class="flex items-center gap-1.5 min-inline-0">
          <span class="truncate text-[0.8125rem] font-semibold"
            >{entry.label}</span
          >
          {#if flip != null}
            <Flip flip={flip.flip} from={flip.from} to={flip.to} />
          {/if}
        </span>
        <span class="truncate text-xs text-muted-foreground"
          >{verdict(summary)}</span
        >
      </span>
      {const chance = $derived(chanceOf(summary))}
      <span
        class="grid justify-items-end font-figures text-xs text-muted-foreground tabular-nums"
      >
        {#if chance != null}
          <span
            class="font-semibold"
            style:color={chance.color}
            title={m.livemap_win_chance()}>{chance.value}</span
          >
        {/if}
        {#if summary.counted != null && summary.total > 0}
          <span title={m.livemap_counted_share()}
            >{percent(summary.counted, 0)}</span
          >
        {/if}
      </span>
    </button>
  </li>
{/snippet}

<div class="grid gap-5">
  {#if bar != null && pair != null && view != null}
    <section class="grid gap-2" aria-label={TITLES[office]()}>
      <div class="flex items-end justify-between gap-3">
        <span class="grid">
          <span class="text-xs font-semibold" style:color={pair[0].color}
            >{bar.heads[0]?.label}</span
          >
          <span class="font-figures text-[1.75rem]/8 font-[760] tabular-nums"
            >{bar.left}</span
          >
          {#if bar.heads[0]?.note != null}
            <span class="text-[0.6875rem]/4 text-muted-foreground"
              >{bar.heads[0].note}</span
            >
          {/if}
        </span>
        {#if office !== "governor"}
          <span class="grid pbe-1 text-center text-xs text-muted-foreground">
            {#if view.tossups > 0}
              <span
                ><span
                  class="font-figures font-semibold text-foreground tabular-nums"
                  >{view.tossups}</span
                >
                {m.night_ladder_tossup()}</span
              >
            {/if}
            <span>{m.night_chamber_majority({ count: view.majority })}</span>
          </span>
        {/if}
        <span class="grid justify-items-end text-end">
          <span class="text-xs font-semibold" style:color={pair[1].color}
            >{bar.heads[1]?.label}</span
          >
          <span class="font-figures text-[1.75rem]/8 font-[760] tabular-nums"
            >{bar.right}</span
          >
          {#if bar.heads[1]?.note != null}
            <span class="text-[0.6875rem]/4 text-muted-foreground"
              >{bar.heads[1].note}</span
            >
          {/if}
        </span>
      </div>
      <div class="relative">
        <div
          class="flex overflow-hidden rounded-xs bg-muted block-3.5"
          aria-hidden="true"
        >
          {#each bar.parts as part (part.key)}
            <span
              class="transition-[flex-basis] duration-700 ease-out motion-reduce:transition-none"
              style:flex-basis="{part.share * 100}%"
              style:background-color={part.isAlly === true
                ? "color-mix(in oklab, var(--muted-foreground) 22%, var(--background))"
                : part.isHeld === true
                  ? `color-mix(in oklab, ${part.color} 35%, var(--background))`
                  : part.color}
              style:box-shadow={part.isAlly === true
                ? `inset 0 0 0 1.5px ${part.color}`
                : undefined}
            ></span>
          {/each}
        </div>
        {#if office !== "governor"}
          <span
            class="absolute -inset-y-1 -translate-x-1/2 rounded-full bg-foreground inline-0.5"
            style:inset-inline-start="{bar.majority * 100}%"
            aria-hidden="true"
          ></span>
        {/if}
      </div>
      <div
        class="flex justify-between gap-3 text-[0.6875rem]/4 text-muted-foreground"
      >
        <span>{breakdown(view.tallies.left)}</span>
        <span class="text-end">{breakdown(view.tallies.right)}</span>
      </div>
      {#if view.tiebreak != null}
        <p class="text-[0.6875rem]/4 text-muted-foreground">
          {m.night_chamber_tiebreak({
            count: view.size / 2,
            party: (view.tiebreak === "left" ? pair[0] : pair[1]).name,
          })}
        </p>
      {:else if view.isTiebreakPending}
        <p class="text-[0.6875rem]/4 text-muted-foreground">
          {m.night_chamber_tiebreak_pending({ count: view.size / 2 })}
        </p>
      {/if}
      {#if forecasts && isInformed && office !== "governor"}
        <div class="grid gap-1.5 border-bs border-border pbs-3">
          <div
            class="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5 text-xs"
          >
            <span class="font-semibold">{m.night_chamber_odds()}</span>
            <span
              class="flex gap-3 font-figures font-semibold whitespace-nowrap tabular-nums"
            >
              <span style:color={pair[0].color}
                >{pair[0].short} {odds(view.control.left)}</span
              >
              <span style:color={pair[1].color}
                >{pair[1].short} {odds(view.control.right)}</span
              >
              {#if view.control.none >= 0.005}
                <span class="font-normal text-muted-foreground"
                  >{m.night_chamber_split({
                    share: odds(view.control.none),
                  })}</span
                >
              {/if}
            </span>
          </div>
          <svg
            viewBox="{span.low - 0.5} 0 {span.high - span.low + 1} 40"
            preserveAspectRatio="none"
            class="overflow-visible block-12 inline-full"
            role="img"
            aria-label={m.night_chamber_histogram({ party: pair[0].name })}
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
                  ? pair[0].color
                  : view.size - count >= view.needs.right
                    ? pair[1].color
                    : "var(--muted-foreground)"}
                opacity={count >= view.range[0] && count <= view.range[1]
                  ? 1
                  : 0.4}
              >
                <title>{pair[0].short} {count}: {percent(value, 1)}</title>
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
            class="flex justify-between gap-2 font-figures text-[0.6875rem]/4 text-muted-foreground tabular-nums"
          >
            <span>{span.low}</span>
            <span class="text-center"
              >{m.night_chamber_expected({
                party: pair[0].short.slice(0, 1),
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
      {:else if forecasts && isCounting && office !== "governor"}
        <p class="flex justify-between gap-3 text-xs font-semibold">
          <span
            >{m.night_nation_chance({
              name: pair[0].short,
              chance: odds(view.control.left),
            })}</span
          >
          <span class="text-end"
            >{m.night_nation_chance({
              name: pair[1].short,
              chance: odds(view.control.right),
            })}</span
          >
        </p>
      {/if}
    </section>
  {/if}

  {#if picked != null && code != null}
    <section class="grid gap-4">
      <header class="flex items-center justify-between gap-2">
        <button
          type="button"
          class="-ms-2 flex items-center gap-1.5 rounded-sm px-2 text-[0.8125rem] text-muted-foreground block-8 hover:bg-secondary hover:text-foreground"
          onclick={() => onPick(null)}
          ><ArrowLeftIcon
            class="block-3.5 inline-3.5"
            aria-hidden="true"
          />{m.livemap_all()}</button
        >
      </header>
      <h2 class="text-xl/6 font-[760] tracking-tight">
        {picked.includes("-") && shown[0] != null
          ? seatName(shown[0].race, states)
          : states(code)}
      </h2>
      {#if isDistrictList}
        <ul class="grid">
          {#each ordered.filter((entry) => entry.summary.race.state === code) as entry (entry.summary.race.id)}
            {@render row(entry, true)}
          {/each}
        </ul>
      {:else if shown.length === 0}
        <p class="text-sm text-muted-foreground">{m.night_nation_none()}</p>
      {:else}
        {#each shown as summary (summary.race.id)}
          {const seat = $derived(found.seats.get(summary.race.id))}
          <RaceCard
            {summary}
            held={holding(control, summary.race)}
            flip={flipped(seat, pair, control)}
            closes={closing(election, summary.race)}
            {clock}
            {forecasts}
          />
        {/each}
      {/if}
      <div class="grid gap-2">
        <button
          type="button"
          class="grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-2.5 rounded-sm bg-foreground px-3 py-2.5 text-start text-sm/snug font-semibold text-background min-block-10 hover:bg-foreground/90"
          onclick={() => onOpen(code)}
          ><MapPinnedIcon class="block-4 inline-4" aria-hidden="true" /><span
            class="text-balance"
            >{m.livemap_drill({ place: states(code) })}</span
          ><ChevronRightIcon
            class="opacity-70 block-4 inline-4"
            aria-hidden="true"
          /></button
        >
        <a
          href={toLive(`${country}/${code}/${date}`)}
          class="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-2.5 rounded-sm px-3 py-2 text-[0.8125rem]/snug font-semibold ring-1 ring-input min-block-9 hover:bg-secondary"
          ><span class="text-balance"
            >{m.night_map_open({ place: states(code) })}</span
          ><ArrowUpRightIcon
            class="text-muted-foreground block-3.5 inline-3.5"
            aria-hidden="true"
          /></a
        >
      </div>
    </section>
  {:else}
    <section class="grid gap-1.5">
      <h2 class="text-xs font-semibold text-muted-foreground">
        {isCounting ? m.night_nation_closest() : m.livemap_races()}
      </h2>
      <ul class="grid">
        {#each ordered as entry (entry.summary.race.id)}
          {@render row(entry, false)}
        {/each}
      </ul>
    </section>
  {/if}
</div>
