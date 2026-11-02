<script lang="ts">
  import type { LiveOption, LiveRace } from "@ea/analytics/live";
  import type { Summary } from "#lib/live/summary.js";
  import { isHover } from "#lib/explain.svelte.js";
  import { percent } from "#lib/format.js";
  import { HATCH, lead, surname, tone } from "#lib/live/format.js";
  import { liveLinks } from "#lib/live/links.js";
  import { shade, tally, verdict } from "#lib/live/summary.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";
  import RaceIndex from "./race-index.svelte";

  const {
    summaries,
    current = null,
    counties = [],
  }: {
    summaries: Array<Summary>;
    current?: string | null;
    counties?: Array<{
      id: string;
      name: string;
      href: string;
      leader: LiveOption | null;
      margin: number;
      counted: number | null;
    }>;
  } = $props();

  const toLive = liveLinks();

  const houses = $derived(
    summaries.filter((entry) => entry.race.office === "house"),
  );
  const isGrid = $derived(houses.length > 1);
  const cards = $derived(
    isGrid
      ? summaries.filter((entry) => entry.race.office !== "house")
      : summaries,
  );
  const seats = $derived(tally(houses, "house"));
  const isMany = $derived(cards.length > 6);

  let probed = $state<string | null>(null);

  const shown = $derived(
    houses.find((entry) => entry.race.slug === probed) ??
      houses.find((entry) => entry.race.slug === current) ??
      null,
  );

  function title(race: LiveRace) {
    if (race.office === "senate") {
      return m.night_nation_senate();
    }
    if (race.office === "governor") {
      return m.night_nation_governor();
    }
    if (race.office === "house") {
      return race.district == null
        ? m.night_nation_house()
        : m.night_rail_house({ district: race.district });
    }
    return race.title;
  }
</script>

