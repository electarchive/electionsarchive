<script lang="ts">
  import { goto, refreshAll } from "$app/navigation";
  import { resolve } from "$app/paths";
  import { page } from "$app/state";
  import ChevronDownIcon from "@lucide/svelte/icons/chevron-down";
  import { untrack } from "svelte";
  import { count, percent, points } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { getLocale } from "#lib/paraglide/runtime.js";
  import { swatch } from "#lib/party.js";
  import { cn } from "#lib/utils.js";
  import type { PageProps } from "./$types";
  import Board from "./board.svelte";
  import Choropleth from "./map.svelte";
  import Ruler from "./ruler.svelte";

  const { data }: PageProps = $props();

  const night = $derived(data.night);
  const frames = $derived(night.frames);
  const hasFinal = $derived(night.races.some((race) => race.certified != null));

  let position = $state(
    untrack(() => {
      if (data.at === "final" && hasFinal) {
        return frames.length;
      }
      if (data.at == null) {
        return frames.length - 1;
      }
      const wanted = new Date(data.at).getTime();
      return Math.max(
        0,
        frames.findLastIndex((frame) => Date.parse(frame.at) <= wanted),
      );
    }),
  );
  let isPlaying = $state(false);

  const isFinal = $derived(hasFinal && position === frames.length);
  const time = $derived(isFinal ? null : (frames[position]?.at ?? null));
  const newest = $derived(Date.parse(frames.at(-1)?.at ?? "0"));
  const opened = $derived(Date.parse(`${night.election.date}T00:00:00Z`));
  const isLive = $derived(
    !hasFinal &&
      (frames.length > 0
        ? Date.now() - newest < 12 * 3_600_000
        : Date.now() > opened - 12 * 3_600_000 &&
          Date.now() < opened + 48 * 3_600_000),
  );

  const tones = $derived(
    new Map(
      night.races.map((race) => [
        race.id,
        race.options.map((option) => swatch(option.color, option.key)),
      ]),
    ),
  );

  const states = $derived(
    new Map(
      night.races.map((race) => {
        const reached = race.series.filter(
          (point) => time == null || point.at <= time,
        );
        const current = reached.at(-1) ?? null;
        const shown =
          isFinal && race.certified != null
            ? race.certified.votes
            : (current?.votes ?? null);
        return [
          race.id,
          {
            votes: shown,
            previous: isFinal
              ? (current?.votes ?? null)
              : (reached.at(-2)?.votes ?? null),
            isFresh: isFinal ? race.certified != null : current?.at === time,
            isCertified: isFinal && race.certified != null,
          },
        ];
      }),
    ),
  );

  const lead = $derived(
    night.races.find((race) => race.path === night.focus) ??
      ["governor", "mayor", "senate"]
        .map((seat) =>
          night.races.find((race) => race.path.endsWith(`/${seat}`)),
        )
        .find((race) => race != null) ??
      night.races.find((race) => race.group === "executive") ??
      night.races[0],
  );
  const leadTones = $derived(lead == null ? [] : (tones.get(lead.id) ?? []));
  const leadState = $derived(lead == null ? null : states.get(lead.id));
  const ranked = $derived.by(() => {
    const votes = leadState?.votes ?? [];
    let total = 0;
    for (const value of votes) {
      total += value;
    }
    const order = votes
      .map((value, index) => ({ value, index }))
      .toSorted((a, b) => b.value - a.value);
    return { total, order };
  });
  const sentence = $derived.by(() => {
    const first = ranked.order[0];
    const second = ranked.order[1];
    if (lead == null || first == null || ranked.total === 0) {
      return m.live_sentence_waiting();
    }
    const name = lead.options[first.index]?.name ?? "";
    if (leadState?.isCertified && lead.certified?.elected[first.index]) {
      return m.live_sentence_won({
        name,
        share: percent(first.value / ranked.total),
      });
    }
    return m.live_sentence_leads({
      name,
      margin: points(
        (first.value - (second?.value ?? 0)) / ranked.total,
      ).replace(/^\+/u, ""),
      counted: count(ranked.total),
    });
  });
  const certifiedTotal = $derived.by(() => {
    let total = 0;
    for (const value of lead?.certified?.votes ?? []) {
      total += value;
    }
    return total;
  });
  const nightTotal = $derived.by(() => {
    let sum = 0;
    for (const value of lead?.series.at(-1)?.votes ?? []) {
      sum += value;
    }
    return sum;
  });

  const trace = $derived(
    lead == null
      ? []
      : lead.series.flatMap((point) => {
          const order = point.votes
            .map((value, index) => ({ value, index }))
            .toSorted((a, b) => b.value - a.value);
          let total = 0;
          for (const value of point.votes) {
            total += value;
          }
          const first = order[0];
          if (first == null || total === 0) {
            return [];
          }
          return [
            {
              at: point.at,
              margin: (first.value - (order[1]?.value ?? 0)) / total,
              color: leadTones[first.index] ?? "var(--foreground)",
            },
          ];
        }),
  );
  const counts = $derived(
    frames.map(
      (frame) =>
        night.races.filter((race) =>
          race.series.some((point) => point.at === frame.at),
        ).length,
    ),
  );
  const scanners = $derived.by(() => {
    const list = Object.entries(
      Object.fromEntries(
        frames
          .slice(0, position + 1)
          .flatMap((frame) =>
            frame.scanners.map((entry) => [entry.area, entry.share]),
          ),
      ) as Record<string, number>,
    ).map(([area, share]) => ({ area, share }));
    return list.length > 1 &&
      list.every((entry) => entry.share === list[0]?.share)
      ? [{ area: m.live_scanners_all(), share: list[0]?.share ?? 0 }]
      : list;
  });

  const map = $derived.by(() => {
    if (lead == null) {
      return null;
    }
    if (isFinal && lead.certified != null) {
      return { at: null, level: "us.nyc_ed" as const };
    }
    const reached = lead.maps.filter(
      (entry) => time == null || entry.at <= time,
    );
    const finest =
      reached.find((entry) => entry.level === "us.nyc_ed") ??
      reached.find((entry) => entry.level === "us.nyc_ad");
    if (finest != null && time != null) {
      return { at: time, level: finest.level as "us.nyc_ed" | "us.nyc_ad" };
    }
    return time != null && reached.some((entry) => entry.level === "us.county")
      ? { at: time, level: "us.county" as const }
      : null;
  });

  const groups = $derived(
    Map.groupBy(
      night.races
        .filter((race) => race.id !== lead?.id)
        .toSorted((a, b) =>
          a.path.localeCompare(b.path, undefined, { numeric: true }),
        ),
      (race) => race.group,
    ),
  );
  const isCity = $derived(night.election.path.split("/").length > 3);
  const executive = $derived(groups.get("executive") ?? []);
  const senate = $derived(
    (groups.get("federal") ?? []).filter((race) =>
      /\/senate[^/]*$/u.test(race.path),
    ),
  );
  const house = $derived(
    (groups.get("federal") ?? []).filter((race) =>
      /\/house[^/]*$/u.test(race.path),
    ),
  );
  const measures = $derived(groups.get("measure") ?? []);
  const local = $derived([
    ...(groups.get("borough") ?? []),
    ...(groups.get("prosecutor") ?? []),
  ]);
  const courts = $derived(groups.get("judicial") ?? []);

  function chamber(races: typeof night.races) {
    const tiles = races.map((race) => {
      let total = 0;
      let best = 0;
      let index = 0;
      for (const [position, value] of (
        states.get(race.id)?.votes ?? []
      ).entries()) {
        total += value;
        if (value > best) {
          best = value;
          index = position;
        }
      }
      const leader = total === 0 ? null : (race.options[index] ?? null);
      return {
        race,
        leader,
        label: /(\d+[a-z]?)$/u.exec(race.path)?.[1] ?? "AL",
        share: total === 0 ? null : best / total,
        tone:
          leader == null
            ? null
            : (tones.get(race.id)?.[index] ?? "var(--foreground)"),
      };
    });
    return {
      tiles,
      captured: races.filter((race) => states.get(race.id)?.votes != null)
        .length,
      overnight: races.filter((race) => race.series.length > 0).length,
      seats: [
        ...Map.groupBy(
          tiles.filter((tile) => tile.leader != null && tile.tone != null),
          (tile) => tile.leader?.party ?? tile.leader?.name ?? "",
        ),
      ]
        .map(([name, group]) => ({
          name,
          tone: group[0]?.tone ?? "",
          count: group.length,
        }))
        .toSorted((a, b) => b.count - a.count),
    };
  }

  const day = $derived(
    new Intl.DateTimeFormat(getLocale(), {
      dateStyle: "long",
      timeZone: "UTC",
    }).format(new Date(`${night.election.date}T00:00:00Z`)),
  );
  const clock = $derived(
    new Intl.DateTimeFormat(getLocale(), {
      hour: "numeric",
      minute: "2-digit",
      timeZone: night.election.zone,
    }),
  );

  $effect(() => {
    const at = isFinal ? "final" : frames[position]?.at;
    if (at == null || page.url.searchParams.get("at") === at) {
      return;
    }
    untrack(() => {
      void goto(resolve(`/live/${page.params.path ?? ""}?at=${at}`), {
        state: page.state,
        shallow: true,
        replace: true,
      });
    });
  });

  $effect(() => {
    if (!isLive && (hasFinal || frames.length > 0)) {
      return;
    }
    const timer = setInterval(() => void refreshAll(), 30_000);
    return () => clearInterval(timer);
  });
