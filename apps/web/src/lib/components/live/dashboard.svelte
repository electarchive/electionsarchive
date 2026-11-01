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
  import type { Geography } from "#lib/live/standing.js";
  import type { Summary } from "#lib/live/summary.js";
  import { percent } from "#lib/format.js";
  import {
    clock as clockOf,
    lead,
    stamp,
    surname,
    tone,
    zoneName,
  } from "#lib/live/format.js";
  import { party, summarize, tally } from "#lib/live/summary.js";
  import { ZONES } from "#lib/live/zones.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";
  import Hexes from "./hexes.svelte";
  import Nation from "./nation.svelte";

  const {
    election,
    estimates,
    nation,
    house,
    clock,
    isOffline,
  }: {
    election: LiveElection;
    estimates: Record<string, LiveEstimate>;
    nation: {
      map: Geography;
      codes: Record<string, string>;
      labels: Record<string, string>;
    } | null;
    house: {
      map: Geography | null;
      codes: Record<string, string>;
      cartogram: {
        box: [number, number, number, number];
        districts: Array<{ key: string; d: string; x: number; y: number }>;
        states: Array<string>;
        labels: Array<{ x: number; y: number; text: string }>;
      } | null;
    } | null;
    clock: number;
    isOffline: boolean;
  } = $props();

  type View = "senate" | "governor" | "house" | "reporting";

  let view = $state<View>("senate");
  let hovered = $state<string | null>(null);
  let layout = $state<"shapes" | "hexes">("shapes");
  let probe = $state<string | null>(null);

  const VIEWS: Array<{ id: View; label: () => string }> = [
    { id: "senate", label: m.night_nation_senate },
    { id: "governor", label: m.night_nation_governor },
    { id: "house", label: m.night_nation_house },
    { id: "reporting", label: m.night_nation_reporting },
  ];
  const MUTED =
    "color-mix(in oklab, var(--muted-foreground) 7%, var(--background))";
  const WAITING =
    "color-mix(in oklab, var(--muted-foreground) 20%, var(--background))";

  const summaries = $derived(
    election.races.map((race) => summarize(race, estimates[race.id] ?? null)),
  );
  const byState = $derived(
    Map.groupBy(summaries, (summary) => summary.race.state),
  );
  const codes = $derived(
    [...byState.keys()].toSorted((a, b) =>
      (nation?.labels[a] ?? a).localeCompare(nation?.labels[b] ?? b),
    ),
  );
  const office = $derived<LiveOffice>(view === "reporting" ? "senate" : view);
  const counting = $derived(
    summaries.filter((summary) => summary.total > 0).length,
  );
  const certified = $derived(
    summaries.filter((summary) => summary.race.certified != null).length,
  );
  const statesIn = $derived(
    codes.filter((code) =>
      (byState.get(code) ?? []).some((summary) => summary.total > 0),
    ).length,
  );
  const newest = $derived(election.newest);
  const isLive = $derived(counting > 0 && !election.isCertified);

  function of(code: string, wanted: LiveOffice) {
    return (byState.get(code) ?? []).filter(
      (summary) => summary.race.office === wanted,
    );
  }

  function strength(summary: Summary) {
    const option = summary.winner ?? summary.leader;
    if (option == null) {
      return WAITING;
    }
    const share =
      summary.winner == null
        ? Math.round(30 + 60 * Math.min(summary.margin / 0.3, 1))
        : 100;
    return `color-mix(in oklab, ${tone(option)} ${share}%, var(--background))`;
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
      if (view === "reporting") {
        const share = reported(code);
        found[code] =
          share == null
            ? MUTED
            : `color-mix(in oklab, var(--foreground) ${Math.round(10 + 78 * share)}%, var(--background))`;
        continue;
      }
      const races = of(code, office);
      if (races.length === 0) {
        found[code] = MUTED;
        continue;
      }
      if (view === "house") {
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
      found[code] = first == null ? MUTED : strength(first);
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
      [...districts].map(([key, summary]) => [key, strength(summary)]),
    ),
  );
  const hasShapes = $derived(house?.map != null);
  const hasHexes = $derived(house?.cartogram != null);
  const isHexes = $derived(
    (layout === "hexes" && hasHexes) || (!hasShapes && hasHexes),
  );

  function districtHref(key: string) {
    const summary = districts.get(key);
    return summary == null ? null : raceHref(summary);
  }

  const balance = $derived(tally(summaries, office));
  const totalRaces = $derived(
    summaries.filter((summary) => summary.race.office === office).length,
  );
  const closest = $derived(
    summaries
      .filter(
        (summary) =>
          summary.race.office === office &&
          summary.total > 0 &&
          summary.race.certified == null,
      )
      .toSorted((a, b) => a.margin - b.margin)
      .slice(0, 6),
  );
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

  function href(code: string): string {
    return resolve("/live/[...path]", {
      path: `${election.slug.split("/")[0]}/${code}/${election.slug.split("/")[1]}`,
    });
  }

  function raceHref(summary: Summary) {
    return resolve("/live/[...path]", { path: summary.race.slug });
  }

  function name(code: string) {
    return nation?.labels[code] ?? code.toUpperCase();
  }