{#snippet meter(counted: number | null)}
  <span
    class="relative overflow-hidden rounded-xs bg-muted block-1"
    aria-hidden="true"
  >
    <span
      class="absolute inset-y-0 inset-s-0 bg-foreground transition-[inline-size] duration-700 motion-reduce:transition-none"
      style:inline-size="{(counted ?? 0) * 100}%"
    ></span>
  </span>
{/snippet}

{#snippet chip(option: LiveOption | null, text: string, isStrong: boolean)}
  <span class="flex items-center gap-1.5 text-xs min-inline-0">
    <span
      class="shrink-0 rounded-full block-2 inline-2"
      style:background-color={option == null ? "var(--border)" : tone(option)}
    ></span>
    <span
      class={cn("truncate", {
        "font-semibold": isStrong,
        "text-muted-foreground": option == null,
      })}>{text}</span
    >
  </span>
{/snippet}

<nav
  aria-label={m.night_rail_label()}
  class={cn(
    "grid gap-x-8 gap-y-4 border-be border-border bg-background px-4 py-3.5 sm:px-8",
    { "lg:grid-cols-[minmax(0,auto)_minmax(0,1fr)]": !isMany },
  )}
>
  {#if isMany}
    <RaceIndex summaries={cards} {current} />
  {:else if cards.length > 0}
    <ul
      class="grid grid-cols-2 content-start gap-2 sm:grid-cols-[repeat(auto-fill,minmax(10rem,1fr))] lg:flex lg:flex-wrap"
    >
      {#each cards as entry (entry.race.id)}
        {const isCurrent = $derived(entry.race.slug === current)}
        {const option = $derived(entry.winner ?? entry.leader)}
        <li class="lg:inline-40">
          <a
            href={toLive(entry.race.slug)}
            aria-current={isCurrent ? "page" : undefined}
            class={cn(
              "grid gap-1.5 rounded-sm px-3 py-2 ring-1 ring-border transition-colors hover:bg-secondary",
              { "ring-2 ring-foreground": isCurrent },
            )}
          >
            <span class="truncate text-[0.8125rem] font-semibold"
              >{title(entry.race)}</span
            >
            {@render chip(
              option,
              option == null
                ? verdict(entry)
                : `${surname(option)} +${lead(entry.margin)}`,
              entry.winner != null,
            )}
            {#if entry.later == null}
              {@render meter(entry.counted)}
            {/if}
          </a>
        </li>
      {/each}
    </ul>
  {/if}

  {#if isGrid}
    <div class="grid content-start gap-2 min-inline-0">
      <div
        class="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 min-block-5"
      >
        <span class="text-[0.8125rem] font-semibold"
          >{m.night_races_house({ count: houses.length })}</span
        >
        <span
          class="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs"
          aria-live="polite"
        >
          {#if shown == null}
            {#each seats.groups as group (group.option.id)}
              {@render chip(
                group.option,
                `${group.option.party?.short ?? surname(group.option)} ${group.won + group.leading}`,
                true,
              )}
            {/each}
            {#if seats.waiting > 0}
              {@render chip(
                null,
                m.night_races_waiting({ count: seats.waiting }),
                false,
              )}
            {/if}
            {#if seats.later > 0}
              {@render chip(
                null,
                m.night_races_later({ count: seats.later }),
                false,
              )}
            {/if}
          {:else}
            <span class="font-semibold">{title(shown.race)}</span>
            {@render chip(
              shown.winner ?? shown.leader,
              verdict(shown),
              shown.winner != null,
            )}
            {#if shown.counted != null}
              <span class="font-figures text-muted-foreground tabular-nums"
                >{m.night_races_counted({
                  share: percent(shown.counted, 0),
                })}</span
              >
            {/if}
          {/if}
        </span>
      </div>
      <ol
        class="grid grid-cols-[repeat(auto-fill,minmax(2.25rem,1fr))] gap-1"
        onpointerleave={() => (probed = null)}
      >
        {#each houses as entry (entry.race.id)}
          {const isCurrent = $derived(entry.race.slug === current)}
          {const color = $derived(shade(entry))}
          <li>
            <a
              href={toLive(entry.race.slug)}
              aria-current={isCurrent ? "page" : undefined}
              aria-label="{title(entry.race)}: {verdict(entry)}"
              class={cn(
                "grid place-items-center rounded-xs font-figures text-xs font-semibold tabular-nums transition-[background-color,box-shadow] duration-500 block-8 motion-reduce:transition-none",
                {
                  "bg-muted text-muted-foreground": color == null,
                  "text-white": entry.winner != null,
                },
                {
                  "ring-2 ring-foreground ring-offset-2 ring-offset-background":
                    isCurrent,
                  "hover:ring-1 hover:ring-foreground": !isCurrent,
                },
              )}
              style:background-color={color}
              style:background-image={entry.later == null ? undefined : HATCH}
              onpointerenter={(event) => {
                if (isHover(event)) probed = entry.race.slug;
              }}
              onfocus={() => (probed = entry.race.slug)}
              onblur={() => (probed = null)}>{entry.race.district ?? ""}</a
            >
          </li>
        {/each}
      </ol>
    </div>
  {/if}

  {#if counties.length > 0}
    <div
      class="flex flex-wrap items-center gap-2 border-bs border-border pbs-3 lg:col-span-2"
    >
      <span class="me-2 text-[0.8125rem] font-semibold"
        >{m.night_races_counties()}</span
      >
      {#each counties as county (county.id)}
        <a
          href={county.href}
          class="flex items-center gap-2 rounded-full py-1 ps-2 pe-3 ring-1 ring-border transition-colors hover:bg-secondary"
        >
          <span
            class="shrink-0 rounded-full block-2 inline-2"
            style:background-color={county.leader == null
              ? "var(--border)"
              : tone(county.leader)}
          ></span>
          <span class="text-[0.8125rem] font-semibold">{county.name}</span>
          {#if county.leader != null}
            <span
              class="font-figures text-xs text-muted-foreground tabular-nums"
              >{surname(county.leader)} +{lead(county.margin)}</span
            >
          {/if}
          <span
            class="relative overflow-hidden rounded-xs bg-muted block-1 inline-8"
            title={county.counted == null
              ? m.night_nation_waiting()
              : m.night_races_counted({ share: percent(county.counted, 0) })}
          >
            <span
              class="absolute inset-y-0 inset-s-0 bg-foreground transition-[inline-size] duration-700 motion-reduce:transition-none"
              style:inline-size="{(county.counted ?? 0) * 100}%"
            ></span>
          </span>
        </a>
      {/each}
    </div>
  {/if}
</nav>
