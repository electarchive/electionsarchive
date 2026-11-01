<script lang="ts">
  import { refreshAll, replaceState } from "$app/navigation";
  import { resolve } from "$app/paths";
  import { page } from "$app/state";
  import { untrack } from "svelte";
  import Dashboard from "#lib/components/live/dashboard.svelte";
  import Place from "#lib/components/live/place.svelte";
  import Race from "#lib/components/live/race.svelte";
  import Races from "#lib/components/live/races.svelte";
  import { placeName, placeSlug, stamp } from "#lib/live/format.js";
  import { localize } from "#lib/live/prefs.svelte.js";
  import { remainder, standing } from "#lib/live/standing.js";
  import { closing, describe, summarize } from "#lib/live/summary.js";
  import { m } from "#lib/paraglide/messages.js";
  import { orpc } from "#lib/rpc.js";
  import type { PageProps } from "./$types";

  const { data }: PageProps = $props();

  const ORDER = { senate: 0, governor: 1, house: 2, other: 3 };

  let clock = $state(Date.now());
  let isOffline = $state(false);
  let at = $state<string | null>(untrack(() => data.at));
  let isFinal = $state(true);

  const election = $derived(data.election);
  const race = $derived(
    election.races.find((entry) => entry.slug === data.focus) ?? null,
  );
  const rail = $derived(
    election.races
      .map((entry) => summarize(entry, data.estimates[entry.id] ?? null))
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
    const estimate = data.estimates[anchor.id] ?? null;
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
          href: resolve("/live/[...path]", {
            path: `${election.slug}/place/${placeSlug(row.unit.name)}`,
          }),
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
  const focused = $derived(
    race == null ? null : summarize(race, data.estimates[race.id] ?? null),
  );
  const headline = $derived(
    race == null || focused == null
      ? null
      : describe(focused, closing(election, race), clock),
  );
  const spot = $derived(data.place == null ? null : placeName(data.place.name));
  const name = $derived(race?.title ?? election.title);
  const heading = $derived(
    spot == null
      ? m.night_seo_title({ title: name })
      : m.night_place_seo_title({ place: spot, title: election.title }),
  );
  const description = $derived(
    spot != null
      ? m.night_place_seo_description({ place: spot, title: election.title })
      : headline == null
        ? m.night_seo_nation({ title: election.title })
        : election.newest == null
          ? m.night_seo_upcoming({ headline, title: name })
          : m.night_seo_description({
              headline,
              title: name,
              time: stamp(election.newest),
            }),
  );
  const canonical = $derived(`${page.url.origin}${page.url.pathname}`);
  const image = $derived(
    `${page.url.origin}/og${spot == null ? page.url.pathname : `/live/${election.slug}`}.png${election.newest == null ? "" : `?v=${encodeURIComponent(election.newest)}`}`,
  );
  const alt = $derived(
    spot != null
      ? m.night_place_og_alt({ place: spot, title: election.title })
      : headline == null
        ? m.night_og_nation_alt({ title: election.title })
        : m.night_og_alt({ title: name, headline }),
  );
  const jsonld = $derived(
    JSON.stringify({
      "@context": "https://schema.org",
      "@type": "Dataset",
      name: heading,
      description,
      url: canonical,
      isAccessibleForFree: true,
      creator: {
        "@type": "Organization",
        name: m.wordmark(),
        url: page.url.origin,
      },
      temporalCoverage: election.date,
      dateModified: election.newest ?? undefined,
      image,
      distribution:
        spot == null
          ? [
              ["application/json", "json"],
              ["text/markdown", "md"],
            ].map(([type, suffix]) => ({
              "@type": "DataDownload",
              encodingFormat: type,
              contentUrl: `${canonical}.${suffix}`,
            }))
          : undefined,
    })
      .replaceAll("<", String.raw`\u003c`)
      .replaceAll("&", String.raw`\u0026`),
  );

  $effect(() => {
    const wanted = at;
    const url = new URL(untrack(() => page.url.href));
    if ((url.searchParams.get("at") ?? null) === wanted) {
      return;
    }
    if (wanted == null) {
      url.searchParams.delete("at");
    } else {
      url.searchParams.set("at", wanted);
    }
    void replaceState(
      url,
      untrack(() => page.state),
    );
  });

  $effect(() => {
    const path = data.place == null ? (page.params.path ?? "") : election.slug;
    const timer = setInterval(() => {
      clock = Date.now();
      void (async () => {
        try {
          const probe = await orpc.live.stamp.call({ path });
          isOffline = false;
          if (
            probe.newest > (election.newest ?? "") ||
            probe.isCertified !== election.isCertified
          ) {
            await refreshAll();
          }
        } catch {
          isOffline = true;
        }
      })();
    }, 30_000);
    return () => clearInterval(timer);
  });

  $effect(localize);
</script>

<svelte:head>
  <title>{heading} | {m.wordmark()}</title>
  <meta name="description" content={description} />
  <meta
    name="robots"
    content="index, follow, max-image-preview:large, max-snippet:-1"
  />
  <link rel="canonical" href={canonical} />
  {#if spot == null}
    <link rel="alternate" type="text/markdown" href="{canonical}.md" />
    <link rel="alternate" type="application/json" href="{canonical}.json" />
  {/if}
  <meta property="og:type" content="website" />
  <meta property="og:site_name" content={m.wordmark()} />
  <meta property="og:locale" content="en_US" />
  <meta property="og:title" content={heading} />
  <meta property="og:description" content={description} />
  <meta property="og:url" content={canonical} />
  <meta property="og:image" content={image} />
  <meta property="og:image:secure_url" content={image} />
  <meta property="og:image:type" content="image/png" />
  <meta property="og:image:width" content="2400" />
  <meta property="og:image:height" content="1260" />
  <meta property="og:image:alt" content={alt} />
  {#if election.newest != null}
    <meta property="og:updated_time" content={election.newest} />
  {/if}
  <meta name="twitter:card" content="summary_large_image" />
  <meta name="twitter:title" content={heading} />
  <meta name="twitter:description" content={description} />
  <meta name="twitter:image" content={image} />
  <meta name="twitter:image:alt" content={alt} />
  <svelte:element this={"script"} type="application/ld+json"
    >{jsonld}</svelte:element
  >
</svelte:head>

<main class="sm:pbe-16">
  {#if data.place != null}
    <Place
      {election}
      estimates={data.estimates}
      place={data.place}
      geography={data.geography}
      {clock}
      {isOffline}
    />
  {:else if data.focus == null}
    <Dashboard
      {election}
      estimates={data.estimates}
      nation={data.nation}
      house={data.house}
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
</main>
