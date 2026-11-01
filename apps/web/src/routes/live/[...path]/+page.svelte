<script lang="ts">
  import type { LiveRace } from "@ea/analytics/live";
  import { refreshAll, replaceState } from "$app/navigation";
  import { resolve } from "$app/paths";
  import { page } from "$app/state";
  import { untrack } from "svelte";
  import Dashboard from "#lib/components/live/dashboard.svelte";
  import Race from "#lib/components/live/race.svelte";
  import { lead, stamp, surname, tone } from "#lib/live/format.js";
  import { closing, describe, summarize } from "#lib/live/summary.js";
  import { m } from "#lib/paraglide/messages.js";
  import { orpc } from "#lib/rpc.js";
  import { cn } from "#lib/utils.js";
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

  const focused = $derived(
    race == null ? null : summarize(race, data.estimates[race.id] ?? null),
  );
  const headline = $derived(
    race == null || focused == null
      ? null
      : describe(focused, closing(election, race), clock),
  );
  const name = $derived(race?.title ?? election.title);
  const heading = $derived(m.night_seo_title({ title: name }));
  const description = $derived(
    headline == null
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
    `${page.url.origin}/og${page.url.pathname}.png${election.newest == null ? "" : `?v=${encodeURIComponent(election.newest)}`}`,
  );
  const alt = $derived(
    headline == null
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
      distribution: [
        ["application/json", "json"],
        ["text/markdown", "md"],
      ].map(([type, suffix]) => ({
        "@type": "DataDownload",
        encodingFormat: type,
        contentUrl: `${canonical}.${suffix}`,
      })),
    })
      .replaceAll("<", String.raw`\u003c`)
      .replaceAll("&", String.raw`\u0026`),
  );

  function title(entry: LiveRace) {
    if (entry.office === "senate") {
      return m.night_nation_senate();
    }
    if (entry.office === "governor") {
      return m.night_nation_governor();
    }
    if (entry.office === "house" && entry.district != null) {
      return m.night_rail_house({ district: entry.district });
    }
    return entry.title;
  }

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
    const path = page.params.path ?? "";
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
</script>

<svelte:head>
  <title>{heading} | {m.wordmark()}</title>
  <meta name="description" content={description} />
  <meta
    name="robots"
    content="index, follow, max-image-preview:large, max-snippet:-1"
  />
  <link rel="canonical" href={canonical} />
  <link rel="alternate" type="text/markdown" href="{canonical}.md" />
  <link rel="alternate" type="application/json" href="{canonical}.json" />
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
  {#if data.focus == null}
    <Dashboard
      {election}
      estimates={data.estimates}
      nation={data.nation}
      {clock}
      {isOffline}
    />
  {:else}
    {#if rail.length > 1}
      <nav
        aria-label={m.night_rail_label()}
        class="border-be border-border bg-background"
      >
        <ol class="flex gap-px overflow-x-auto bg-border">
          {#each rail as summary (summary.race.id)}
            {const isCurrent = $derived(summary.race.slug === data.focus)}
            {const option = $derived(summary.winner ?? summary.leader)}
            <li class="shrink-0 bg-background">
              <a
                href={resolve("/live/[...path]", { path: summary.race.slug })}
                aria-current={isCurrent ? "page" : undefined}
                class={cn(
                  "grid gap-1 px-3 py-2 text-[0.8125rem] min-inline-32 hover:highlighter",
                  { "shadow-[inset_0_-2px_0_var(--foreground)]": isCurrent },
                )}
              >
                <span
                  class={cn("font-semibold", {
                    "text-muted-foreground": !isCurrent,
                  })}>{title(summary.race)}</span
                >
                <span class="flex items-center gap-1.5 text-xs">
                  {#if option == null}
                    <span class="rounded-full bg-muted block-2 inline-2"></span>
                    <span class="text-muted-foreground"
                      >{m.night_nation_waiting()}</span
                    >
                  {:else}
                    <span
                      class="rounded-full block-2 inline-2"
                      style:background-color={tone(option)}
                    ></span>
                    <span
                      class={cn({ "font-semibold": summary.winner != null })}
                      >{surname(option)}</span
                    >
                    <span
                      class="font-figures text-muted-foreground tabular-nums"
                      >+{lead(summary.margin)}</span
                    >
                  {/if}
                </span>
                <span
                  class="relative overflow-hidden rounded-xs bg-muted block-1"
                  aria-hidden="true"
                >
                  <span
                    class="absolute inset-y-0 inset-s-0 bg-foreground transition-[inline-size] duration-700 motion-reduce:transition-none"
                    style:inline-size="{(summary.counted ?? 0) * 100}%"
                  ></span>
                </span>
              </a>
            </li>
          {/each}
        </ol>
      </nav>
    {/if}
    {#if race != null}
      {#key race.id}
        <Race
          {election}
          {race}
          geography={data.geography}
          {clock}
          {isOffline}
          bind:at
          bind:isFinal
        />
      {/key}
    {/if}
  {/if}
</main>
