<script lang="ts">
  import type { LiveElection, LiveEstimate } from "@ea/analytics/live";
  import ChevronLeftIcon from "@lucide/svelte/icons/chevron-left";
  import type { Entry, Seating } from "#lib/live/ballot.js";
  import type { Geography } from "#lib/live/standing.js";
  import type { Ballot, BallotTier } from "#lib/server/live-ballot.js";
  import BoardLink from "#lib/components/live/board-link.svelte";
  import { Segmented } from "#lib/components/ui/segmented/index.js";
  import { count } from "#lib/format.js";
  import { district, entries as entriesOf, seating } from "#lib/live/ballot.js";
  import {
    clock as clockOf,
    HATCH,
    placeSlug,
    stamp,
    tone,
  } from "#lib/live/format.js";
  import { provideReach, reach } from "#lib/live/lineup.js";
  import { liveLinks } from "#lib/live/links.js";
  import { standing } from "#lib/live/standing.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";
  import Later from "./later.svelte";
  import LiveMap from "./map.svelte";
  import Bar from "./state/bar.svelte";
  import Card from "./state/card.svelte";
  import Chamber from "./state/chamber.svelte";
  import Districts from "./state/districts.svelte";
  import Local from "./state/local.svelte";
  import Measure from "./state/measure.svelte";
  import Peek from "./state/peek.svelte";
  import Rows from "./state/rows.svelte";
  import Sheet from "./state/sheet.svelte";
  import Ticket from "./state/ticket.svelte";

  const {
    election,
    estimates,
    ballot,
    geography,
    isOffline,
  }: {
    election: LiveElection;
    estimates: Record<string, LiveEstimate>;
    ballot: Ballot;
    geography: Geography | null;
    isOffline: boolean;
  } = $props();

  const toLive = liveLinks();

  const WAITING =
    "color-mix(in oklab, var(--muted-foreground) 20%, var(--background))";
  const LATER =
    "color-mix(in oklab, var(--muted-foreground) 5%, var(--background))";

  let picked = $state<string | null>(null);

  function make(tier: BallotTier, chamber: string | null = null) {
    return entriesOf({
      contests: ballot.contests
        .filter(
          (row) =>
            row.tier === tier && (chamber == null || row.chamber === chamber),
        )
        .map((row) => row.contest),
      election,
      estimates,
      parties: ballot.parties,
      families: ballot.families,
      state: ballot.state,
      tier,
    });
  }

  function byDistrict(list: Array<Entry>) {
    return list.toSorted(
      (a, b) =>
        district(a) - district(b) ||
        a.contest.title.localeCompare(b.contest.title),
    );
  }

  const top = $derived([...make("senate"), ...make("governor")]);
  const statewide = $derived(make("statewide"));
  const house = $derived(byDistrict(make("house")));
  const courts = $derived(make("courts"));
  const measures = $derived(make("measures"));
  const chambers = $derived(
    ballot.chambers
      .toSorted(
        (a, b) => Number(a.tier === "lower") - Number(b.tier === "lower"),
      )
      .map((chamber) => {
        const entries = byDistrict(make(chamber.tier, chamber.id));
        return {
          ...chamber,
          entries,
          seats: seating(entries, chamber.holdovers, ballot.parties),
        };
      })
      .filter((chamber) => chamber.entries.length > 0),
  );
  const legislature = $derived.by(() => {
    let found = 0;
    for (const chamber of chambers) {
      found += chamber.entries.length;
    }
    return found;
  });
  const everything = $derived([
    ...top,
    ...statewide,
    ...house,
    ...chambers.flatMap((chamber) => chamber.entries),
    ...courts,
    ...measures,
  ]);
  const counting = $derived(
    everything.filter((entry) => entry.summary.total > 0).length,
  );
  const flips = $derived(everything.filter((entry) => entry.isFlip).length);
  const isLive = $derived(counting > 0 && !election.isCertified);
  const later = $derived(
    election.later?.[ballot.state]?.feed === "none" && counting === 0
      ? (election.later[ballot.state] ?? null)
      : null,
  );
  const closes = $derived(
    election.pollClose
      .filter((entry) => entry.state === ballot.state)
      .map((entry) => entry.at)
      .toSorted()
      .at(-1) ?? null,
  );
  const national = $derived(
    `${election.slug.split("/")[0]}/${election.slug.split("/")[2]}`,
  );

  const mappable = $derived(
    top.filter(
      (entry) =>
        entry.isLive &&
        entry.summary.race.units.some((unit) => unit.scheme === "us.county"),
    ),
  );
  const mapped = $derived(
    mappable.find((entry) => entry.contest.id === picked) ??
      mappable[0] ??
      top[0] ??
      null,
  );
  const views = $derived(
    mappable.map((entry) => ({
      value: entry.contest.id,
      label: entry.contest.title,
    })),
  );
  const counties = $derived(
    new Map(
      (mapped?.summary.race.units ?? []).flatMap((unit) =>
        unit.scheme === "us.county" ? [[unit.id, unit.name] as const] : [],
      ),
    ),
  );
  const houseSeats = $derived(seating(house, {}, ballot.parties));

  const parties = $derived(
    reach(everything.map((entry) => entry.summary.race)),
  );
  provideReach(() => parties);

  const sections = $derived(
    [
      { id: "top", label: m.night_state_top(), count: top.length },
      {
        id: "statewide",
        label: m.night_state_statewide(),
        count: statewide.length,
      },
      { id: "house", label: m.night_state_house(), count: house.length },
      {
        id: "legislature",
        label: m.night_state_legislature(),
        count: legislature,
      },
      { id: "courts", label: m.night_state_courts(), count: courts.length },
      {
        id: "measures",
        label: m.night_state_measures(),
        count: measures.length,
      },
      { id: "local", label: m.night_state_local(), count: ballot.locals.total },
    ].filter((section) => section.count > 0),
  );

  function href(entry: Entry) {
    return toLive(entry.contest.slug);
  }

  function seatLabel(entry: Entry) {
    const seat = entry.contest.seat;
    if (seat == null) {
      return entry.contest.title;
    }
    return seat === "at-large" || seat === "0"
      ? m.night_state_at_large()
      : m.night_state_seat({ seat });
  }

  function titleOf(entry: Entry) {
    return entry.contest.title;
  }

  function countyHref(unitId: string) {
    const name = counties.get(unitId);
    return name == null
      ? null
      : toLive(`${election.slug}/place/${placeSlug(name)}`);
  }

  function shade(entry: Entry) {
    const { summary } = entry;
    if (summary.later != null) {
      return LATER;
    }
    const pick = summary.winner ?? summary.leader;
    if (pick == null || summary.total === 0) {
      return entry.held == null
        ? WAITING
        : `color-mix(in oklab, ${entry.held.color} 22%, var(--background))`;
    }
    const share =
      summary.winner == null
        ? Math.round(30 + 55 * Math.min(summary.margin / 0.3, 1))
        : 100;
    return `color-mix(in oklab, ${tone(pick)} ${share}%, var(--background))`;
  }

  function before(seats: Seating) {
    const parts = [seats.left, ...seats.rest, seats.right].flatMap((side) =>
      side == null || side.before === 0
        ? []
        : [`${side.short} ${count(side.before)}`],
    );
    return `${m.night_state_before()}: ${parts.join(", ")}`;
  }

  let sheet = $state<Entry | null>(null);
  let isSheet = $state(false);

  function isStrong(entry: Entry) {
    return (
      entry.summary.winner != null ||
      (entry.summary.total > 0 && entry.summary.margin > 0.15)
    );
  }
