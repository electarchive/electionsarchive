<script lang="ts">
  import { refreshAll, replaceState } from "$app/navigation";
  import { page } from "$app/state";
  import { untrack } from "svelte";
  import Night from "#lib/components/live/night.svelte";
  import { placeName, stamp } from "#lib/live/format.js";
  import { localize } from "#lib/live/prefs.svelte.js";
  import { closing, describe, summarize } from "#lib/live/summary.js";
  import { m } from "#lib/paraglide/messages.js";
  import { orpc } from "#lib/rpc.js";
  import type { PageProps } from "./$types";

  const { data }: PageProps = $props();

  let clock = $state(Date.now());
  let isOffline = $state(false);
  let at = $state<string | null>(untrack(() => data.at));
  let isFinal = $state(true);

  const election = $derived(data.election);
  const race = $derived(
    data.ballot == null
      ? (election.races.find((entry) => entry.slug === data.focus) ?? null)
      : null,
  );
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
  <Night
    {data}
    {election}
    estimates={data.estimates}
    {clock}
    {isOffline}
    bind:at
    bind:isFinal
  />
</main>
