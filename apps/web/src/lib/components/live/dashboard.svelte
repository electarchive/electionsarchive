<script lang="ts">
  import type {
    LiveElection,
    LiveEstimate,
    LiveOffice,
  } from "@ea/analytics/live";
  import type { Part } from "svelte-islands";
  import { goto } from "$app/navigation";
  import { resolve } from "$app/paths";
  import HexagonIcon from "@lucide/svelte/icons/hexagon";
  import MapIcon from "@lucide/svelte/icons/map";
  import type { SegmentedOption } from "#lib/components/ui/segmented/index.js";
  import type { Chamber, Control, Seat, Trail } from "#lib/live/nation.js";
  import type { Geography } from "#lib/live/standing.js";
  import type { Summary } from "#lib/live/summary.js";
  import type { House } from "#lib/server/live-board.js";
  import Lazy from "#lib/components/lazy.svelte";
  import ViewLinks from "#lib/components/live/view-links.svelte";
  import { Segmented } from "#lib/components/ui/segmented/index.js";
  import { isHover } from "#lib/explain.svelte.js";
  import { fits } from "#lib/fits.js";
  import { dates, numbers, percent } from "#lib/format.js";
  import { hatch, swing } from "#lib/live/flips.js";
  import {
    clock as clockOf,
    compact,
    lead,
    stamp,
    surname,
    tone,
    zoneName,
  } from "#lib/live/format.js";
  import { provideReach, reach } from "#lib/live/lineup.js";
  import { liveLinks } from "#lib/live/links.js";
  import {
    chamber,
    isSettled,
    pulse,
    seat as seatOf,
    sides as sidesOf,
    STATES,
  } from "#lib/live/nation.js";
  import {
    closing,
    edge,
    isUncounted,
    party,
    summarize,
    tally,
  } from "#lib/live/summary.js";
  import { ZONES } from "#lib/live/zones.js";
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";
  import { leadSafety, marginShade, safety } from "#lib/shade.js";
  import { cn } from "#lib/utils.js";
  import { Windowed } from "#lib/windowed.svelte.js";
  import Calls from "./dashboard/calls.svelte";
  import Chambers from "./dashboard/chamber.svelte";
  import Explorer from "./dashboard/explorer.svelte";
  import Flip from "./dashboard/flip.svelte";
  import Governors from "./dashboard/governors.svelte";
  import Ladder from "./dashboard/ladder.svelte";
  import Picker from "./dashboard/picker.svelte";
  import Pulse from "./dashboard/pulse.svelte";
  import SeatKey from "./dashboard/seat-key.svelte";
  import Swing from "./dashboard/swing.svelte";
  import Hexes from "./hexes.svelte";
  import LaterKey from "./later-key.svelte";
  import Locals from "./locals.svelte";
  import Nation from "./nation.svelte";
  import Search from "./search.svelte";

  const {
    election,
    estimates,
    nation,
    control,
    trail = null,
    board = null,
    clock,
    isOffline,
    forecasts = true,
  }: {
    election: LiveElection;
    estimates: Record<string, LiveEstimate>;
    nation: {
      map: Geography;
      codes: Record<string, string>;
      labels: Record<string, string>;
    } | null;
    control: Control | null;
    trail?: Trail | null;
    board?: Part | null;
    clock: number;
    isOffline: boolean;
    forecasts?: boolean;
  } = $props();

  const toLive = liveLinks();

  type Mode = "lead" | "chance" | "counted";

  const OFFICES = ["senate", "governor", "house"] as const;

  const modes = $state<Record<Chamber, Mode>>({
    senate: "lead",
    governor: "lead",
    house: "lead",
  });
  let hovered = $state<string | null>(null);
  let layout = $state<"shapes" | "hexes">("hexes");
  let probe = $state<string | null>(null);
  let house = $state<House | null>(null);
  let housePath: string | null = null;

  async function loadHouse(path: string) {
    if (housePath === path) {
      return;
    }
    housePath = path;
    house = null;
    try {
      const response = await fetch(`/live/house/${path}`);
      if (!response.ok) {
        throw new Error(String(response.status));
      }
      const found = (await response.json()) as House;
      if (housePath === path) {
        house = found;
      }
    } catch {
      if (housePath === path) {
        housePath = null;
      }
    }
  }

  $effect(() => {
    const path = election.slug;
    if ("requestIdleCallback" in window) {
      const id = requestIdleCallback(() => void loadHouse(path), {
        timeout: 4000,
      });
      return () => cancelIdleCallback(id);
    }
    const timer = setTimeout(() => void loadHouse(path), 2000);
    return () => clearTimeout(timer);
  });

  const NAMES: Record<Chamber, () => string> = {
    senate: m.night_nation_senate,
    governor: m.night_nation_governor,
    house: m.night_nation_house,
  };
  const MODES = $derived<Array<SegmentedOption<Mode>>>(
    (
      [
        ["lead", m.night_color_lead],
        ["chance", m.night_color_chance],
        ["counted", m.night_color_counted],
      ] as const
    )
      .filter(([value]) => forecasts || value !== "chance")
      .map(([value, label]) => ({ value, label: label() })),
  );
  const LAYOUTS = $derived<Array<SegmentedOption<"shapes" | "hexes">>>([
    { value: "shapes", label: m.night_house_shapes(), icon: MapIcon },
    { value: "hexes", label: m.night_house_hexes(), icon: HexagonIcon },
  ]);
  const LEGENDS: Record<Mode, () => string> = {
    lead: m.night_nation_legend,
    chance: m.night_nation_legend_chance,
    counted: m.night_nation_legend_reporting,
  };
  const MUTED =
    "color-mix(in oklab, var(--muted-foreground) 7%, var(--background))";
  const WAITING =
    "color-mix(in oklab, var(--muted-foreground) 20%, var(--background))";
  const LATER =
    "color-mix(in oklab, var(--muted-foreground) 5%, var(--background))";

  const summaries = $derived(
    election.races.map((race) => summarize(race, estimates[race.id] ?? null)),
  );
  const pair = $derived(sidesOf(control));
  const seats = $derived(
    pair == null
      ? []
      : summaries
          .filter((summary) => summary.race.office !== "other")
          .map((summary) => seatOf(summary, control, pair)),
  );
  const seatById = $derived(
    new Map(seats.map((entry) => [entry.summary.race.id, entry])),
  );
  const chambers = $derived(
    pair == null
      ? null
      : {
          senate: chamber(
            seats.filter(
              (entry) =>
                entry.summary.race.office === "senate" &&
                STATES.has(entry.summary.race.state),
            ),
            "senate",
            control?.holdovers.senate ?? {},
            pair,
          ),
          house: chamber(
            seats.filter(
              (entry) =>
                entry.summary.race.office === "house" &&
                STATES.has(entry.summary.race.state),
            ),
            "house",
            {},
            pair,
          ),
          governor: chamber(
            seats.filter(
              (entry) =>
                entry.summary.race.office === "governor" &&
                STATES.has(entry.summary.race.state),
            ),
            "governor",
            control?.holdovers.governor ?? {},
            pair,
          ),
        },
  );
  const points = $derived(
    trail ??
      (pair == null
        ? []
        : pulse(
            election.races.filter((race) => race.office === "house"),
            control,
            pair,
          )),
  );

  const byState = $derived(
    Map.groupBy(summaries, (summary) => summary.race.state),
  );
  const codes = $derived(
    [...byState.keys()].toSorted((a, b) =>
      (nation?.labels[a] ?? a).localeCompare(nation?.labels[b] ?? b),
    ),
  );
  const states = new Windowed(() => ({
    items: codes,
    key: (code) => code,
    size: 33,
    threshold: 40,
  }));
  const locals = $derived(
    summaries.filter((summary) => summary.race.office === "other"),
  );
  const counting = $derived(
    summaries.filter((summary) => summary.total > 0).length,
  );
  const uncontested = $derived(summaries.filter(isUncounted).length);
  const statesIn = $derived(
    codes.filter((code) =>
      (byState.get(code) ?? []).some((summary) => summary.total > 0),
    ).length,
  );
  const statesOpen = $derived(
    codes.filter((code) =>
      (byState.get(code) ?? []).some((summary) => !isUncounted(summary)),
    ).length,
  );
  const houseVotes = $derived(points.at(-1) ?? null);
  const houseTotal = $derived(
    houseVotes == null
      ? 0
      : houseVotes.left + houseVotes.right + houseVotes.other,
  );
  const remaining = $derived.by(() => {
    let found = 0;
    for (const entry of seats) {
      if (entry.summary.race.office === "house") {
        found += entry.remaining ?? 0;
      }
    }
    return found;
  });
  const flips = $derived(swing(seats.map((entry) => entry.shift)));
  const newest = $derived(election.newest);
  const isLive = $derived(counting > 0 && !election.isCertified);

  function of(code: string, wanted: LiveOffice) {
    return (byState.get(code) ?? []).filter(
      (summary) => summary.race.office === wanted,
    );
  }

  function tint(id: string | null) {
    if (id == null) {
      return WAITING;
    }
    if (pair != null) {
      for (const side of pair) {
        if (side.id === id) {
          return side.color;
        }
      }
    }
    return swatch(control?.parties[id]?.color, `party:${id}`);
  }

  const parties = $derived(reach(election.races));
  provideReach(() => parties);

  function partyOf(id: string) {
    return { short: short(id), color: tint(id) };
  }

  function short(id: string | null) {
    if (id == null) {
      return "–";
    }
    if (id === "") {
      return m.night_explorer_independent();
    }
    return control?.parties[id]?.short ?? "–";
  }

  function strength(summary: Summary) {
    if (summary.later != null) {
      return LATER;
    }
    const called = summary.settled;
    const option = called ?? summary.leader;
    if (option == null) {
      const held = seatById.get(summary.race.id)?.held ?? null;
      return held == null ? WAITING : marginShade(tint(held), safety(0.24));
    }
    return marginShade(
      tone(option),
      leadSafety(summary.margin, called != null),
    );
  }

  function reporting(share: number | null) {
    return share == null
      ? MUTED
      : marginShade("var(--map-ink)", safety(0.1 + 0.78 * share));
  }

  function shade(summary: Summary, wanted: Mode) {
    const entry = seatById.get(summary.race.id);
    if (summary.later != null) {
      return LATER;
    }
    if (wanted === "counted") {
      return reporting(summary.counted ?? (summary.total > 0 ? 0.02 : null));
    }
    const mark = seatHatch(summary);
    if (mark != null) {
      return mark.fill;
    }
    if (entry == null || wanted === "lead") {
      return strength(summary);
    }
    if (entry.rating === "waiting" || entry.rating === "counting") {
      return summary.total > 0
        ? marginShade(tone(summary.leader ?? undefined), safety(0.22))
        : WAITING;
    }
    const isLeft = entry.left >= entry.right;
    const sure = Math.abs(entry.left - entry.right);
    const color = pair == null ? WAITING : pair[isLeft ? 0 : 1].color;
    return marginShade(color, safety(0.18 + 0.82 * sure));
  }

  function turnOf(summary: Summary) {
    const found = seatById.get(summary.race.id)?.shift;
    return found == null || found.flip === "held"
      ? null
      : { flip: found.flip, to: found.to };
  }

  function seatHatch(summary: Summary) {
    const turn = turnOf(summary);
    return turn == null || summary.later != null
      ? null
      : hatch(tint(turn.to), turn.flip);
  }

  function flipsFor(view: Chamber, mode: Mode) {
    const found: Record<string, string> = {};
    if (mode === "counted" || view === "house") {
      return found;
    }
    for (const code of Object.keys(nation?.codes ?? {})) {
      const first = of(code, view)[0];
      const stripe = first == null ? null : seatHatch(first)?.stripe;
      if (stripe != null) {
        found[code] = stripe;
      }
    }
    return found;
  }

  function seatShade(entry: Seat) {
    if (isSettled(entry.rating)) {
      return tint(entry.side);
    }
    if (entry.rating === "later") {
      return LATER;
    }
    if (entry.rating === "waiting" || entry.side == null) {
      return WAITING;
    }
    if (entry.rating === "counting") {
      return marginShade(tint(entry.side), safety(0.28));
    }
    const share = { solid: 0.82, likely: 0.62, lean: 0.44, tossup: 0.28 }[
      entry.rating
    ];
    return marginShade(tint(entry.side), safety(share));
  }

  function reported(code: string) {
    const all = byState.get(code) ?? [];
    const lead =
      all.find((summary) => summary.race.office === "senate") ??
      all.find((summary) => summary.race.office === "governor") ??
      all[0];
    return (
      lead?.counted ?? (all.some((summary) => summary.total > 0) ? 0.02 : null)
    );
  }

  function colorsFor(view: Chamber, mode: Mode) {
    const found: Record<string, string> = {};
    for (const code of Object.keys(nation?.codes ?? {})) {
      const races = of(code, view);
      if (races.length === 0) {
        found[code] = MUTED;
        continue;
      }
      if (races.every((summary) => summary.later != null)) {
        found[code] = LATER;
        continue;
      }
      if (view === "house") {
        if (mode === "counted") {
          found[code] = reporting(reported(code));
          continue;
        }
        const seats = tally(races, "house");
        const top = seats.groups[0];
        const share = top == null ? 0 : top.seats / races.length;
        found[code] =
          top == null
            ? WAITING
            : marginShade(tone(top.option), safety(0.25 + 0.7 * share));
        continue;
      }
      const first = races[0];
      found[code] = first == null ? MUTED : shade(first, mode);
    }
    return found;
  }
  const colors = $derived({
    senate: colorsFor("senate", modes.senate),
    governor: colorsFor("governor", modes.governor),
    house: colorsFor("house", modes.house),
  });

  const districts = $derived(
    new Map<string, Summary>(
      summaries.flatMap((summary) =>
        summary.race.office === "house" && summary.race.district != null
          ? [
              [
                `${summary.race.state}-${Number.parseInt(summary.race.district, 10)}`,
                summary,
              ] as const,
            ]
          : [],
      ),
    ),
  );
  function laterFor(view: Chamber) {
    return Object.keys(nation?.codes ?? {}).filter((code) => {
      const races = of(code, view);
      return (
        races.length > 0 && races.every((summary) => summary.later != null)
      );
    });
  }
  const mapFlips = $derived({
    senate: flipsFor("senate", modes.senate),
    governor: flipsFor("governor", modes.governor),
    house: flipsFor("house", modes.house),
  });
  const later = $derived({
    senate: laterFor("senate"),
    governor: laterFor("governor"),
    house: laterFor("house"),
  });
  const districtLater = $derived(
    [...districts].flatMap(([key, summary]) =>
      summary.later == null ? [] : [key],
    ),
  );
  const districtFlips = $derived(
    modes.house === "counted"
      ? {}
      : Object.fromEntries(
          [...districts].flatMap(([key, summary]) => {
            const stripe = seatHatch(summary)?.stripe;
            return stripe == null ? [] : [[key, stripe] as const];
          }),
        ),
  );
  const districtColors = $derived(
    Object.fromEntries(
      [...districts].map(([key, summary]) => [
        key,
        shade(summary, modes.house),
      ]),
    ),
  );
  const hasShapes = $derived(house?.map != null);
  const hasHexes = $derived(house?.cartogram != null);
  const isHexes = $derived(
    (layout === "hexes" && hasHexes) || (!hasShapes && hasHexes),
  );

  function districtHref(key: string) {
    const summary = districts.get(key);
    return summary == null ? null : toLive(summary.race.slug);
  }

  const closings = $derived(
    [...Map.groupBy(election.pollClose, (entry) => entry.at)]
      .map(([at, entries]) => {
        const states = entries.map((entry) => entry.state).toSorted();
        return {
          at,
          states,
          local: [
            ...Map.groupBy(states, (code) =>
              stamp(at, ZONES[code] ?? "America/New_York"),
            ),
          ].map(([time, codes]) => ({ time, codes })),
        };
      })
      .toSorted((a, b) => a.at.localeCompare(b.at)),
  );
  const next = $derived(
    closings.find((closing) => Date.parse(closing.at) > clock) ?? null,
  );

  function wait(at: string) {
    const minutes = Math.max(1, Math.round((Date.parse(at) - clock) / 60_000));
    return minutes < 60
      ? numbers({ style: "unit", unit: "minute", unitDisplay: "short" }).format(
          minutes,
        )
      : numbers({
          style: "unit",
          unit: "hour",
          unitDisplay: "short",
          maximumFractionDigits: minutes < 600 ? 1 : 0,
        }).format(minutes / 60);
  }

  function href(code: string): string {
    return toLive(
      `${election.slug.split("/")[0]}/${code}/${election.slug.split("/")[1]}`,
    );
  }

  function name(code: string) {
    return nation?.labels[code] ?? code.toUpperCase();
  }

  function label(entry: Seat) {
    const { race } = entry.summary;
    if (race.office !== "house") {
      return name(race.state);
    }
    const number = Number.parseInt(race.district ?? "", 10);
    return `${race.state.toUpperCase()}-${Number.isNaN(number) || number === 0 ? "AL" : number}`;
  }

  function fullLabel(entry: Seat) {
    const ahead = entry.summary.settled ?? entry.summary.leader;
    return ahead == null
      ? `${entry.summary.race.title}: ${entry.summary.later == null ? m.night_nation_waiting() : m.night_later()}`
      : `${entry.summary.race.title}: ${surname(ahead)} ${edge(entry.summary)}`;
  }

  function seatHref(entry: Seat) {
    return toLive(entry.summary.race.slug);
  }