</script>

{#snippet chip(summary: Summary | undefined)}
  {#if summary == null}
    <span class="text-muted-foreground">–</span>
  {:else if summary.leader == null}
    <span class="text-muted-foreground">{m.night_nation_waiting()}</span>
  {:else}
    {const option = $derived(summary.winner ?? summary.leader)}
    <span class="inline-flex items-center gap-1.5">
      <span
        class="rounded-full block-2 inline-2"
        style:background-color={tone(option)}
      ></span>
      <span class={cn({ "font-semibold": summary.winner != null })}
        >{surname(option)}</span
      >
      <span class="font-figures text-muted-foreground tabular-nums"
        >+{lead(summary.margin)}</span
      >
    </span>
  {/if}
{/snippet}

{#snippet tip(code: string)}
  {const races = $derived(
    view === "reporting" ? (byState.get(code) ?? []) : of(code, office),
  )}
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
      <span class="flex items-center justify-between gap-3">
        <span
          >{summary.race.office === "senate"
            ? m.night_nation_senate()
            : summary.race.office === "governor"
              ? m.night_nation_governor()
              : summary.race.title}</span
        >
        {@render chip(summary)}
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

{#snippet district(key: string)}
  {const summary = $derived(districts.get(key))}
  {#if summary == null}
    <span class="text-sm font-semibold">{key.toUpperCase()}</span>
    <span class="text-muted-foreground">{m.night_nation_none()}</span>
  {:else}
    <span class="text-sm font-semibold"
      >{name(summary.race.state)}, {summary.race.district === "0"
        ? m.night_nation_house()
        : m.night_rail_house({ district: summary.race.district ?? "" })}</span
    >
    {@render chip(summary)}
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
    </p>
    <h1 class="title text-balance">{m.night_nation_title()}</h1>
    <dl class="flex flex-wrap gap-x-8 gap-y-2">
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
      <div class="grid">
        <dt class="text-xs text-muted-foreground">
          {m.night_nation_certified()}
        </dt>
        <dd class="font-figures text-2xl font-[760] tabular-nums">
          {certified}
        </dd>
      </div>
    </dl>
  </header>

  <section
    class="grid gap-6 bg-background px-4 py-6 sm:px-8 lg:grid-cols-[minmax(0,1fr)_22rem] lg:gap-10"
  >
    <div class="grid content-start gap-3">
      <div class="flex flex-wrap items-center justify-between gap-2">
        <div
          role="radiogroup"
          aria-label={m.night_nation_views()}
          class="flex flex-wrap gap-1"
        >
          {#each VIEWS as entry (entry.id)}
            <button
              type="button"
              role="radio"
              aria-checked={view === entry.id}
              class={cn(
                "rounded-xs px-2.5 py-1 text-[0.8125rem] font-semibold transition-colors",
                {
                  "bg-primary text-primary-foreground": view === entry.id,
                  "text-muted-foreground hover:highlighter hover:text-foreground":
                    view !== entry.id,
                },
              )}
              onclick={() => (view = entry.id)}>{entry.label()}</button
            >
          {/each}
        </div>
        {#if view === "house" && hasShapes && hasHexes}
          <div
            role="radiogroup"
            aria-label={m.night_house_layout()}
            class="flex gap-0.5 rounded-sm p-0.5 ring-1 ring-border"
          >
            {#each [{ id: "shapes" as const, label: m.night_house_shapes(), Icon: MapIcon }, { id: "hexes" as const, label: m.night_house_hexes(), Icon: HexagonIcon }] as entry (entry.id)}
              <button
                type="button"
                role="radio"
                aria-checked={layout === entry.id}
                class={cn(
                  "inline-flex items-center gap-1.5 rounded-xs px-2 py-0.5 text-[0.8125rem] font-semibold transition-colors",
                  {
                    "bg-secondary text-foreground": layout === entry.id,
                    "text-muted-foreground hover:text-foreground":
                      layout !== entry.id,
                  },
                )}
                onclick={() => (layout = entry.id)}
                ><entry.Icon
                  class="block-3.5 inline-3.5"
                  aria-hidden="true"
                />{entry.label}</button
              >
            {/each}
          </div>
        {/if}
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
        <div
          class="aspect-[1.6] animate-pulse rounded-sm bg-muted inline-full motion-reduce:animate-none"
        ></div>
      {:else}
        <Nation
          geography={nation.map}
          codes={nation.codes}
          {colors}
          label={m.night_nation_map({
            view: VIEWS.find((entry) => entry.id === view)?.label() ?? "",
          })}
          bind:hovered
          onPick={async (code: string) => goto(href(code))}
          {tip}
        />
      {/if}
      <p class="text-xs text-muted-foreground">
        {view === "reporting"
          ? m.night_nation_legend_reporting()
          : view === "house" && house != null && (hasShapes || hasHexes)
            ? m.night_house_legend()
            : m.night_nation_legend()}
      </p>
    </div>

    <aside class="grid content-start gap-6">
      {#if view !== "reporting"}
        <section class="grid gap-2">
          <h2 class="heading text-lg!">
            {VIEWS.find((entry) => entry.id === view)?.label()}
          </h2>
          <div
            class="flex overflow-hidden rounded-xs bg-muted block-5"
            aria-hidden="true"
          >
            {#each balance.groups as group (party(group.option))}
              <span
                class="transition-[inline-size] duration-700 block-full motion-reduce:transition-none"
                style:inline-size="{(group.won / Math.max(1, totalRaces)) *
                  100}%"
                style:background-color={tone(group.option)}
              ></span>
              <span
                class="transition-[inline-size] duration-700 block-full motion-reduce:transition-none"
                style:inline-size="{(group.leading / Math.max(1, totalRaces)) *
                  100}%"
                style:background-color="color-mix(in oklab, {tone(group.option)} 45%,
                var(--background))"
              ></span>
            {/each}
          </div>
          <ul class="grid gap-1 text-[0.8125rem]">
            {#each balance.groups as group (party(group.option))}
              <li class="flex items-center justify-between gap-3">
                <span class="flex items-center gap-1.5"
                  ><span
                    class="rounded-full block-2.5 inline-2.5"
                    style:background-color={tone(group.option)}
                  ></span>{group.option.party?.name ??
                    surname(group.option)}</span
                >
                <span class="font-figures tabular-nums"
                  >{m.night_nation_balance({
                    won: group.won,
                    leading: group.leading,
                  })}</span
                >
              </li>
            {/each}
            <li
              class="flex items-center justify-between gap-3 text-muted-foreground"
            >
              <span>{m.night_nation_waiting()}</span>
              <span class="font-figures tabular-nums">{balance.waiting}</span>
            </li>
          </ul>
        </section>
        <section class="grid gap-2">
          <h2 class="heading text-lg!">{m.night_nation_closest()}</h2>
          {#if closest.length === 0}
            <ul class="grid gap-2" aria-hidden="true">
              {#each { length: 4 } as _, index (index)}
                <li
                  class="animate-pulse rounded-xs bg-muted block-9 motion-reduce:animate-none"
                ></li>
              {/each}
            </ul>
          {:else}
            <ul class="grid">
              {#each closest as summary (summary.race.id)}
                <li>
                  <a
                    href={raceHref(summary)}
                    class="grid gap-1 border-be border-border px-1 py-2 hover:highlighter"
                  >
                    <span
                      class="flex items-baseline justify-between gap-3 text-[0.8125rem]"
                    >
                      <span class="font-semibold"
                        >{name(summary.race.state)}{summary.race.district ==
                        null
                          ? ""
                          : ` ${summary.race.district}`}</span
                      >
                      {@render chip(summary)}
                    </span>
                    <span
                      class="relative flex overflow-hidden rounded-xs bg-muted block-1.5"
                      aria-hidden="true"
                    >
                      <span
                        class="block-full"
                        style:inline-size="{(summary.counted ?? 0) * 100}%"
                        style:background-color="var(--foreground)"
                      ></span>
                    </span>
                    <span
                      class="flex justify-between text-xs text-muted-foreground"
                    >
                      <span
                        >{summary.counted == null
                          ? ""
                          : m.night_tip_counted({
                              share: percent(summary.counted, 0),
                            })}</span
                      >
                      {#if summary.chance != null && summary.favorite != null}
                        <span class="font-figures tabular-nums"
                          >{m.night_nation_chance({
                            name: surname(summary.favorite),
                            chance: percent(summary.chance, 0),
                          })}</span
                        >
                      {/if}
                    </span>
                  </a>
                </li>
              {/each}
            </ul>
          {/if}
        </section>
      {/if}
      <section class="grid gap-2">
        <div class="grid gap-0.5">
          <h2 class="heading text-lg!">{m.night_nation_closings()}</h2>
          {#if closings[0] != null}
            <p class="text-xs text-muted-foreground">
              {m.night_closings_zone({ zone: zoneName(closings[0].at) })}
            </p>
          {/if}
        </div>
        <ol class="grid gap-1.5">
          {#each closings as closing (closing.at)}
            {const isPast = $derived(Date.parse(closing.at) <= clock)}
            <li
              class={cn(
                "grid grid-cols-[5rem_1fr] items-baseline gap-2 text-[0.8125rem]",
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
    </aside>
  </section>

  <section class="grid gap-3 bg-background px-4 py-6 sm:px-8">
    <h2 class="heading text-lg!">{m.night_nation_table()}</h2>
    <div class="relative overflow-x-auto">
      <table class="border-collapse text-[0.8125rem] inline-full">
        <thead>
          <tr class="border-be border-foreground text-start">
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
        <tbody>
          {#each codes as code (code)}
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
              onpointerenter={() => (hovered = code)}
              onpointerleave={() => (hovered = null)}
              onclick={async () => goto(href(code))}
            >
              <th scope="row" class="py-1.5 ps-1 text-start font-semibold"
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
                  <span class="font-figures text-muted-foreground tabular-nums"
                    >{share == null ? "–" : percent(share, 0)}</span
                  >
                </span>
              </td>
              <td class="py-1.5">{@render chip(of(code, "senate")[0])}</td>
              <td class="py-1.5">{@render chip(of(code, "governor")[0])}</td>
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
                        href={raceHref(seat)}
                        class="rounded-[1px] block-2.5 inline-2.5 hover:outline-1 hover:outline-foreground"
                        style:background-color={seat.leader == null
                          ? WAITING
                          : strength(seat)}
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
        </tbody>
      </table>
    </div>
  </section>
</article>
