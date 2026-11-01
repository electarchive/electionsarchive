<script lang="ts">
  import { page } from "$app/state";
  import { untrack } from "svelte";
  import type { Edge } from "#lib/live/simulate.js";
  import Race from "#lib/components/live/race.svelte";
  import { localize } from "#lib/live/prefs.svelte.js";
  import { plan, snapshot, stamps, start } from "#lib/live/simulate.js";
  import { m } from "#lib/paraglide/messages.js";
  import type { PageProps } from "./$types";
  import Toolbar from "./toolbar.svelte";

  const { data }: PageProps = $props();

  const seed = $derived(data.seed);
  let scenario = $state(untrack(() => start(data.seed)));
  let edges = $state<Array<Edge>>([]);
  let isOffline = $state(false);
  const planned = $derived(plan(seed, scenario));
  let clock = $state(
    untrack(() => plan(data.seed, start(data.seed)).close - 5 * 60_000),
  );
  let held = $state.raw<ReturnType<typeof snapshot> | null>(null);
  let at = $state<string | null>(null);
  let isFinal = $state(true);

  const quantum = $derived(Math.floor(clock / 30_000) * 30_000);
  const live = $derived(snapshot(seed, planned, edges, quantum));
  const election = $derived(isOffline && held != null ? held : live);
  const race = $derived(election.races[0]);
  const marks = $derived(stamps(planned, edges, planned.end));

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
  {#if race != null}
    <Race
      {election}
      {race}
      geography={data.geography}
      {clock}
      {isOffline}
      bind:at
      bind:isFinal
    />
  {/if}
</main>

<Toolbar
  {seed}
  {planned}
  {marks}
  bind:scenario
  bind:edges
  bind:clock
  bind:isOffline
  bind:at
/>