</script>

<svelte:head>
  <title>{m.live_title({ place: night.election.place })} | {m.wordmark()}</title
  >
  <meta
    name="description"
    content={m.live_meta_description({
      place: night.election.place,
      date: day,
    })}
  />
  <link rel="canonical" href="{page.url.origin}{page.url.pathname}" />
  <link
    rel="alternate"
    type="text/markdown"
    href="{page.url.origin}{page.url.pathname}.md"
  />
  <link
    rel="alternate"
    type="application/json"
    href="{page.url.origin}{page.url.pathname}.json"
  />
</svelte:head>

<main class="pbe-40 sm:pbe-16">
  <article class="grid gap-px border-be bg-border">
    <header class="grid gap-2 bg-card px-3.5 pbs-4 pbe-4 sm:px-6">
      <p
        class="flex flex-wrap items-center gap-2 text-sm text-muted-foreground"
      >
        {#if isLive || frames.length > 0}
          <span
            class={cn(
              "inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-semibold",
              {
                "bg-(--no) text-white": isLive,
                "bg-foreground text-background": !isLive,
              },
            )}
          >
            {#if isLive}
              <span
                class="animate-pulse rounded-full bg-white block-1.5 inline-1.5 motion-reduce:animate-none"
              ></span>
            {/if}
            {isLive ? m.live_live() : m.live_replay()}
          </span>
        {/if}
        <a
          href={resolve(`/${night.election.path}`)}
          class="hover:text-foreground hover:underline">{day}</a
        >
      </p>
      <h1 class="title">{m.live_title({ place: night.election.place })}</h1>
      {#if !isLive && frames.length > 0}
        <p class="text-[0.9375rem] text-muted-foreground max-inline-[68ch]">
          {m.live_replay_note()}
        </p>
      {/if}
    </header>

    {#if frames.length > 0}
      <div
        class="bg-card/95 px-3.5 py-2.5 backdrop-blur-md sm:sticky sm:inset-bs-0 sm:z-20 sm:px-6 sm:py-3 max-sm:fixed max-sm:inset-be-0 max-sm:z-30 max-sm:border-bs max-sm:border-border max-sm:inline-full"
      >
        <Ruler
          {frames}
          bind:position
          bind:isPlaying
          {hasFinal}
          zone={night.election.zone}
          {trace}
          {counts}
        />
      </div>
    {/if}

    {#if lead != null && leadState != null}
      <section
        aria-labelledby="live-lead"
        class="grid gap-x-10 gap-y-6 bg-card px-3.5 pbs-6 pbe-7 sm:px-6 lg:grid-cols-[minmax(0,5fr)_minmax(0,6fr)] lg:grid-rows-[auto_1fr]"
      >
        <div class="grid content-start gap-5">
          <div class="grid gap-2">
            <h2 id="live-lead" class="heading">
              <a href={resolve(`/${lead.path}`)} class="hover:underline"
                >{lead.name}</a
              >
            </h2>
            {#key sentence}
              <p
                class="animate-[settle_420ms_ease-out_both] font-serif text-[1.375rem] leading-snug text-balance motion-reduce:animate-none"
              >
                {sentence}
              </p>
            {/key}
          </div>
          <Board
            race={lead}
            tones={leadTones}
            votes={leadState.votes}
            previous={leadState.previous}
            isCertified={leadState.isCertified}
            isFresh={leadState.isFresh}
            ghost={hasFinal}
            variant="lead"
          />
          <div class="grid gap-4 border-bs border-border pbs-4">
            {#if !isFinal}
              <div class="grid gap-2">
                <h3 class="text-sm font-semibold">{m.live_scanners()}</h3>
                {#if scanners.length === 0}
                  <p class="text-[0.8125rem] text-muted-foreground">
                    {m.live_scanners_none()}
                  </p>
                {:else}
                  <ul class="grid gap-1.5">
                    {#each scanners as entry (entry.area)}
                      <li
                        class="grid grid-cols-[7.5rem_minmax(0,1fr)_3.5rem] items-center gap-3 text-[0.8125rem]"
                      >
                        <span class="truncate">{entry.area}</span>
                        <span class="relative rounded-xs bg-muted block-1.5">
                          <span
                            class="absolute inset-s-0 inset-bs-0 inset-be-0 rounded-xs bg-foreground transition-[width] duration-700 ease-out motion-reduce:transition-none"
                            style:width="{entry.share}%"
                          ></span>
                        </span>
                        <span class="text-end font-figures tabular-nums"
                          >{percent(entry.share / 100)}</span
                        >
                      </li>
                    {/each}
                  </ul>
                {/if}
                <p class="text-xs text-muted-foreground max-inline-[60ch]">
                  {m.live_scanners_note()}
                </p>
              </div>
            {/if}
            {#if isFinal && certifiedTotal > nightTotal && nightTotal > 0}
              <p
                class="text-[0.8125rem] text-muted-foreground max-inline-[60ch]"
              >
                {m.live_after_night({
                  count: count(certifiedTotal - nightTotal),
                  share: percent(
                    (certifiedTotal - nightTotal) / certifiedTotal,
                  ),
                })}
              </p>
            {/if}
            {#if certifiedTotal > 0 && ranked.total > 0 && !isFinal}
              <p class="text-xs text-muted-foreground max-inline-[60ch]">
                {m.live_of_certified({
                  share: percent(ranked.total / certifiedTotal),
                  count: count(certifiedTotal),
                })}
              </p>
            {/if}
          </div>
        </div>
        <div class="grid content-start gap-2 lg:row-span-2 lg:content-stretch">
          {#if map != null}
            <Choropleth
              contest={lead.id}
              options={lead.options}
              tones={leadTones}
              at={map.at}
              level={map.level}
            />
          {:else}
            <div
              class="grid aspect-[4/3.4] place-items-center rounded-sm bg-muted/40 text-sm text-muted-foreground ring-1 ring-border lg:aspect-auto lg:min-block-120"
            >
              {m.live_map_waiting()}
            </div>
          {/if}
        </div>
        {#if executive.length > 0}
          <div
            class="grid content-start gap-4 border-bs border-border pbs-5 lg:col-start-1"
          >
            <h2 id="live-citywide" class="heading">
              {isCity ? m.live_citywide() : m.live_statewide()}
            </h2>
            <ul class="grid gap-x-8 gap-y-6 sm:grid-cols-2">
              {#each executive as race (race.id)}
                {const state = $derived(states.get(race.id))}
                <li class="grid content-start gap-1">
                  <Board
                    {race}
                    tones={tones.get(race.id) ?? []}
                    votes={state?.votes ?? null}
                    previous={state?.previous ?? null}
                    isCertified={state?.isCertified ?? false}
                    isFresh={state?.isFresh ?? false}
                    ghost={hasFinal}
                    variant="compact"
                  />
                  {#if race.outside}
                    <p class="text-xs text-muted-foreground">
                      {m.live_city_only()}
                    </p>
                  {/if}
                </li>
              {/each}
            </ul>
          </div>
        {/if}
      </section>
    {:else if night.races.length === 0}
      <section class="bg-card px-3.5 pbs-8 pbe-10 sm:px-6">
        <p
          class="font-serif text-[1.375rem] leading-snug text-balance max-inline-[40ch]"
        >
          {m.live_waiting_night()}
        </p>
      </section>
    {/if}

    {#snippet boards(
      heading: string,
      id: string,
      races: typeof night.races,
      variant: "compact" | "measure",
    )}
      {#if races.length > 0}
        <section
          aria-labelledby={id}
          class="grid gap-4 bg-card px-3.5 pbs-5 pbe-6 sm:px-6"
        >
          <h2 {id} class="heading">{heading}</h2>
          <ul
            class={cn("grid gap-x-10 gap-y-6", {
              "sm:grid-cols-2 lg:grid-cols-3": variant === "compact",
              "sm:grid-cols-2": variant === "measure",
            })}
          >
            {#each races as race (race.id)}
              {const state = $derived(states.get(race.id))}
              <li class="grid content-start gap-1">
                <Board
                  {race}
                  tones={tones.get(race.id) ?? []}
                  votes={state?.votes ?? null}
                  previous={state?.previous ?? null}
                  isCertified={state?.isCertified ?? false}
                  isFresh={state?.isFresh ?? false}
                  ghost={hasFinal}
                  {variant}
                />
                {#if race.outside}
                  <p class="text-xs text-muted-foreground">
                    {m.live_city_only()}
                  </p>
                {/if}
              </li>
            {/each}
          </ul>
        </section>
      {/if}
    {/snippet}

    {#snippet chamberGrid(
      heading: string,
      id: string,
      races: typeof night.races,
    )}
      {#if races.length > 0}
        {const view = $derived(chamber(races))}
        <section
          aria-labelledby={id}
          class="grid gap-3 bg-card px-3.5 pbs-5 pbe-6 sm:px-6"
        >
          <div
            class="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1"
          >
            <h2 {id} class="heading">{heading}</h2>
            <p class="text-[0.8125rem] text-muted-foreground tabular-nums">
              {isFinal
                ? m.live_council_note_final({
                    captured: String(view.overnight),
                    total: String(races.length),
                  })
                : m.live_council_note({
                    captured: String(view.captured),
                    total: String(races.length),
                  })}
            </p>
          </div>
          <ol
            class="grid grid-cols-[repeat(auto-fill,minmax(3.25rem,1fr))] gap-1"
          >
            {#each view.tiles as tile (tile.race.id)}
              <li>
                <a
                  href={resolve(`/${tile.race.path}`)}
                  title={tile.leader == null || tile.share == null
                    ? `${tile.race.name}: ${m.live_no_update()}`
                    : `${tile.race.name}: ${tile.leader.name} ${percent(tile.share)}`}
                  class={cn(
                    "grid aspect-square place-items-center rounded-xs font-figures text-[0.8125rem] font-semibold tabular-nums transition-colors duration-500 motion-reduce:transition-none",
                    {
                      "text-white": tile.tone != null,
                      "bg-[repeating-linear-gradient(135deg,var(--muted)_0_3px,var(--card)_3px_6px)] text-muted-foreground ring-1 ring-border ring-inset":
                        tile.tone == null,
                    },
                  )}
                  style:background-color={tile.tone == null
                    ? undefined
                    : `color-mix(in oklab, ${tile.tone} ${Math.round(45 + 55 * Math.min(((tile.share ?? 0.5) - 0.5) / 0.4, 1))}%, var(--foreground))`}
                >
                  {tile.label}
                </a>
              </li>
            {/each}
          </ol>
          <ul
            class="flex flex-wrap items-center gap-x-5 gap-y-1.5 text-[0.8125rem] text-muted-foreground"
          >
            {#each view.seats as seat (seat.name)}
              <li class="flex items-center gap-1.5">
                <span
                  class="rounded-xs block-3 inline-3"
                  style:background-color={seat.tone}
                  aria-hidden="true"
                ></span>
                <span class="text-foreground">{seat.name}</span>
                <span
                  class="font-figures font-semibold text-foreground tabular-nums"
                  >{seat.count}</span
                >
              </li>
            {/each}
            {#if view.captured < races.length || view.seats.length === 0}
              <li class="flex items-center gap-1.5">
                <span
                  class="rounded-xs bg-[repeating-linear-gradient(135deg,var(--muted)_0_3px,var(--card)_3px_6px)] ring-1 ring-border block-3 inline-3 ring-inset"
                  aria-hidden="true"
                ></span>
                {m.live_council_legend_none()}
              </li>
            {/if}
            {#if view.seats.length > 0}
              <li>{m.live_council_legend_shade()}</li>
            {/if}
          </ul>
        </section>
      {/if}
    {/snippet}

    {#if lead == null || leadState == null}
      {@render boards(
        isCity ? m.live_citywide() : m.live_statewide(),
        "live-citywide",
        executive,
        "compact",
      )}
    {/if}
    {@render boards(m.live_senate(), "live-senate", senate, "compact")}
    {@render chamberGrid(m.live_house(), "live-house", house)}
    {@render boards(m.live_measures(), "live-measures", measures, "measure")}
    {@render boards(m.live_boroughs(), "live-boroughs", local, "compact")}
    {@render chamberGrid(
      m.live_council(),
      "live-council",
      groups.get("council") ?? [],
    )}
    {@render chamberGrid(
      m.live_legislature(),
      "live-legislature",
      groups.get("legislative") ?? [],
    )}
    {@render boards(
      m.live_other(),
      "live-other",
      groups.get("other") ?? [],
      "compact",
    )}

    {#if courts.length > 0}
      <section class="bg-card px-3.5 pbs-4 pbe-5 sm:px-6">
        <details class="group">
          <summary
            class="flex cursor-pointer list-none items-baseline justify-between gap-3 heading"
          >
            {m.live_courts()}
            <span
              class="flex items-center gap-1.5 font-figures text-sm font-normal text-muted-foreground tabular-nums"
              >{count(courts.length)}
              <ChevronDownIcon
                class="transition-transform duration-200 block-4 inline-4 group-open:rotate-180 motion-reduce:transition-none"
                aria-hidden="true"
              /></span
            >
          </summary>
          <ul class="grid gap-x-10 gap-y-6 pbs-4 sm:grid-cols-2 lg:grid-cols-3">
            {#each courts as race (race.id)}
              {const state = $derived(states.get(race.id))}
              <li>
                <Board
                  {race}
                  tones={tones.get(race.id) ?? []}
                  votes={state?.votes ?? null}
                  previous={state?.previous ?? null}
                  isCertified={state?.isCertified ?? false}
                  isFresh={state?.isFresh ?? false}
                  ghost={hasFinal}
                  variant="compact"
                />
              </li>
            {/each}
          </ul>
        </details>
      </section>
    {/if}

    {#if night.source.publisher !== ""}
      <footer
        class="flex flex-wrap gap-x-6 gap-y-1 bg-card px-3.5 py-3 text-xs text-muted-foreground sm:px-6"
      >
        {#if night.source.url == null}
          <span>{m.live_source({ publisher: night.source.publisher })}</span>
        {:else}
          <a href={night.source.url} class="hover:underline" rel="external"
            >{m.live_source({ publisher: night.source.publisher })}</a
          >
        {/if}
        {#if time != null}
          <span>{m.live_as_of({ time: clock.format(Date.parse(time)) })}</span>
        {/if}
      </footer>
    {/if}
  </article>
</main>

<style>
  @keyframes -global-settle {
    from {
      opacity: 0;
      transform: translateY(0.25rem);
    }
  }
</style>
