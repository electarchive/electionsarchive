<script lang="ts">
  import { page } from "$app/state";
  import { setContext } from "svelte";
  import { date } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { WITHIN } from "#lib/party.js";
  import type { PageData } from "./$types";
  import Contest from "./contest.svelte";
  import Day from "./day.svelte";
  import Election from "./election.svelte";
  import Polity from "./polity.svelte";
  import Preview from "./preview.svelte";

  const { data }: { data: PageData } = $props();

  setContext(WITHIN, () => data.within);

  const crumbs = $derived(
    data.kind === "contest"
      ? [
          ...data.contest.breadcrumbs,
          {
            name: data.contest.election.name,
            path: data.contest.election.path,
          },
        ]
      : data.kind === "election"
        ? [
            ...data.election.breadcrumbs,
            ...(data.election.parent == null ? [] : [data.election.parent]),
          ]
        : data.kind === "day"
          ? data.local.breadcrumbs
          : data.polity.breadcrumbs,
  );
  const title = $derived.by(() => {
    if (data.kind === "day") {
      return m.local_title({
        year: data.date.slice(0, 4),
        place: data.local.name,
      });
    }
    if (data.kind !== "contest") {
      return data.kind === "election" ? data.election.name : data.polity.name;
    }
    const name =
      data.contest.scope.noun == null
        ? data.contest.name
        : `${data.contest.scope.name}, ${data.contest.name}`;
    const tail = /, [^,]+$/u.exec(data.contest.election.name)?.[0];
    return /\b\d{4}\b/u.test(name)
      ? name
      : `${tail != null && name.endsWith(tail) ? name.slice(0, -tail.length) : name}, ${data.contest.election.name}`;
  });
  const query = $derived(
    data.kind === "election" && data.scope != null
      ? `?in=${data.scope.path}`
      : "",
  );
  const canonical = $derived(`${page.url.origin}${page.url.pathname}${query}`);
  const years = $derived(
    data.kind === "polity"
      ? [
          ...data.polity.elections.flatMap((entry) =>
            entry.year == null ? [] : [entry.year],
          ),
          ...data.polity.years,
        ]
      : [],
  );
  const description = $derived(
    data.kind === "contest"
      ? m.meta_contest({ name: title, date: date(data.contest.election.date) })
      : data.kind === "election"
        ? m.meta_election({
            name: data.election.name,
            date: date(data.election.date),
          })
        : data.kind === "day"
          ? m.local_meta({ place: data.local.name, date: date(data.date) })
          : m.meta_polity({
              name: data.polity.name,
              first: String(Math.min(...years)),
              last: String(Math.max(...years)),
            }),
  );
  const dataset = $derived({
    "@context": "https://schema.org",
    "@type": "Dataset",
    name: title,
    description,
    url: canonical,
    isAccessibleForFree: true,
    creator: {
      "@type": "Organization",
      name: m.wordmark(),
      url: page.url.origin,
    },
    spatialCoverage: {
      "@type": "Place",
      name:
        data.kind === "contest"
          ? data.contest.scope.name
          : data.kind === "election"
            ? (data.election.breadcrumbs.at(-1)?.name ?? data.election.name)
            : data.kind === "day"
              ? data.local.name
              : data.polity.name,
    },
    temporalCoverage:
      data.kind === "contest"
        ? data.contest.election.date
        : data.kind === "election"
          ? data.election.date
          : data.kind === "day"
            ? data.date
            : years.length === 0
              ? null
              : `${Math.min(...years)}/${Math.max(...years)}`,
    isBasedOn:
      data.kind === "contest"
        ? data.contest.sets.flatMap(({ source }) =>
            source.url == null ? [] : [source.url],
          )
        : undefined,
    distribution: [
      ["application/json", "json"],
      ["text/markdown", "md"],
    ].map(([type, suffix]) => ({
      "@type": "DataDownload",
      encodingFormat: type,
      contentUrl: `${page.url.origin}${page.url.pathname}.${suffix}${query}`,
    })),
  });
  const jsonld = $derived(
    JSON.stringify(dataset)
      .replaceAll("<", String.raw`\u003c`)
      .replaceAll("&", String.raw`\u0026`),
  );
</script>

<svelte:head>
  <title>{title} | {m.wordmark()}</title>
  <meta name="description" content={description} />
  <link rel="canonical" href={canonical} />
  <meta property="og:type" content="website" />
  <meta property="og:title" content={title} />
  <meta property="og:description" content={description} />
  <meta property="og:url" content={canonical} />
  <meta
    property="og:image"
    content="{page.url.origin}/og{page.url.pathname}.png"
  />
  <meta name="twitter:card" content="summary_large_image" />
  <link
    rel="alternate"
    type="application/json"
    href="{page.url.origin}{page.url.pathname}.json{query}"
  />
  <link
    rel="alternate"
    type="text/markdown"
    href="{page.url.origin}{page.url.pathname}.md{query}"
  />
  {#if data.kind === "contest" && data.contest.scope.isFine}
    <meta name="robots" content="noindex" />
  {/if}
  <svelte:element this={"script"} type="application/ld+json"
    >{jsonld}</svelte:element
  >
</svelte:head>

<main class="pbe-16">
  <article class="grid grid-cols-12 gap-px overflow-clip border-be bg-border">
    {#if crumbs.length > 0}
      <nav
        class="col-span-12 flex flex-wrap items-center gap-x-2 gap-y-1 bg-card px-3.5 py-2.5 text-sm text-muted-foreground"
        aria-label={m.breadcrumbs()}
      >
        {#each crumbs as crumb, index (index)}
          {#if index > 0}
            <span aria-hidden="true">/</span>
          {/if}
          {#if crumb.path == null}
            <span>{crumb.name}</span>
          {:else}
            <Preview
              class="hover:highlighter hover:text-foreground"
              path={crumb.path}>{crumb.name}</Preview
            >
          {/if}
        {/each}
      </nav>
    {/if}
    {#if data.kind === "contest"}
      <Contest
        contest={data.contest}
        lists={data.lists}
        rounds={data.rounds}
        ballot={data.ballot}
        cycle={data.cycle}
        handover={data.handover}
        additional={data.additional}
        successors={data.successors}
        statutes={data.statutes}
        boundaries={data.boundaries}
        surround={data.surround}
        match={data.match}
        lean={data.lean}
      />
    {:else if data.kind === "election"}
      {#key `${data.id}${query}`}
        <Election
          id={data.id}
          scope={data.scope}
          election={data.election}
          cycle={data.cycle}
          sections={data.sections}
        />
      {/key}
    {:else if data.kind === "day"}
      <Day {data} />
    {:else}
      <Polity id={data.id} polity={data.polity} />
    {/if}
  </article>
</main>
