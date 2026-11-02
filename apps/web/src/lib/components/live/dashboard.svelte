<script lang="ts">
  import type {
    LiveElection,
    LiveEstimate,
    LiveOffice,
  } from "@ea/analytics/live";
  import { goto } from "$app/navigation";
  import { resolve } from "$app/paths";
  import HexagonIcon from "@lucide/svelte/icons/hexagon";
  import MapIcon from "@lucide/svelte/icons/map";
  import type { SegmentedOption } from "#lib/components/ui/segmented/index.js";
  import type { Chamber, Control, Seat } from "#lib/live/nation.js";
  import type { Geography } from "#lib/live/standing.js";
  import type { Summary } from "#lib/live/summary.js";
  import type { House } from "#lib/server/live-board.js";
  import BoardLink from "#lib/components/live/board-link.svelte";
  import { Segmented } from "#lib/components/ui/segmented/index.js";
  import { isHover } from "#lib/explain.svelte.js";
  import { fits } from "#lib/fits.js";
  import { dates, numbers, percent } from "#lib/format.js";
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
    pulse,
    seat as seatOf,
    sides as sidesOf,
    STATES,
  } from "#lib/live/nation.js";
  import { closing, party, summarize, tally } from "#lib/live/summary.js";
  import { ZONES } from "#lib/live/zones.js";
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";
  import { cn } from "#lib/utils.js";
  import { Windowed } from "#lib/windowed.svelte.js";
  import Chambers from "./dashboard/chamber.svelte";
  import Explorer from "./dashboard/explorer.svelte";
  import Flip from "./dashboard/flip.svelte";
  import Governors from "./dashboard/governors.svelte";
  import Ladder from "./dashboard/ladder.svelte";
  import Picker from "./dashboard/picker.svelte";
  import Pulse from "./dashboard/pulse.svelte";
  import Hexes from "./hexes.svelte";
  import Locals from "./locals.svelte";
  import Nation from "./nation.svelte";

  const {
    election,
    estimates,
    nation,
    control,
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
    clock: number;
    isOffline: boolean;
    forecasts?: boolean;
  } = $props();

  const toLive = liveLinks();

  type Mode = "lead" | "chance" | "flips" | "counted";

  let view = $state<Chamber>("senate");
  let mode = $state<Mode>("lead");
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
    if (view === "house") {
      void loadHouse(path);
      return;
    }
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
  const VIEWS = $derived<Array<SegmentedOption<Chamber>>>(
    (["senate", "governor", "house"] as const).map((value) => ({
      value,
      label: NAMES[value](),
    })),
  );
  const MODES = $derived<Array<SegmentedOption<Mode>>>(
    (
      [
        ["lead", m.night_color_lead],
        ["chance", m.night_color_chance],
        ["flips", m.night_color_flips],
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
    flips: m.night_nation_legend_flips,
    counted: m.night_nation_legend_reporting,
  };
  const MUTED =
    "color-mix(in oklab, var(--muted-foreground) 7%, var(--background))";
  const WAITING =
    "color-mix(in oklab, var(--muted-foreground) 20%, var(--background))";

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
    pair == null
      ? []
      : pulse(
          seats.filter((entry) => entry.summary.race.office === "house"),
          control,
          pair,
        ),
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
  const statesIn = $derived(
    codes.filter((code) =>
      (byState.get(code) ?? []).some((summary) => summary.total > 0),
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
  const flips = $derived(seats.filter((entry) => entry.isFlip).length);
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
    const called = summary.winner ?? summary.projected;
    const option = called ?? summary.leader;
    if (option == null) {
      const held = seatById.get(summary.race.id)?.held ?? null;
      return held == null
        ? WAITING
        : `color-mix(in oklab, ${tint(held)} 24%, var(--background))`;
    }
    const share =
      called == null
        ? Math.round(30 + 60 * Math.min(summary.margin / 0.3, 1))
        : 100;
    return `color-mix(in oklab, ${tone(option)} ${share}%, var(--background))`;
  }

  function reporting(share: number | null) {
    return share == null
      ? MUTED
      : `color-mix(in oklab, var(--foreground) ${Math.round(10 + 78 * share)}%, var(--background))`;
  }

  function shade(summary: Summary, wanted: Mode = mode) {
    const entry = seatById.get(summary.race.id);
    if (wanted === "counted") {
      return reporting(summary.counted ?? (summary.total > 0 ? 0.02 : null));
    }
    if (entry == null || wanted === "lead") {
      return strength(summary);
    }
    if (wanted === "flips") {
      if (!entry.isFlip) {
        return summary.total > 0 ? MUTED : WAITING;
      }
      return `color-mix(in oklab, ${tint(entry.side)} ${entry.rating === "won" || entry.rating === "projected" ? 100 : 70}%, var(--background))`;
    }
    if (entry.rating === "waiting" || entry.rating === "counting") {
      return summary.total > 0
        ? `color-mix(in oklab, ${tone(summary.leader ?? undefined)} 22%, var(--background))`
        : WAITING;
    }
    const isLeft = entry.left >= entry.right;
    const sure = Math.abs(entry.left - entry.right);
    const color = pair == null ? WAITING : pair[isLeft ? 0 : 1].color;
    return `color-mix(in oklab, ${color} ${Math.round(18 + 82 * sure)}%, var(--background))`;
  }

  function seatShade(entry: Seat) {
    if (entry.rating === "won" || entry.rating === "projected") {
      return tint(entry.side);
    }
    if (entry.rating === "waiting" || entry.side == null) {
      return WAITING;
    }
    if (entry.rating === "counting") {
      return `color-mix(in oklab, ${tint(entry.side)} 28%, var(--background))`;
    }
    const share = { solid: 82, likely: 62, lean: 44, tossup: 28 }[entry.rating];
    return `color-mix(in oklab, ${tint(entry.side)} ${share}%, var(--background))`;
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

  const colors = $derived.by(() => {
    const found: Record<string, string> = {};
    for (const code of Object.keys(nation?.codes ?? {})) {
      const races = of(code, view);
      if (races.length === 0) {
        found[code] = MUTED;
        continue;
      }
      if (view === "house") {
        if (mode === "counted") {
          found[code] = reporting(reported(code));
          continue;
        }
        const seats = tally(races, "house");
        const top = seats.groups[0];
        const share = top == null ? 0 : (top.won + top.leading) / races.length;
        found[code] =
          top == null
            ? WAITING
            : `color-mix(in oklab, ${tone(top.option)} ${Math.round(25 + 70 * share)}%, var(--background))`;
        continue;
      }
      const first = races[0];
      found[code] = first == null ? MUTED : shade(first);
    }
    return found;
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
  const districtColors = $derived(
    Object.fromEntries(
      [...districts].map(([key, summary]) => [key, shade(summary)]),
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
    const ahead =
      entry.summary.winner ?? entry.summary.projected ?? entry.summary.leader;
    return ahead == null
      ? `${entry.summary.race.title}: ${m.night_nation_waiting()}`
      : `${entry.summary.race.title}: ${surname(ahead)} +${lead(entry.summary.margin)}`;
  }

  function seatHref(entry: Seat) {
    return toLive(entry.summary.race.slug);
  }
</script>

{#snippet chip(summary: Summary | undefined)}
  {#if summary == null}
    <span class="text-muted-foreground">–</span>
  {:else if summary.leader == null}
    <span class="text-muted-foreground">{m.night_nation_waiting()}</span>
  {:else}
    {const option = $derived(
      summary.winner ?? summary.projected ?? summary.leader,
    )}
    {const entry = $derived(seatById.get(summary.race.id))}
    <span class="flex items-center gap-1.5 whitespace-nowrap min-inline-0">
      <span
        class="shrink-0 rounded-full block-2 inline-2"
        style:background-color={tone(option)}
      ></span>
      <span
        class={cn("truncate min-inline-0", {
          "font-semibold": summary.winner != null || summary.projected != null,
        })}>{surname(option)}</span
      >
      <span class="shrink-0 font-figures text-muted-foreground tabular-nums"
        >+{lead(summary.margin)}</span
      >
      {#if entry?.isFlip === true}
        <Flip party={short(entry.side)} color={tint(entry.side)} />
      {/if}
    </span>
  {/if}
{/snippet}

{#snippet tip(code: string)}
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
        <span class="font-figures tabular-nums"
          >{group.won + group.leading}</span
        >
      </span>
    {/each}
    <span class="text-muted-foreground"
      >{m.night_nation_seats({ count: races.length })}</span
    >
  {:else}
    {#each races.slice(0, 3) as summary (summary.race.id)}
      {const entry = $derived(seatById.get(summary.race.id))}
      <span class="grid gap-0.5">
        <span class="flex items-center justify-between gap-3">
          <span>{summary.race.title}</span>
          {@render chip(summary)}
        </span>
        {#if entry != null}
          <span class="flex justify-between gap-3 text-muted-foreground">
            <span
              >{m.night_tip_held({
                party: short(entry.held),
                name: entry.holder,
              })}</span
            >
            {#if forecasts && entry.rating !== "waiting" && entry.rating !== "counting" && entry.rating !== "won" && entry.rating !== "projected" && pair != null}
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
    <span
      >{m.night_tip_held({
        party: short(entry.held),
        name: entry.holder,
      })}</span
    >
    {#if forecasts && entry.rating !== "waiting" && entry.rating !== "counting" && entry.rating !== "won" && entry.rating !== "projected" && pair != null}
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
      <span class="text-muted-foreground"
        >{m.night_tip_held({
          party: short(entry.held),
          name: entry.holder,
        })}</span
      >
    {/if}
    {#if summary.counted != null}
      <span class="text-muted-foreground"
        >{m.night_tip_counted({ share: percent(summary.counted, 0) })}</span
      >
    {/if}
  {/if}
{/snippet}

<article class="grid gap-px bg-border">
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
      <BoardLink election={election.slug} />
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
    <dl class="grid grid-cols-2 gap-x-8 gap-y-3 sm:flex sm:flex-wrap">
      <div class="grid">
        <dt class="text-xs text-muted-foreground">{m.night_nation_states()}</dt>
        <dd class="font-figures text-2xl font-[760] tabular-nums">
          {statesIn}<span class="text-base font-semibold text-muted-foreground">
            / {codes.length}</span
          >
        </dd>
      </div>
      <div class="grid">
        <dt class="text-xs text-muted-foreground">
          {m.night_nation_counting()}
        </dt>
        <dd class="font-figures text-2xl font-[760] tabular-nums">
          {counting}<span class="text-base font-semibold text-muted-foreground">
            / {summaries.length}</span
          >
        </dd>
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
          <dd class="font-figures text-2xl font-[760] tabular-nums">
            {flips}
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
  </header>

  <section
    class="grid gap-5 bg-background px-4 py-6 sm:px-8 lg:grid-cols-[17rem_minmax(0,36rem)] lg:gap-12"
    aria-labelledby="night-pick"
  >
    <div class="grid content-start gap-2">
      <h2 id="night-pick" class="heading text-lg!">{m.night_pick_title()}</h2>
      <p class="text-sm text-pretty text-muted-foreground">
        {m.night_pick_hint()}
      </p>
      <p class="text-xs text-pretty text-muted-foreground">
        {m.night_pick_legend()}
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
          isFlip: entry?.isFlip ?? false,
        };
      }}
      {href}
    />
  </section>

  {#if chambers != null && pair != null}
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
    {#if forecasts}
      <p
        class="bg-background px-4 py-2 text-xs text-pretty text-muted-foreground sm:px-8"
      >
        {m.night_chamber_note({ prior: percent(0.7, 0) })}
      </p>
    {/if}
  {/if}

  <section
    class="grid gap-6 bg-background px-4 py-6 sm:px-8 lg:grid-cols-[minmax(0,1fr)_24rem] lg:grid-rows-[auto_1fr] lg:gap-x-10 lg:gap-y-8"
  >
    <div class="grid content-start gap-3">
      <div class="flex flex-wrap items-center justify-between gap-2">
        <Segmented
          options={VIEWS}
          bind:value={view}
          label={m.night_nation_views()}
        />
        <div class="flex flex-wrap items-center gap-2">
          <Segmented
            options={MODES}
            bind:value={mode}
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
      {#if view === "house" && house != null && (hasShapes || hasHexes)}
        {#if isHexes && house.cartogram != null}
          <Hexes
            cartogram={house.cartogram}
            colors={districtColors}
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
          {colors}
          label={m.night_nation_map({ view: NAMES[view]() })}
          bind:hovered
          onPick={async (code: string) => goto(href(code))}
          {tip}
        />
      {/if}
      <p class="text-xs text-muted-foreground">
        {view === "house" && mode === "lead"
          ? m.night_house_legend()
          : LEGENDS[mode]()}
      </p>
    </div>

    <aside class="lg:col-start-2 lg:row-span-2 lg:row-start-1">
      {#if chambers != null && pair != null}
        <Ladder
          view={chambers[view]}
          sides={pair}
          {forecasts}
          {label}
          href={seatHref}
          {tint}
        />
      {/if}
    </aside>

    <section class="grid content-start gap-2 lg:col-start-1 lg:row-start-2">
      <div class="grid gap-0.5">
        <h2 class="heading text-lg!">{m.night_nation_closings()}</h2>
        {#if closings[0] != null}
          <p class="text-xs text-muted-foreground">
            {m.night_closings_zone({ zone: zoneName(closings[0].at) })}
          </p>
        {/if}
      </div>
      <ol class="gap-x-10 sm:columns-2">
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
  </section>

  {#if pair != null}
    <Pulse {points} sides={pair} />
  {/if}

  <section
    class="grid grid-cols-[minmax(0,1fr)] gap-3 bg-background px-4 py-6 sm:px-8"
  >
    <h2 class="heading text-lg!">{m.night_nation_table()}</h2>
    <div
      class="relative overflow-x-auto overflow-y-hidden overscroll-x-contain min-inline-0 not-data-fits:overflow-y-auto not-data-fits:max-block-svh data-fits:overflow-visible"
      {@attach fits}
    >
      <table
        class="table-fixed border-collapse text-[0.8125rem] inline-full min-inline-240"
      >
        <colgroup>
          <col class="inline-40" />
          <col class="inline-32" />
          <col />
          <col />
          <col class="inline-[27%]" />
          <col class="inline-20" />
        </colgroup>
        <thead
          class="sticky inset-bs-0 z-2 bg-background [&_th]:shadow-[inset_0_-1px_0_var(--color-foreground)]"
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
        <tbody {@attach states.anchor}>
          {#if states.before > 0}
            <tr aria-hidden="true">
              <td class="p-0" colspan="6" style:block-size="{states.before}px"
              ></td>
            </tr>
          {/if}
          {#each states.entries as { value: code, index, key } (key)}
            {const all = $derived(byState.get(code) ?? [])}
            {const share = $derived(reported(code))}
            {const house = $derived(of(code, "house"))}
            {const latest = $derived(
              all
                .map((summary) => summary.at ?? "")
                .toSorted()
                .at(-1) ?? "",
            )}
            <tr
              class={cn(
                "cursor-pointer border-be border-border hover:highlighter",
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
                class="truncate py-1.5 ps-1 pe-3 text-start font-semibold"
                ><a href={href(code)} class="hover:underline">{name(code)}</a
                ></th
              >
              <td class="py-1.5">
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
              <td class="py-1.5 pe-4">{@render chip(of(code, "senate")[0])}</td>
              <td class="py-1.5 pe-4">
                {@render chip(of(code, "governor")[0])}
              </td>
              <td class="py-1.5">
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
                        title={seat.race.title}
                        aria-label={seat.race.title}
                        onclick={(event) => event.stopPropagation()}
                      ></a>
                    {/each}
                  </span>
                {/if}
              </td>
              <td
                class="py-1.5 pe-1 text-end font-figures text-muted-foreground tabular-nums"
                >{latest === "" ? "–" : clockOf(latest)}</td
              >
            </tr>
          {/each}
          {#if states.after > 0}
            <tr aria-hidden="true">
              <td class="p-0" colspan="6" style:block-size="{states.after}px"
              ></td>
            </tr>
          {/if}
        </tbody>
      </table>
    </div>
  </section>

  {#if pair != null}
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
  {/if}

  {#if locals.length > 0}
    <Locals
      summaries={locals}
      labels={nation?.labels}
      isNation={nation != null}
    />
  {/if}
</article>
