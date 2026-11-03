<script lang="ts">
  import type { LiveElection } from "@ea/analytics/live";
  import ArrowLeftIcon from "@lucide/svelte/icons/arrow-left";
  import ArrowUpRightIcon from "@lucide/svelte/icons/arrow-up-right";
  import MapPinnedIcon from "@lucide/svelte/icons/map-pinned";
  import type { Control } from "#lib/live/nation.js";
  import type { Summary } from "#lib/live/summary.js";
  import Flip from "#lib/components/live/dashboard/flip.svelte";
  import { percent } from "#lib/format.js";
  import { HATCH } from "#lib/live/format.js";
  import { liveLinks } from "#lib/live/links.js";
  import { breakdown, odds } from "#lib/live/nation.js";
  import { closing, verdict } from "#lib/live/summary.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";
  import type { Board, Office } from "./model.js";
  import {
    districtKey,
    flipped,
    holding,
    mix,
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
    paint: "lead" | "chance" | "flips" | "counted";
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
    const parts = [
      { key: "lw", count: left.won + left.projected, color: pair[0].color },
      { key: "la", count: left.ahead, color: mix(pair[0].color, 55) },
      {
        key: "lh",
        count: left.holdover,
        color: mix(pair[0].color, 30),
        isHeld: true,
      },
      {
        key: "o",
        count: other.won + other.projected + other.ahead + other.holdover,
        color: mix("var(--muted-foreground)", 55),
      },
      { key: "w", count: view.waiting + view.later, color: WAITING },
      {
        key: "rh",
        count: right.holdover,
        color: mix(pair[1].color, 30),
        isHeld: true,
      },
      { key: "ra", count: right.ahead, color: mix(pair[1].color, 55) },
      { key: "rw", count: right.won + right.projected, color: pair[1].color },
    ];
    return {
      parts: parts
        .filter((part) => part.count > 0)
        .map((part) => ({ ...part, share: part.count / size })),
      left: left.won + left.projected + left.ahead + left.holdover,
      right: right.won + right.projected + right.ahead + right.holdover,
      majority: view.majority / size,
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
        style:background-image={summary.later == null ? undefined : HATCH}
      ></span>
      <span class="grid min-inline-0">
        <span class="flex items-center gap-1.5 min-inline-0">
          <span class="truncate text-[0.8125rem] font-semibold"
            >{entry.label}</span
          >
          {#if flip != null}
            <Flip party={flip.party} color={flip.color} />
          {/if}
        </span>
        <span class="truncate text-xs text-muted-foreground"
          >{verdict(summary)}</span
        >
      </span>
      <span class="font-figures text-xs text-muted-foreground tabular-nums">
        {#if summary.counted != null && summary.total > 0}
          {percent(summary.counted, 0)}
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
            >{pair[0].short}</span
          >
          <span class="font-figures text-[1.75rem]/8 font-[760] tabular-nums"
            >{bar.left}</span
          >
        </span>
        {#if office !== "governor"}
          <span class="pbe-1 text-center text-xs text-muted-foreground"
            >{m.night_chamber_majority({ count: view.majority })}</span
          >
        {/if}
        <span class="grid justify-items-end">
          <span class="text-xs font-semibold" style:color={pair[1].color}
            >{pair[1].short}</span
          >
          <span class="font-figures text-[1.75rem]/8 font-[760] tabular-nums"
            >{bar.right}</span
          >
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
              style:background-color={part.color}
              style:background-image={part.isHeld === true ? HATCH : undefined}
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
      {#if forecasts && isCounting && office !== "governor"}
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
          class="flex items-center justify-center gap-2 rounded-sm bg-foreground px-3 text-sm font-semibold text-background block-10 hover:bg-foreground/90"
          onclick={() => onOpen(code)}
          ><MapPinnedIcon
            class="block-4 inline-4"
            aria-hidden="true"
          />{m.livemap_drill({ place: states(code) })}</button
        >
        <a
          href={toLive(`${country}/${code}/${date}`)}
          class="flex items-center justify-center gap-1.5 rounded-sm px-3 text-[0.8125rem] font-semibold ring-1 ring-input block-9 hover:bg-secondary"
          >{m.night_map_open({ place: states(code) })}<ArrowUpRightIcon
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