</script>

{#snippet chip(summary: Summary | undefined)}
  {#if summary == null}
    <span class="text-muted-foreground">–</span>
  {:else if summary.settled == null && summary.leader == null}
    <span class="text-muted-foreground"
      >{summary.later == null
        ? m.night_nation_waiting()
        : m.night_later()}</span
    >
  {:else}
    {const option = $derived(summary.settled ?? summary.leader ?? undefined)}
    {const entry = $derived(seatById.get(summary.race.id))}
    <span
      class="flex flex-wrap items-center justify-end gap-x-1.5 gap-y-0.5 whitespace-nowrap min-inline-0"
    >
      <span
        class="shrink-0 rounded-full block-2 inline-2"
        style:background-color={tone(option)}
      ></span>
      <span
        class={cn("truncate min-inline-0", {
          "font-semibold": summary.settled != null,
        })}>{surname(option)}</span
      >
      <span class="shrink-0 font-figures text-muted-foreground tabular-nums"
        >{edge(summary)}</span
      >
      {#if entry?.shift != null && entry.shift.flip !== "held"}
        <Flip
          flip={entry.shift.flip}
          from={partyOf(entry.shift.from)}
          to={partyOf(entry.shift.to)}
          isCompact={true}
        />
      {/if}
    </span>
  {/if}
{/snippet}

{#snippet tip(code: string, view: Chamber)}
  {const races = $derived(of(code, view))}
  <span class="text-sm font-semibold">{name(code)}</span>
  {#if races.length === 0}
    <span class="text-muted-foreground">{m.night_nation_none()}</span>
  {:else if view === "house"}
    {const seats = $derived(tally(races, "house"))}
    {#each seats.groups as group (party(group.option))}
      <span class="flex items-center justify-between gap-3">
        <span class="flex items-center gap-1.5"
          ><span
            class="rounded-full block-2 inline-2"
            style:background-color={tone(group.option)}
          ></span>{group.option.party?.short ?? surname(group.option)}</span
        >
        <span class="font-figures tabular-nums">{group.seats}</span>
      </span>
    {/each}
    <span class="text-muted-foreground"
      >{m.night_nation_seats({ count: races.length })}</span
    >
  {:else}
    {#each races.slice(0, 3) as summary (summary.race.id)}
      {const entry = $derived(seatById.get(summary.race.id))}
      <span class="grid gap-0.5">
        <span class="flex items-start justify-between gap-3">
          <span class="min-inline-0">{summary.race.title}</span>
          {@render chip(summary)}
        </span>
        {#if entry != null}
          <span class="flex justify-between gap-3 text-muted-foreground">
            {#if entry.held != null}
              <span
                >{m.night_tip_held({
                  party: short(entry.held),
                  name: entry.holder,
                })}</span
              >
            {/if}
            {#if forecasts && entry.rating !== "waiting" && entry.rating !== "later" && entry.rating !== "counting" && !isSettled(entry.rating) && pair != null}
              <span class="font-figures tabular-nums"
                >{entry.left >= entry.right ? pair[0].short : pair[1].short}
                {percent(Math.max(entry.left, entry.right), 0)}</span
              >
            {/if}
          </span>
        {/if}
      </span>
    {/each}
    {const share = $derived(reported(code))}
    {#if share != null}
      <span class="text-muted-foreground"
        >{m.night_tip_counted({ share: percent(share, 0) })}</span
      >
    {/if}
  {/if}
{/snippet}

{#snippet seatTip(entry: Seat)}
  {const summary = $derived(entry.summary)}
  <span class="text-sm font-semibold"
    >{summary.race.office === "house"
      ? `${name(summary.race.state)}, ${
          summary.race.district == null || summary.race.district === "0"
            ? m.night_nation_house()
            : m.night_rail_house({ district: summary.race.district })
        }`
      : name(summary.race.state)}</span
  >
  {@render chip(summary)}
  <span class="flex justify-between gap-3 text-muted-foreground">
    {#if entry.held != null}
      <span
        >{m.night_tip_held({
          party: short(entry.held),
          name: entry.holder,
        })}</span
      >
    {/if}
    {#if forecasts && entry.rating !== "waiting" && entry.rating !== "later" && entry.rating !== "counting" && !isSettled(entry.rating) && pair != null}
      <span class="font-figures tabular-nums"
        >{entry.left >= entry.right ? pair[0].short : pair[1].short}
        {percent(Math.max(entry.left, entry.right), 0)}</span
      >
    {/if}
  </span>
  {#if summary.counted != null}
    <span class="text-muted-foreground"
      >{m.night_tip_counted({ share: percent(summary.counted, 0) })}</span
    >
  {/if}
  {#if summary.projectedAt != null}
    <span class="text-muted-foreground"
      >{m.night_tip_projected({ time: stamp(summary.projectedAt) })}</span
    >
  {/if}
{/snippet}

{#snippet district(key: string)}
  {const summary = $derived(districts.get(key))}
  {#if summary == null}
    <span class="text-sm font-semibold">{key.toUpperCase()}</span>
    <span class="text-muted-foreground">{m.night_nation_none()}</span>
  {:else}
    {const entry = $derived(seatById.get(summary.race.id))}
    <span class="text-sm font-semibold"
      >{name(summary.race.state)}, {summary.race.district === "0"
        ? m.night_nation_house()
        : m.night_rail_house({ district: summary.race.district ?? "" })}</span
    >
    {@render chip(summary)}
    {#if entry != null}
      {#if entry.held != null}
        <span class="text-muted-foreground"
          >{m.night_tip_held({
            party: short(entry.held),
            name: entry.holder,
          })}</span
        >
      {/if}
    {/if}
    {#if summary.counted != null}
      <span class="text-muted-foreground"
        >{m.night_tip_counted({ share: percent(summary.counted, 0) })}</span
      >
    {/if}
  {/if}
{/snippet}

<article class="grid gap-px bg-border" data-sveltekit-preload-data="tap">
  <header class="grid gap-4 bg-background px-4 pbs-6 pbe-5 sm:px-8">
    <p
      class="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted-foreground"
    >
      <span
        class={cn(
          "inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-semibold",
          {
            "bg-foreground text-background": isLive && !isOffline,
            "border border-border": !isLive && !isOffline,
            "bg-(--no) text-background": isOffline,
          },
        )}
      >
        {#if isOffline}
          {m.night_status_offline()}
        {:else if isLive}
          <span
            class="animate-pulse rounded-full bg-background block-1.5 inline-1.5 motion-reduce:animate-none"
          ></span>
          {m.night_status_live()}
        {:else if election.isCertified}
          {m.night_status_certified()}
        {:else}
          {m.night_status_before()}
        {/if}
      </span>
      {#if newest != null}
        <span class="font-figures tabular-nums"
          >{m.night_updated({ time: stamp(newest) })}</span
        >
      {/if}
    </p>
    <div class="grid gap-1.5">
      <h1 class="title text-balance">{m.night_nation_title()}</h1>
      <p class="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 text-lg/snug">
        <a
          href={resolve("/[...path]", { path: election.slug })}
          class="font-semibold underline decoration-border underline-offset-4 hover:decoration-foreground"
          >{election.title}</a
        >
        <time datetime={election.date} class="text-muted-foreground"
          >{dates({
            weekday: "long",
            month: "long",
            day: "numeric",
            timeZone: "UTC",
          }).format(new Date(`${election.date}T00:00:00Z`))}</time
        >
      </p>
    </div>
    <Search
      path={election.slug}
      {summaries}
      {codes}
      labels={nation?.labels ?? {}}
      {href}
    />
    <dl class="grid grid-cols-2 gap-x-8 gap-y-3 sm:flex sm:flex-wrap">
      <div class="grid">
        <dt class="text-xs text-muted-foreground">{m.night_nation_states()}</dt>
        <dd class="font-figures text-2xl font-[760] tabular-nums">
          {statesIn}<span class="text-base font-semibold text-muted-foreground">
            / {statesOpen}</span
          >
        </dd>
      </div>
      <div class="grid">
        <dt class="text-xs text-muted-foreground">
          {m.night_nation_counting()}
        </dt>
        <dd class="font-figures text-2xl font-[760] tabular-nums">
          {counting}<span class="text-base font-semibold text-muted-foreground">
            / {summaries.length - uncontested}</span
          >
        </dd>
        {#if uncontested > 0}
          <dd class="text-xs text-muted-foreground">
            {m.night_nation_unopposed({ count: uncontested })}
          </dd>
        {/if}
      </div>
      {#if pair != null}
        <div class="grid">
          <dt class="text-xs text-muted-foreground">
            {m.night_kpi_house_votes()}
          </dt>
          <dd class="font-figures text-2xl font-[760] tabular-nums">
            {compact(houseTotal)}
          </dd>
        </div>
        {#if forecasts && remaining > 0}
          <div class="grid">
            <dt class="text-xs text-muted-foreground">
              {m.night_kpi_remaining()}
            </dt>
            <dd class="font-figures text-2xl font-[760] tabular-nums">
              {compact(remaining)}
            </dd>
          </div>
        {/if}
        {#if houseVotes != null && houseTotal > 0}
          {const margin = $derived(
            (houseVotes.left - houseVotes.right) / houseTotal,
          )}
          <div class="grid">
            <dt class="text-xs text-muted-foreground">
              {m.night_kpi_house_margin()}
            </dt>
            <dd
              class="font-figures text-2xl font-[760] tabular-nums"
              style:color={pair[margin >= 0 ? 0 : 1].color}
            >
              {pair[margin >= 0 ? 0 : 1].short.slice(0, 1)}+{lead(margin)}
            </dd>
          </div>
        {/if}
        <div class="grid">
          <dt class="text-xs text-muted-foreground">{m.night_kpi_flips()}</dt>
          <dd>
            <Swing swing={flips} party={partyOf} />
          </dd>
        </div>
      {/if}
      {#if next != null}
        <div class="grid">
          <dt class="text-xs text-muted-foreground">{m.night_kpi_next()}</dt>
          <dd class="font-figures text-2xl font-[760] tabular-nums">
            {wait(next.at)}<span
              class="ms-1.5 text-base font-semibold text-muted-foreground uppercase"
              >{next.states.slice(0, 4).join(" ")}</span
            >{#if next.states.length > 4}
              <span
                class="group relative ms-1 text-base font-semibold text-muted-foreground"
              >
                <button
                  type="button"
                  aria-describedby="next-more"
                  class="cursor-help underline decoration-current/45 decoration-dotted underline-offset-[0.2em]"
                  >+{next.states.length - 4}</button
                >
                <span
                  id="next-more"
                  role="tooltip"
                  class="pointer-events-none invisible absolute inset-s-0 inset-bs-full z-20 mbs-1.5 grid gap-1 rounded-sm bg-popover px-3 py-2 font-sans text-xs font-normal whitespace-nowrap text-foreground opacity-0 shadow-menu ring-1 ring-border transition-opacity duration-150 group-focus-within:visible group-focus-within:opacity-100 group-hover:visible group-hover:opacity-100 motion-reduce:transition-none"
                >
                  <span class="font-semibold"
                    >{m.night_kpi_next_more({ time: clockOf(next.at) })}</span
                  >
                  {#each next.states.slice(4) as code (code)}
                    <span class="text-muted-foreground">{name(code)}</span>
                  {/each}
                </span>
              </span>
            {/if}
          </dd>
        </div>
      {/if}
    </dl>
    <ViewLinks election={election.slug} isNation={true} />
  </header>

  <section
    class="grid gap-5 bg-background px-4 py-6 sm:px-8 lg:grid-cols-[17rem_minmax(0,36rem)] lg:gap-12 xl:grid-cols-[17rem_minmax(0,36rem)_minmax(16rem,22rem)]"
    aria-labelledby="night-pick"
  >
    <div class="grid content-start gap-2">
      <h2 id="night-pick" class="heading text-lg!">{m.night_pick_title()}</h2>
      <p class="text-sm text-pretty text-muted-foreground">
        {m.night_pick_hint()}
      </p>
    </div>
    <Picker
      {codes}
      labels={nation?.labels ?? {}}
      senate={(code: string) => of(code, "senate")}
      governor={(code: string) => of(code, "governor")}
      counted={reported}
      closes={(summary: Summary) => closing(election, summary.race)}
      describe={(summary: Summary) => {
        const entry = seatById.get(summary.race.id);
        return {
          held:
            entry?.held == null
              ? null
              : { short: short(entry.held), color: tint(entry.held) },
          flip:
            entry?.shift == null || entry.shift.flip === "held"
              ? null
              : { flip: entry.shift.flip, to: partyOf(entry.shift.to) },
        };
      }}
      {href}
    />
    <aside class="lg:col-span-2 xl:col-span-1" aria-labelledby="night-calls">
      <Calls
        {summaries}
        href={(summary: Summary) => toLive(summary.race.slug)}
      />
    </aside>
  </section>

  {#if chambers != null && pair != null}
    <Lazy part={board} view="chambers" ssr={false}>
      <div class="grid gap-px bg-border lg:grid-cols-3">
        <Chambers
          view={chambers.senate}
          sides={pair}
          title={NAMES.senate()}
          {forecasts}
          label={fullLabel}
          name={label}
          href={seatHref}
          shade={seatShade}
          detail={seatTip}
          line={chip}
        />
        <Chambers
          view={chambers.house}
          sides={pair}
          title={NAMES.house()}
          {forecasts}
          label={fullLabel}
          name={label}
          href={seatHref}
          shade={seatShade}
          detail={seatTip}
          line={chip}
        />
        <Governors
          view={chambers.governor}
          sides={pair}
          title={m.night_nation_governors()}
          label={fullLabel}
          name={label}
          href={seatHref}
          shade={seatShade}
          detail={seatTip}
          line={chip}
        />
      </div>
    </Lazy>
    {#if forecasts}
      <p
        class="bg-background px-4 py-2 text-xs text-pretty text-muted-foreground sm:px-8"
      >
        {m.night_chamber_note({ prior: percent(0.7, 0) })}
      </p>
    {/if}
  {/if}

  {#if chambers != null && pair != null}
    <Lazy part={board} view="ladder" ssr={false}>
      <section
        class="grid content-start gap-4 bg-background px-4 py-6 sm:px-8"
        aria-labelledby="night-ladder"
      >
        <div class="grid gap-0.5">
          <h2 id="night-ladder" class="heading text-lg!">
            {m.night_ladder_title()}
          </h2>
          <p class="max-inline-prose text-xs text-pretty text-muted-foreground">
            {m.night_ladder_intro({
              left: pair[0].name,
              right: pair[1].name,
              party: pair[0].name,
            })}
          </p>
        </div>
        <div class="grid items-start gap-x-12 gap-y-8 lg:grid-cols-2">
          <Ladder
            view={chambers.senate}
            sides={pair}
            {forecasts}
            title={NAMES.senate()}
            {label}
            href={seatHref}
            {tint}
            {short}
            party={partyOf}
          />
          <Ladder
            view={chambers.house}
            sides={pair}
            {forecasts}
            title={NAMES.house()}
            {label}
            href={seatHref}
            {tint}
            {short}
            party={partyOf}
            limit={20}
          />
        </div>
      </section>
    </Lazy>
  {/if}

  <Lazy part={board} view="closings" ssr={false}>
    <section class="grid content-start gap-2 bg-background px-4 py-6 sm:px-8">
      <div class="grid gap-0.5">
        <h2 class="heading text-lg!">{m.night_nation_closings()}</h2>
        {#if closings[0] != null}
          <p class="text-xs text-muted-foreground">
            {m.night_closings_zone({ zone: zoneName(closings[0].at) })}
          </p>
        {/if}
      </div>
      <ol class="gap-x-10 sm:columns-2 xl:columns-3">
        {#each closings as closing (closing.at)}
          {const isPast = $derived(Date.parse(closing.at) <= clock)}
          <li
            class={cn(
              "grid break-inside-avoid grid-cols-[5rem_1fr] items-baseline gap-2 pbe-1.5 text-[0.8125rem]",
              { "text-muted-foreground": isPast },
            )}
          >
            <span class="group relative justify-self-start">
              <button
                type="button"
                aria-describedby="closing-{closing.at}"
                class="cursor-help font-figures font-semibold tabular-nums underline decoration-current/45 decoration-dotted underline-offset-[0.2em]"
                >{clockOf(closing.at)}</button
              >
              <span
                id="closing-{closing.at}"
                role="tooltip"
                class="pointer-events-none invisible absolute inset-s-0 inset-bs-full z-20 mbs-1.5 grid gap-1 rounded-sm bg-popover px-3 py-2 text-xs text-foreground opacity-0 shadow-menu ring-1 ring-border transition-opacity duration-150 min-inline-48 group-focus-within:visible group-focus-within:opacity-100 group-hover:visible group-hover:opacity-100 motion-reduce:transition-none"
              >
                <span class="font-semibold">{m.night_closings_local()}</span>
                {#each closing.local as entry (entry.time)}
                  <span class="flex items-baseline justify-between gap-4">
                    <span class="uppercase">{entry.codes.join(", ")}</span>
                    <span
                      class="font-figures whitespace-nowrap text-muted-foreground tabular-nums"
                      >{entry.time}</span
                    >
                  </span>
                {/each}
              </span>
            </span>
            <span class="flex flex-wrap gap-1">
              {#each closing.states as code (code)}
                <a
                  href={href(code)}
                  class="rounded-xs border border-border px-1.5 py-0.5 text-xs font-semibold uppercase hover:highlighter"
                  >{code}</a
                >
              {/each}
            </span>
          </li>
        {/each}
      </ol>
    </section>
  </Lazy>

  {#each OFFICES as view (view)}
    {#snippet stateTip(code: string)}
      {@render tip(code, view)}
    {/snippet}
    {const mode = $derived(modes[view])}
    {const isDistricts = $derived(
      view === "house" && house != null && (hasShapes || hasHexes),
    )}
    <section
      class="grid bg-background px-4 py-6 sm:px-8"
      aria-labelledby="night-office-{view}"
    >
      <div class="grid content-start gap-3 inline-full max-inline-240">
        <div
          class="flex flex-wrap items-center justify-between gap-x-4 gap-y-2"
        >
          <h2 id="night-office-{view}" class="heading text-xl!">
            {NAMES[view]()}
          </h2>
          <div class="flex flex-wrap items-center gap-2">
            <Segmented
              options={MODES}
              bind:value={modes[view]}
              label={m.night_color_label()}
              size="sm"
            />
            {#if view === "house" && hasShapes && hasHexes}
              <Segmented
                options={LAYOUTS}
                bind:value={layout}
                label={m.night_house_layout()}
                size="sm"
              />
            {/if}
          </div>
        </div>
        {#if isDistricts && house != null}
          {#if isHexes && house.cartogram != null}
            <Hexes
              cartogram={house.cartogram}
              colors={districtColors}
              later={districtLater}
              flips={districtFlips}
              label={m.night_house_hexes_label()}
              bind:hovered={probe}
              href={districtHref}
              tip={district}
            />
          {:else if house.map != null}
            <Nation
              geography={house.map}
              codes={house.codes}
              colors={districtColors}
              later={districtLater}
              flips={districtFlips}
              label={m.night_house_map()}
              bind:hovered={probe}
              onPick={async (key: string) => {
                const link = districtHref(key);
                if (link != null) {
                  await goto(link);
                }
              }}
              tip={district}
            />
          {/if}
        {:else if nation == null}
          <p
            class="grid aspect-[1.6] place-items-center rounded-sm border border-dashed border-border p-4 text-center text-sm text-muted-foreground inline-full"
          >
            {m.night_nation_map_missing()}
          </p>
        {:else}
          <Nation
            geography={nation.map}
            codes={nation.codes}
            colors={colors[view]}
            later={later[view]}
            flips={mapFlips[view]}
            label={m.night_nation_map({ view: NAMES[view]() })}
            bind:hovered
            onPick={async (code: string) => goto(href(code))}
            tip={stateTip}
          />
        {/if}
        <p class="text-xs text-muted-foreground">
          {view === "house" && mode === "lead"
            ? m.night_house_legend()
            : LEGENDS[mode]()}
        </p>
        {#if pair != null && mode !== "counted"}
          <SeatKey
            sides={pair}
            changing={swing(
              seats
                .filter((entry) => entry.summary.race.office === view)
                .map((entry) => entry.shift),
            )}
          />
        {/if}
        {#if (isDistricts ? districtLater : later[view]).length > 0}
          <LaterKey />
        {/if}
      </div>
    </section>
  {/each}

  {#if pair != null}
    <Lazy part={board} view="pulse" ssr={false}>
      <Pulse {points} sides={pair} />
    </Lazy>
  {/if}

  <Lazy part={board} view="states" ssr={false}>
    <section
      class="@container grid grid-cols-[minmax(0,1fr)] gap-3 bg-background px-4 py-6 sm:px-8"
    >
      <h2 class="heading text-lg!">{m.night_nation_table()}</h2>
      <div
        class="overflow-x-auto overflow-y-hidden overscroll-x-contain min-inline-0 data-fits:overflow-visible"
        {@attach fits}
      >
        <table
          class="table-fixed border-collapse text-[0.8125rem] inline-full min-inline-240 @max-[60rem]:block @max-[60rem]:shadow-[inset_0_1px_0_var(--color-foreground)] @max-[60rem]:min-inline-0"
        >
          <colgroup class="@max-[60rem]:hidden">
            <col class="inline-40" />
            <col class="inline-32" />
            <col />
            <col />
            <col class="inline-[27%]" />
            <col class="inline-20" />
          </colgroup>
          <thead
            class="sticky inset-bs-0 z-2 bg-background @max-[60rem]:sr-only [&_th]:shadow-[inset_0_-1px_0_var(--color-foreground)]"
          >
            <tr class="text-start">
              <th scope="col" class="py-1.5 ps-1 text-start font-semibold"
                >{m.night_nation_state()}</th
              >
              <th scope="col" class="py-1.5 text-start font-semibold"
                >{m.night_nation_reporting()}</th
              >
              <th scope="col" class="py-1.5 text-start font-semibold"
                >{m.night_nation_senate()}</th
              >
              <th scope="col" class="py-1.5 text-start font-semibold"
                >{m.night_nation_governor()}</th
              >
              <th scope="col" class="py-1.5 text-start font-semibold"
                >{m.night_nation_house()}</th
              >
              <th scope="col" class="py-1.5 pe-1 text-end font-semibold"
                >{m.night_nation_latest()}</th
              >
            </tr>
          </thead>
          <tbody class="@max-[60rem]:block" {@attach states.anchor}>
            {#if states.before > 0}
              <tr aria-hidden="true" class="@max-[60rem]:block">
                <td
                  class="p-0 @max-[60rem]:block"
                  colspan="6"
                  style:block-size="{states.before}px"
                ></td>
              </tr>
            {/if}
            {#each states.entries as { value: code, index, key } (key)}
              {const all = $derived(byState.get(code) ?? [])}
              {const share = $derived(reported(code))}
              {const senate = $derived(of(code, "senate")[0])}
              {const governor = $derived(of(code, "governor")[0])}
              {const house = $derived(of(code, "house"))}
              {const latest = $derived(
                all
                  .map((summary) => summary.at ?? "")
                  .toSorted()
                  .at(-1) ?? "",
              )}
              <tr
                class={cn(
                  "cursor-pointer border-be border-border hover:highlighter @max-[60rem]:flex @max-[60rem]:flex-wrap @max-[60rem]:items-center @max-[60rem]:gap-x-4 @max-[60rem]:py-2.5 @max-[60rem]:before:order-4 @max-[60rem]:before:basis-full @max-[60rem]:before:content-[''] @max-[60rem]:after:order-7 @max-[60rem]:after:basis-full @max-[60rem]:after:content-['']",
                  { highlighter: hovered === code },
                )}
                {@attach states.measure(index)}
                onpointerenter={(event) => {
                  if (isHover(event)) hovered = code;
                }}
                onpointerleave={() => (hovered = null)}
                onclick={async () => goto(href(code))}
              >
                <th
                  scope="row"
                  class="truncate py-1.5 ps-1 pe-3 text-start font-semibold @max-[60rem]:order-1 @max-[60rem]:flex-1 @max-[60rem]:p-0 @max-[60rem]:text-sm @max-[60rem]:min-inline-0"
                  ><a href={href(code)} class="hover:underline">{name(code)}</a
                  ></th
                >
                <td class="py-1.5 @max-[60rem]:order-2 @max-[60rem]:p-0">
                  <span class="flex items-center gap-2">
                    <span
                      class="relative overflow-hidden rounded-xs bg-muted block-1.5 inline-16"
                      aria-hidden="true"
                    >
                      <span
                        class="absolute inset-y-0 inset-s-0 bg-foreground"
                        style:inline-size="{(share ?? 0) * 100}%"
                      ></span>
                    </span>
                    <span
                      class="text-end font-figures text-muted-foreground tabular-nums inline-12"
                      >{share == null ? "–" : percent(share, 0)}</span
                    >
                  </span>
                </td>
                <td
                  class={cn(
                    "py-1.5 pe-4 @max-[60rem]:order-5 @max-[60rem]:flex @max-[60rem]:items-center @max-[60rem]:gap-1.5 @max-[60rem]:p-0 @max-[60rem]:pbs-1.5 @max-[60rem]:min-inline-0",
                    { "@max-[60rem]:hidden": senate == null },
                  )}
                >
                  <span
                    class="hidden text-xs text-muted-foreground @max-[60rem]:inline"
                    >{m.night_nation_senate()}</span
                  >
                  {@render chip(senate)}
                </td>
                <td
                  class={cn(
                    "py-1.5 pe-4 @max-[60rem]:order-6 @max-[60rem]:flex @max-[60rem]:items-center @max-[60rem]:gap-1.5 @max-[60rem]:p-0 @max-[60rem]:pbs-1.5 @max-[60rem]:min-inline-0",
                    { "@max-[60rem]:hidden": governor == null },
                  )}
                >
                  <span
                    class="hidden text-xs text-muted-foreground @max-[60rem]:inline"
                    >{m.night_nation_governor()}</span
                  >
                  {@render chip(governor)}
                </td>
                <td
                  class={cn(
                    "py-1.5 @max-[60rem]:order-8 @max-[60rem]:flex @max-[60rem]:basis-full @max-[60rem]:items-baseline @max-[60rem]:gap-1.5 @max-[60rem]:p-0 @max-[60rem]:pbs-1.5",
                    { "@max-[60rem]:hidden": house.length === 0 },
                  )}
                >
                  <span
                    class="hidden shrink-0 text-xs text-muted-foreground @max-[60rem]:inline"
                    >{m.night_nation_house()}</span
                  >
                  {#if house.length === 0}
                    <span class="text-muted-foreground">–</span>
                  {:else}
                    <span
                      class="flex flex-wrap gap-px"
                      aria-label={m.night_nation_seats({ count: house.length })}
                    >
                      {#each house.toSorted((x, y) => Number.parseInt(x.race.district ?? "0", 10) - Number.parseInt(y.race.district ?? "0", 10)) as seat (seat.race.id)}
                        <a
                          href={toLive(seat.race.slug)}
                          class="rounded-[1px] block-2.5 inline-2.5 hover:outline-1 hover:outline-foreground"
                          style:background-color={seat.leader == null
                            ? WAITING
                            : shade(seat, "lead")}
                          style:background-image={seatHatch(seat)?.stripes}
                          title={seat.race.title}
                          aria-label={seat.race.title}
                          onclick={(event) => event.stopPropagation()}
                        ></a>
                      {/each}
                    </span>
                  {/if}
                </td>
                <td
                  class="py-1.5 pe-1 text-end font-figures text-muted-foreground tabular-nums @max-[60rem]:order-3 @max-[60rem]:p-0 @max-[60rem]:text-xs"
                  >{latest === "" ? "–" : clockOf(latest)}</td
                >
              </tr>
            {/each}
            {#if states.after > 0}
              <tr aria-hidden="true" class="@max-[60rem]:block">
                <td
                  class="p-0 @max-[60rem]:block"
                  colspan="6"
                  style:block-size="{states.after}px"
                ></td>
              </tr>
            {/if}
          </tbody>
        </table>
      </div>
    </section>
  </Lazy>

  {#if pair != null}
    <Lazy part={board} view="explorer" ssr={false}>
      <Explorer
        {seats}
        sides={pair}
        {forecasts}
        {label}
        href={seatHref}
        {tint}
        {short}
        place={name}
      />
    </Lazy>
  {/if}

  {#if locals.length > 0}
    <Lazy part={board} view="locals" ssr={false}>
      <Locals
        summaries={locals}
        labels={nation?.labels}
        isNation={nation != null}
      />
    </Lazy>
  {/if}
</article>
