<script lang="ts">
  import type { LiveElection, LiveEstimate } from "@ea/analytics/live";
  import type { LivePage } from "#lib/server/live-page.js";
  import Dashboard from "#lib/components/live/dashboard.svelte";
  import Place from "#lib/components/live/place.svelte";
  import Race from "#lib/components/live/race.svelte";
  import Races from "#lib/components/live/races.svelte";
  import { placeName, placeSlug } from "#lib/live/format.js";
  import { liveLinks } from "#lib/live/links.js";
  import { remainder, standing } from "#lib/live/standing.js";
  import { summarize } from "#lib/live/summary.js";

  let {
    data,
    election,
    estimates,
    clock,
    isOffline,
    at = $bindable(),
    isFinal = $bindable(),
  }: {
    data: LivePage;
    election: LiveElection;
    estimates: Record<string, LiveEstimate>;
    clock: number;
    isOffline: boolean;
    at: string | null;
    isFinal: boolean;
  } = $props();

  const toLive = liveLinks();

  const ORDER = { senate: 0, governor: 1, house: 2, other: 3 };

  const race = $derived(
    election.races.find((entry) => entry.slug === data.focus) ?? null,
  );
  const rail = $derived(
    election.races
      .map((entry) => summarize(entry, estimates[entry.id] ?? null))
      .toSorted(
        (a, b) =>
          ORDER[a.race.office] - ORDER[b.race.office] ||
          Number.parseInt(a.race.district ?? "0", 10) -
            Number.parseInt(b.race.district ?? "0", 10) ||
          a.race.title.localeCompare(b.race.title),
      ),
  );
  const anchor = $derived(
    race != null && race.office !== "house"
      ? race
      : (election.races.find((entry) => entry.office === "senate") ??
          election.races.find((entry) => entry.office === "governor") ??
          null),
  );
  const counties = $derived.by(() => {
    if (anchor == null) {
      return [];
    }
    const now = standing(anchor, { at: null, isFinal: true });
    const estimate = estimates[anchor.id] ?? null;
    return [...now.units.values()]
      .filter((row) => row.unit.scheme === "us.county")
      .toSorted(
        (x, y) =>
          (y.baseline?.turnout ?? y.total) - (x.baseline?.turnout ?? x.total),
      )
      .slice(0, 6)
      .map((row) => {
        const rest = remainder(row, estimate, anchor.options, now.pair);
        return {
          id: row.unit.id,
          name: placeName(row.unit.name),
          href: toLive(`${election.slug}/place/${placeSlug(row.unit.name)}`),
          leader:
            row.leader == null ? null : (anchor.options[row.leader] ?? null),
          margin: row.margin,
          counted:
            row.total > 0
              ? Math.min(1, row.total / Math.max(1, rest.expected))
              : null,
        };
      });
  });
</script>

{#if data.place != null}
  <Place
    {election}
    {estimates}
    place={data.place}
    geography={data.geography}
    {clock}
    {isOffline}
  />
{:else if data.focus == null}
  <Dashboard
    {election}
    {estimates}
    nation={data.nation}
    house={data.house}
    control={data.control}
    forecasts={data.shown.forecasts}
    {clock}
    {isOffline}
  />
{:else}
  {#if rail.length > 1 || counties.length > 0}
    <Races summaries={rail} current={data.focus} {counties} />
  {/if}
  {#if race != null}
    {#key race.id}
      <Race
        {election}
        {race}
        geography={data.geography}
        base={election.slug}
        {clock}
        {isOffline}
        forecasts={data.shown.forecasts}
        bind:at
        bind:isFinal
      />
    {/key}
  {/if}
{/if}
