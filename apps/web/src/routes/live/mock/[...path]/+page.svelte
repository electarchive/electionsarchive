<script lang="ts">
  import type { LiveElection } from "@ea/analytics/live";
  import { page } from "$app/state";
  import { untrack } from "svelte";
  import type { Edge } from "#lib/live/simulate.js";
  import Night from "#lib/components/live/night.svelte";
  import { estimateAt } from "#lib/live/history.js";
  import { mockLinks } from "#lib/live/links.js";
  import { localize } from "#lib/live/prefs.svelte.js";
  import {
    DEFAULT_SCENARIO,
    MINUTE,
    plan,
    replay,
    seeds,
    sketch,
    stamps,
    start,
  } from "#lib/live/simulate.js";
  import { m } from "#lib/paraglide/messages.js";
  import type { PageProps } from "./$types";
  import Toolbar from "./toolbar.svelte";

  const { data }: PageProps = $props();

  mockLinks();

  const lead = $derived(
    (
      data.election.races.find((race) => race.slug === data.focus) ??
      data.election.races.find((race) => race.office === "senate") ??
      data.election.races.find((race) => race.office === "governor") ??
      data.election.races[0]
    )?.id ?? null,
  );
  const seeded = $derived(seeds(data.election, lead));
  let scenario = $state(
    untrack(() => {
      const anchor = seeded.find((seed) => seed.race.id === lead);
      return anchor == null ? DEFAULT_SCENARIO : start(anchor);
    }),
  );
  let edges = $state<Array<Edge>>([]);
  let isOffline = $state(false);
  let at = $state<string | null>(null);
  let isFinal = $state(true);
  let held = $state.raw<LiveElection | null>(null);

  const runs = $derived(
    seeded.map((seed) => ({ seed, planned: plan(seed, scenario) })),
  );
  const leading = $derived(
    runs.find((run) => run.seed.race.id === lead) ?? null,
  );
  const from = $derived(Math.min(...runs.map((run) => run.planned.start)));
  const to = $derived(Math.max(...runs.map((run) => run.planned.end)));
  let clock = $state(
    untrack(() => (leading?.planned.close ?? Date.now()) - 5 * MINUTE),
  );

  const quantum = $derived(Math.floor(clock / 30_000) * 30_000);
  const live = $derived(replay(data.election, runs, lead, edges, quantum));
  const election = $derived(isOffline && held != null ? held : live);
  const estimates = $derived(
    Object.fromEntries(
      election.races.map((race) => [
        race.id,
        race.units.length === 0
          ? sketch(race, estimateAt(race, null))
          : estimateAt(race, null),
      ]),
    ),
  );
  const marks = $derived(stamps(runs, lead, edges, to));

  $effect(() => {
    if (!isOffline) {
      held = live;
    }
  });

  $effect(localize);
</script>

<svelte:head>
  <title>{m.night_mock_title()} | {m.wordmark()}</title>
  <meta name="robots" content="noindex, nofollow" />
  <link rel="canonical" href="{page.url.origin}{page.url.pathname}" />
</svelte:head>

<main class="pbe-72 sm:pbe-56">
  <Night
    data={{ ...data, shown: { results: true, forecasts: true } }}
    {election}
    {estimates}
    {clock}
    {isOffline}
    bind:at
    bind:isFinal
  />
</main>

{#if leading != null}
  <Toolbar
    seed={leading.seed}
    planned={leading.planned}
    {marks}
    date={data.election.date}
    {from}
    {to}
    bind:scenario
    bind:edges
    bind:clock
    bind:isOffline
    bind:at
  />
{/if}
