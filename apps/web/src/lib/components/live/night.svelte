<script lang="ts">
  import type { LiveElection, LiveEstimate } from "@ea/analytics/live";
  import ArrowUpIcon from "@lucide/svelte/icons/arrow-up";
  import type { Holder } from "#lib/live/ballot.js";
  import type { Trail } from "#lib/live/nation.js";
  import type { Ballot } from "#lib/server/live-ballot.js";
  import type { LivePage } from "#lib/server/live-page.js";
  import Dashboard from "#lib/components/live/dashboard.svelte";
  import Place from "#lib/components/live/place.svelte";
  import Race from "#lib/components/live/race.svelte";
  import Races from "#lib/components/live/races.svelte";
  import State from "#lib/components/live/state.svelte";
  import { placeName, placeSlug } from "#lib/live/format.js";
  import { liveLinks } from "#lib/live/links.js";
  import { remainder, standing } from "#lib/live/standing.js";
  import { summarize } from "#lib/live/summary.js";
  import { m } from "#lib/paraglide/messages.js";

  let {
    data,
    election,
    estimates,
    clock,
    isOffline,
    at = $bindable(),
    isFinal = $bindable(),
  }: {
    data: LivePage & {
      trail?: Trail | null;
      ballot?: Ballot | null;
      seat?: { held: Holder | null; families: Record<string, string> } | null;
    };
    election: LiveElection;
    estimates: Record<string, LiveEstimate>;
    clock: number;
    isOffline: boolean;
    at: string | null;
    isFinal: boolean;
  } = $props();

  const toLive = liveLinks();

  let scrollY = $state(0);
  let innerHeight = $state(0);

  function rise() {
    window.scrollTo({
      top: 0,
      behavior: matchMedia("(prefers-reduced-motion: reduce)").matches
        ? "auto"
        : "smooth",
    });
  }

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
    if (anchor == null || election.slug.split("/").length !== 3) {
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

<svelte:window bind:scrollY bind:innerHeight />

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
    control={data.control}
    trail={data.trail}
    forecasts={data.shown.forecasts}
    {clock}
    {isOffline}
  />
{:else if data.ballot != null}
  <State
    {election}
    {estimates}
    ballot={data.ballot}
    geography={data.geography}
    {isOffline}
  />
{:else}
  <Races
    summaries={rail}
    current={data.focus}
    {counties}
    back={{ href: toLive(election.slug), label: election.title }}
  />
  {#if race != null}
    {#key race.id}
      <Race
        {election}
        {race}
        geography={data.geography}
        seat={data.seat ?? null}
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

{#if innerHeight > 0 && scrollY > innerHeight}
  <button
    type="button"
    class="fixed inset-e-4 inset-be-4 z-30 flex items-center gap-1.5 rounded-full bg-background px-3.5 py-2 text-[0.8125rem] font-semibold shadow-md ring-1 ring-border transition-colors hover:bg-secondary focus-visible:ring-2 focus-visible:ring-foreground focus-visible:outline-none print:hidden"
    onclick={rise}
  >
    <ArrowUpIcon class="block-4 inline-4" aria-hidden="true" />
    {m.night_back_to_top()}
  </button>
{/if}
