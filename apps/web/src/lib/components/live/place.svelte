<script lang="ts">
  import type {
    LiveElection,
    LiveEstimate,
    LiveRace,
  } from "@ea/analytics/live";
  import { page } from "$app/state";
  import type { Geography, Mode } from "#lib/live/standing.js";
  import BoardLink from "#lib/components/live/board-link.svelte";
  import MapLink from "#lib/components/live/map-link.svelte";
  import { count, percent } from "#lib/format.js";
  import {
    isDistricted,
    lead,
    placeName,
    placeScheme,
    placeSlug,
    stamp,
    surname,
    tone,
  } from "#lib/live/format.js";
  import { liveLinks } from "#lib/live/links.js";
  import { remainder, standing, sum } from "#lib/live/standing.js";
  import { closing, summarize } from "#lib/live/summary.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";
  import { isOutlying } from "./full/model.js";
  import Locals from "./locals.svelte";
  import LiveMap from "./map.svelte";

  const {
    election,
    estimates,
    place,
    geography,
    clock: time,
    isOffline = false,
  }: {
    election: LiveElection;
    estimates: Record<string, LiveEstimate>;
    place: { id: string; areaId: string; name: string };
    geography: Geography | null;
    clock: number;
    isOffline?: boolean;
  } = $props();

  const toLive = liveLinks();

  const ORDER = { senate: 0, governor: 1, house: 2, other: 3 };

  let mode = $state<Mode>("lead");
  let selected = $state<string | null>(null);
  let hovered = $state<string | null>(null);

  const name = $derived(placeName(place.name));
  const ballot = $derived(
    election.races
      .filter((race) => race.units.some((unit) => unit.id === place.id))
      .toSorted(
        (a, b) =>
          ORDER[a.office] - ORDER[b.office] ||
          Number.parseInt(a.district ?? "0", 10) -
            Number.parseInt(b.district ?? "0", 10) ||
          a.title.localeCompare(b.title),
      ),
  );
  const locals = $derived(
    ballot.flatMap((race) =>
      race.office === "other"
        ? [summarize(race, estimates[race.id] ?? null)]
        : [],
    ),
  );
  const rows = $derived(
    ballot.flatMap((race) => {
      if (race.office === "other") {
        return [];
      }
      const now = standing(race, { at: null, isFinal: true });
      return [{ race, now, row: now.units.get(place.id) ?? null }];
    }),
  );
  const isSplit = $derived(
    ballot.filter((race) => race.office === "house").length > 1,
  );
  const anchor = $derived(
    rows.find((entry) => entry.race.office === "senate") ??
      rows.find((entry) => entry.race.office === "governor") ??
      rows[0] ??
      null,
  );
  const share = $derived.by(() => {
    if (anchor?.row?.baseline == null) {
      return null;
    }
    const total = sum(
      [...anchor.now.units.values()].map((row) => row.baseline?.turnout ?? 0),
    );
    return total > 0 ? anchor.row.baseline.turnout / total : null;
  });
  const newest = $derived(
    ballot
      .flatMap((race) => race.frames.map((frame) => frame.at))
      .toSorted()
      .at(-1) ?? null,
  );
  const closes = $derived(
    Math.min(...ballot.map((race) => closing(election, race))),
  );
  const status = $derived.by(() => {
    if (ballot.length > 0 && ballot.every((race) => race.certified != null)) {
      return "certified" as const;
    }
    if (isOffline) {
      return "offline" as const;
    }
    if (time < closes && rows.every((entry) => (entry.row?.total ?? 0) === 0)) {
      return "before" as const;
    }
    return "live" as const;
  });

  const counties = $derived(
    new Map(
      election.races.flatMap((race) =>
        race.units.flatMap((unit) =>
          unit.scheme === placeScheme(race.state)
            ? [[unit.id, unit.name] as const]
            : [],
        ),
      ),
    ),
  );

  function href(unitId: string) {
    const found = counties.get(unitId);
    return found == null || unitId === place.id
      ? null
      : toLive(`${election.slug}/place/${placeSlug(found)}`);
  }

  function title(race: LiveRace) {
    if (race.office === "senate") {
      return m.night_nation_senate();
    }
    if (race.office === "governor") {
      return m.night_nation_governor();
    }
    if (race.office === "house" && race.district != null) {
      return race.district === "0"
        ? m.night_nation_house()
        : m.night_rail_house({ district: race.district });
    }
    return race.title;
  }

  function scope(race: LiveRace) {
    if (race.office !== "house") {
      return m.night_place_statewide();
    }
    if (isDistricted(race.state)) {
      return isSplit
        ? m.night_place_part_district()
        : m.night_place_whole_district();
    }
    return isSplit ? m.night_place_part() : m.night_place_whole();
  }
</script>