</script>

<article class="grid gap-px bg-border">
  <header class="grid gap-4 bg-background px-4 pbs-5 pbe-5 sm:px-8">
    <p
      class="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted-foreground"
    >
      <a
        href={toLive(national)}
        class="-ms-1 inline-flex items-center gap-0.5 rounded-xs pe-1 font-semibold text-foreground hover:underline"
        ><ChevronLeftIcon
          class="block-4 inline-4"
          aria-hidden="true"
        />{m.night_state_back()}</a
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
        {:else if later != null}
          {m.night_later()}
        {:else}
          {m.night_status_before()}
        {/if}
      </span>
      {#if election.newest != null}
        <span class="font-figures tabular-nums"
          >{m.night_updated({ time: stamp(election.newest) })}</span
        >
      {/if}
      <BoardLink election={election.slug} />
    </p>
    <h1 class="title text-balance">{ballot.name}</h1>
    <dl class="grid grid-cols-2 gap-x-8 gap-y-3 sm:flex sm:flex-wrap">
      <div class="grid">
        <dt class="text-xs text-muted-foreground">{m.night_state_ballot()}</dt>
        <dd class="font-figures text-2xl font-[760] tabular-nums">
          {count(everything.length + ballot.locals.total)}
        </dd>
      </div>
      <div class="grid">
        <dt class="text-xs text-muted-foreground">
          {m.night_nation_counting()}
        </dt>
        <dd class="font-figures text-2xl font-[760] tabular-nums">
          {count(counting)}<span
            class="text-base font-semibold text-muted-foreground"
          >
            / {count(everything.length)}</span
          >
        </dd>
      </div>
      <div class="grid">
        <dt class="text-xs text-muted-foreground">{m.night_kpi_flips()}</dt>
        <dd class="font-figures text-2xl font-[760] tabular-nums">{flips}</dd>
      </div>
      {#if closes != null}
        <div class="grid">
          <dt class="text-xs text-muted-foreground">{m.night_state_polls()}</dt>
          <dd class="font-figures text-2xl font-[760] tabular-nums">
            {clockOf(closes)}
          </dd>
        </div>
      {/if}
    </dl>
    {#if later != null}
      <Later
        text={m.night_later_state_body({ state: later.name })}
        closes={null}
        time={0}
      />
    {/if}
  </header>

  <nav
    class="sticky inset-bs-0 z-10 overflow-x-auto bg-background/95 px-4 backdrop-blur-sm sm:px-8"
    aria-label={m.night_state_jump()}
  >
    <ul class="flex gap-5 whitespace-nowrap">
      {#each sections as section (section.id)}
        <li>
          <a
            href="#{section.id}"
            class="flex items-baseline gap-1.5 py-2.5 text-sm font-semibold hover:underline"
            >{section.label}<span
              class="font-figures text-xs font-normal text-muted-foreground tabular-nums"
              >{count(section.count)}</span
            ></a
          >
        </li>
      {/each}
    </ul>
  </nav>

  {#if top.length > 0}
    <section
      id="top"
      class="grid scroll-mbs-12 gap-px bg-border lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]"
      aria-label={m.night_state_top()}
    >
      <div class="flex flex-col gap-px bg-border">
        {#each top as entry (entry.contest.id)}
          <Ticket
            {entry}
            href={href(entry)}
            title={titleOf(entry)}
            isMapped={mappable.length > 1 &&
              mapped?.contest.id === entry.contest.id}
          />
        {/each}
        {#if house.length > 0 || chambers.length > 0}
          <div class="grid grow content-start gap-1 bg-background p-4 sm:px-5">
            {#if house.length > 0}
              <a
                href="#house"
                class="grid gap-1.5 rounded-xs py-2 outline-none hover:highlighter focus-visible:highlighter"
              >
                <span
                  class="flex flex-wrap items-baseline justify-between gap-x-3 text-sm font-semibold"
                  >{m.night_state_house()}<span
                    class="font-figures text-xs font-normal text-muted-foreground tabular-nums"
                    >{before(houseSeats)}</span
                  ></span
                >
                <span class="flex flex-wrap gap-0.5" aria-hidden="true">
                  {#each house as entry (entry.contest.id)}
                    <span
                      class="rounded-[1px] block-4 inline-4"
                      style:background-color={shade(entry)}
                      style:background-image={entry.summary.later == null
                        ? undefined
                        : HATCH}
                    ></span>
                  {/each}
                </span>
              </a>
            {/if}
            {#each chambers as chamber (chamber.id)}
              <a
                href="#legislature"
                class="grid gap-1.5 rounded-xs py-2 outline-none hover:highlighter focus-visible:highlighter"
              >
                <span
                  class="flex flex-wrap items-baseline justify-between gap-x-3 text-sm font-semibold"
                  >{chamber.name}<span
                    class="font-figures text-xs font-normal text-muted-foreground tabular-nums"
                    >{before(chamber.seats)}</span
                  ></span
                >
                <Bar seats={chamber.seats} isSmall />
              </a>
            {/each}
          </div>
        {/if}
      </div>
      {#if geography != null && mapped != null}
        {const race = $derived(mapped.summary.race)}
        <div class="grid content-start gap-3 bg-background p-4 sm:px-6">
          {#if views.length > 1}
            <Segmented
              options={views}
              value={mapped.contest.id}
              onValueChange={(value: string) => (picked = value)}
              label={m.night_state_map_label()}
              size="sm"
            />
          {/if}
          {#key race.id}
            <LiveMap
              {geography}
              options={race.options}
              now={standing(race, { at: null, isFinal: true })}
              estimate={estimates[race.id] ?? null}
              modes={["lead", "bubbles", "shift", "counted"]}
              title={m.night_state_map_title({ race: titleOf(mapped) })}
              isLater={mapped.summary.later != null}
              href={countyHref}
            />
          {/key}
        </div>
      {/if}
    </section>
  {/if}

  {#if statewide.length > 0}
    <section
      id="statewide"
      class="grid scroll-mbs-12 gap-3 bg-background px-4 py-5 sm:px-8"
    >
      <h2 class="heading text-lg!">{m.night_state_statewide()}</h2>
      <Rows
        entries={statewide}
        heading={m.night_state_office()}
        label={titleOf}
        {href}
      />
    </section>
  {/if}

  {#if house.length > 0}
    <section
      id="house"
      class="grid scroll-mbs-12 gap-4 bg-background px-4 py-5 sm:px-8"
    >
      <div
        class="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1"
      >
        <h2 class="heading text-lg!">{m.night_state_house()}</h2>
        <p class="font-figures text-xs text-muted-foreground tabular-nums">
          {before(houseSeats)}
        </p>
      </div>
      <div
        class="grid gap-x-10 gap-y-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]"
      >
        <div
          class="grid content-start gap-4 lg:sticky lg:inset-bs-14 lg:self-start"
        >
          {#if ballot.maps.house != null}
            <Districts
              geography={ballot.maps.house}
              entries={house}
              name={m.night_state_house()}
              label={seatLabel}
              {href}
              {shade}
            />
          {/if}
          <ol class="flex flex-wrap gap-1" aria-label={m.night_state_house()}>
            {#each house as entry (entry.contest.id)}
              <li>
                <Peek
                  href={href(entry)}
                  title={seatLabel(entry)}
                  class={cn(
                    "grid place-items-center rounded-xs font-figures text-xs font-semibold tabular-nums block-9 inline-9 hover:outline-2 hover:outline-foreground focus-visible:outline-2 focus-visible:outline-foreground data-[state=open]:outline-2 data-[state=open]:outline-foreground",
                    { "text-background": isStrong(entry) },
                  )}
                  style="background-color: {shade(entry)}"
                  onTouch={() => {
                    sheet = entry;
                    isSheet = true;
                  }}
                >
                  {#snippet card()}
                    <Card
                      summary={entry.summary}
                      heading={seatLabel(entry)}
                      held={entry.held}
                      isFlip={entry.isFlip}
                      closes={closes == null ? null : Date.parse(closes)}
                    />
                  {/snippet}
                  {entry.contest.seat === "at-large"
                    ? "AL"
                    : (entry.contest.seat ?? "")}
                </Peek>
              </li>
            {/each}
          </ol>
          <p class="text-xs text-pretty text-muted-foreground">
            {m.night_state_districts_legend()}
          </p>
        </div>
        <Rows
          entries={house}
          heading={m.night_state_district()}
          label={seatLabel}
          {href}
        />
      </div>
    </section>
  {/if}

  {#if chambers.length > 0}
    <section id="legislature" class="grid scroll-mbs-12 gap-px bg-border">
      <h2 class="bg-background px-4 pbs-5 heading text-lg! sm:px-8">
        {m.night_state_legislature()}
      </h2>
      <div
        class={cn("grid gap-px bg-border", {
          "lg:grid-cols-2": chambers.length > 1,
        })}
      >
        {#each chambers as chamber (chamber.id)}
          <Chamber
            name={chamber.name}
            entries={chamber.entries}
            seats={chamber.seats}
            {href}
            label={seatLabel}
            geography={ballot.maps[chamber.id] ?? null}
            {shade}
          />
        {/each}
      </div>
      <p
        class="bg-background px-4 py-2 text-xs text-pretty text-muted-foreground sm:px-8"
      >
        {m.night_state_striped()}
      </p>
    </section>
  {/if}

  {#if courts.length > 0}
    <section
      id="courts"
      class="grid scroll-mbs-12 gap-3 bg-background px-4 py-5 sm:px-8"
    >
      <h2 class="heading text-lg!">{m.night_state_courts()}</h2>
      <Rows
        entries={courts}
        heading={m.night_state_office()}
        label={titleOf}
        {href}
      />
    </section>
  {/if}

  {#if measures.length > 0}
    <section
      id="measures"
      class="grid scroll-mbs-12 gap-2 bg-background px-4 py-5 sm:px-8"
    >
      <h2 class="heading text-lg!">{m.night_state_measures()}</h2>
      <ul class="grid gap-x-10 lg:grid-cols-2">
        {#each measures as entry (entry.contest.id)}
          <li class="border-be border-border">
            <Measure {entry} href={href(entry)} />
          </li>
        {/each}
      </ul>
    </section>
  {/if}

  {#if ballot.locals.total > 0}
    <div id="local" class="grid scroll-mbs-12">
      <Local
        {election}
        {estimates}
        code={ballot.state}
        name={ballot.name}
        total={ballot.locals.total}
        places={ballot.locals.places}
      />
    </div>
  {/if}
</article>

{#if sheet != null}
  {const entry = $derived(sheet)}
  <Sheet
    bind:isOpen={isSheet}
    title={`${m.night_state_house()}, ${seatLabel(entry)}`}
    href={href(entry)}
    action={m.night_state_results()}
  >
    <Card
      summary={entry.summary}
      held={entry.held}
      isFlip={entry.isFlip}
      closes={closes == null ? null : Date.parse(closes)}
    />
  </Sheet>
{/if}
