<script lang="ts">
  import type {
    LiveElection,
    LiveEstimate,
    LiveRace,
  } from "@ea/analytics/live";
  import { Pending } from "svelte-islands";
  import type {
    BallotLocalKind,
    BallotLocals,
  } from "#lib/server/live-ballot.js";
  import Locals from "#lib/components/live/locals.svelte";
  import { Button } from "#lib/components/ui/button/index.js";
  import { count } from "#lib/format.js";
  import { stub } from "#lib/live/ballot.js";
  import { summarize } from "#lib/live/summary.js";
  import { m } from "#lib/paraglide/messages.js";

  const {
    election,
    estimates,
    code,
    name,
    total,
    places,
  }: {
    election: LiveElection;
    estimates: Record<string, LiveEstimate>;
    code: string;
    name: string;
    total: number;
    places: number;
  } = $props();

  const KINDS: Record<
    BallotLocalKind,
    NonNullable<LiveRace["jurisdiction"]>["kind"]
  > = {
    county: "county",
    city: "municipal",
    school: "school",
    special: "special",
    judicial: "judicial",
    other: "state",
  };

  let found = $state<BallotLocals | null>(null);
  let status = $state<"idle" | "loading" | "error">("idle");

  const live = $derived(
    new Map(election.races.map((race) => [race.slug, race])),
  );
  const summaries = $derived(
    found == null
      ? []
      : found.places.flatMap((place) =>
          place.contests.map((contest) => {
            const race =
              live.get(contest.slug) ??
              stub(
                contest,
                code,
                found?.parties ?? {},
                "other",
                { name: place.name, kind: KINDS[place.kind] },
                election.later?.[code]?.feed,
              );
            return summarize(race, estimates[race.id] ?? null);
          }),
        ),
  );

  async function load() {
    if (status === "loading" || found != null) {
      return;
    }
    status = "loading";
    try {
      const response = await fetch(`/live/ballot/${election.slug}`);
      if (!response.ok) {
        throw new Error(String(response.status));
      }
      found = (await response.json()) as BallotLocals;
      status = "idle";
    } catch {
      status = "error";
    }
  }

  function watch(node: HTMLElement) {
    const observer = new IntersectionObserver(
      (seen) => {
        if (seen.some((entry) => entry.isIntersecting)) {
          observer.disconnect();
          void load();
        }
      },
      { rootMargin: "600px 0px" },
    );
    observer.observe(node);
    return () => {
      observer.disconnect();
    };
  }
</script>

{#if found != null}
  <Locals {summaries} labels={{ [code]: name }} />
{:else}
  <section
    class="grid justify-items-start gap-3 bg-background px-4 py-6 sm:px-8"
    aria-labelledby="night-local"
    {@attach watch}
  >
    <h2 id="night-local" class="heading text-lg!">{m.night_local_title()}</h2>
    <p class="font-figures text-sm text-muted-foreground tabular-nums">
      {m.night_state_local_count({
        count: count(total),
        places: count(places),
      })}
    </p>
    {#if status === "error"}
      <p class="text-sm">{m.night_state_local_error()}</p>
      <Button variant="outline" size="sm" onclick={load}
        >{m.night_state_retry()}</Button
      >
    {:else if status === "loading"}
      <p class="text-sm text-muted-foreground" aria-live="polite">
        {m.night_state_local_loading()}
      </p>
      <Pending
        name="live.state.local"
        isPending={true}
        class="[--island-inset:0]"
      />
    {:else}
      <Button variant="outline" size="sm" onclick={load}
        >{m.night_state_local_load()}</Button
      >
    {/if}
  </section>
{/if}