<article class="grid gap-px bg-border">
  <header class="grid gap-2 bg-background px-4 pbs-4 pbe-4 sm:px-8">
    <p
      class="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted-foreground"
    >
      <span
        class={cn(
          "inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-semibold",
          {
            "bg-foreground text-background": status === "live",
            "border border-border":
              status === "before" || status === "certified",
            "bg-(--no) text-background": status === "offline",
          },
        )}
      >
        {#if status === "live"}
          <span
            class="animate-pulse rounded-full bg-background block-1.5 inline-1.5 motion-reduce:animate-none"
          ></span>
          {m.night_status_live()}
        {:else if status === "before"}
          {m.night_status_before()}
        {:else if status === "certified"}
          {m.night_status_certified()}
        {:else}
          {m.night_status_offline()}
        {/if}
      </span>
      <a
        href={toLive(election.slug)}
        class="font-semibold text-foreground underline-offset-2 hover:underline"
        >{election.title}</a
      >
      {#if newest != null}
        <span class="font-figures tabular-nums"
          >{m.night_updated({ time: stamp(newest) })}</span
        >
      {/if}
      <BoardLink election={election.slug} />
      {#if election.slug.split("/").length === 3}
        <MapLink
          election={election.slug}
          state={election.slug.split("/")[1] ?? ""}
          county={page.url.pathname.split("/").at(-1) ?? null}
        />
      {/if}
    </p>
    <h1 class="title text-balance">{name}</h1>
    {#if share != null}
      <p class="font-serif text-lg/snug text-pretty max-inline-[72ch]">
        {anchor != null && isDistricted(anchor.race.state)
          ? m.night_place_lede_district({
              share: percent(share, share < 0.1 ? 1 : 0),
            })
          : m.night_place_lede({ share: percent(share, share < 0.1 ? 1 : 0) })}
      </p>
    {/if}
  </header>

  <section
    class="grid gap-8 bg-background px-4 py-5 sm:px-8 lg:grid-cols-[minmax(0,1fr)_minmax(0,26rem)] lg:gap-10"
  >
    {#if anchor != null}
      <LiveMap
        {geography}
        isAlone={isOutlying(anchor.race.state)}
        options={anchor.race.options}
        now={anchor.now}
        estimate={estimates[anchor.race.id] ?? null}
        modes={["lead", "shift", "counted"]}
        title={isDistricted(anchor.race.state)
          ? m.night_place_map_district({ race: title(anchor.race) })
          : m.night_place_map({ race: title(anchor.race) })}
        anchor={place.areaId}
        {href}
        bind:mode
        bind:selected
        bind:hovered
      />
    {/if}
    <section class="grid content-start gap-3">
      <h2
        class="content-center text-base font-[720] tracking-tight min-block-8"
      >
        {m.night_place_races()}
      </h2>
      <ul class="grid gap-3">
        {#each rows as entry (entry.race.id)}
          {const row = $derived(entry.row)}
          {const options = $derived(entry.race.options)}
          {const estimate = $derived(estimates[entry.race.id] ?? null)}
          {const rest = $derived(
            row == null
              ? null
              : remainder(row, estimate, options, entry.now.pair),
          )}
          {const counted = $derived(
            row == null || rest == null || row.total === 0
              ? null
              : Math.min(1, row.total / Math.max(1, rest.expected)),
          )}
          {const order = $derived(
            row != null && row.total > 0
              ? row.order
              : options.flatMap((option, index) =>
                  option.isWriteIn ? [] : [index],
                ),
          )}
          <li class="grid gap-2 rounded-sm p-3 ring-1 ring-border">
            <div class="flex items-baseline justify-between gap-3">
              <a
                href={toLive(entry.race.slug)}
                class="text-[0.9375rem] font-semibold underline-offset-2 hover:underline"
                >{title(entry.race)}</a
              >
              <span class="text-xs text-muted-foreground"
                >{scope(entry.race)}</span
              >
            </div>
            <ol class="grid gap-1.5">
              {#each order.slice(0, 4) as index (index)}
                {const votes = $derived(row?.votes[index] ?? 0)}
                {const part = $derived(
                  row == null || row.total === 0 ? null : votes / row.total,
                )}
                <li class="grid gap-1">
                  <span
                    class="flex items-baseline gap-2 font-figures text-[0.8125rem] tabular-nums"
                  >
                    <span
                      class="shrink-0 self-center rounded-full block-2 inline-2"
                      style:background-color={tone(options[index])}
                    ></span>
                    <span
                      class={cn("grow truncate font-sans min-inline-0", {
                        "font-semibold": index === row?.leader,
                      })}>{options[index]?.label ?? ""}</span
                    >
                    {#if part != null}
                      <span class="text-muted-foreground">{count(votes)}</span>
                      <span class="text-end font-semibold min-inline-11"
                        >{percent(part)}</span
                      >
                    {/if}
                  </span>
                  <span
                    class="mis-4 relative overflow-hidden rounded-xs bg-muted block-1"
                    aria-hidden="true"
                  >
                    <span
                      class="absolute inset-y-0 inset-s-0 transition-[inline-size] duration-700 motion-reduce:transition-none"
                      style:inline-size="{(part ?? 0) * 100}%"
                      style:background-color={tone(options[index])}
                    ></span>
                  </span>
                </li>
              {/each}
            </ol>
            <p
              class="flex flex-wrap justify-between gap-x-3 gap-y-0.5 text-xs text-muted-foreground"
            >
              <span class="font-figures tabular-nums"
                >{counted == null
                  ? entry.race.certified == null &&
                    entry.race.feed != null &&
                    entry.race.feed !== "live"
                    ? m.night_later()
                    : m.night_place_waiting()
                  : m.night_place_counted({ share: percent(counted, 0) })}</span
              >
              {#if row?.baseSigned != null}
                {const pair = $derived(entry.now.pair)}
                {const base = $derived(row.baseSigned)}
                <span class="font-figures tabular-nums"
                  >{m.night_place_baseline({
                    margin: `${surname(options[base >= 0 ? pair[0] : pair[1]])} +${lead(base)}`,
                  })}</span
                >
              {/if}
            </p>
          </li>
        {/each}
      </ul>
    </section>
  </section>

  {#if locals.length > 0}
    <Locals summaries={locals} />
  {/if}

  <footer
    class="grid gap-1 bg-background px-4 py-5 text-xs text-muted-foreground sm:px-8"
  >
    <p class="max-inline-[80ch]">
      {anchor != null && isDistricted(anchor.race.state)
        ? m.night_method_district()
        : m.night_method()}
    </p>
  </footer>
</article>
