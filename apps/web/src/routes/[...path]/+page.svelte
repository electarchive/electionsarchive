<script lang="ts">
  import { page } from "$app/state";
  import { setContext } from "svelte";
  import ShareImage from "#lib/components/share-image.svelte";
  import { date } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { WITHIN } from "#lib/party.js";
  import { Settled } from "#lib/settled.svelte.js";
  import type { PageData } from "./$types";
  import PreviewHost from "./preview-host.svelte";
  import Preview from "./preview.svelte";

  const { data }: { data: PageData } = $props();

  setContext(WITHIN, () => data.within);

  const visible = $derived(page.shallow?.url ?? page.url);
  const opened = $derived(
    data.kind === "election" ? new Settled(data.body) : null,
  );
  const picked = $derived.by(() => {
    const election = opened?.value?.election;
    if (data.kind !== "election" || election == null) {
      return null;
    }
    const rest = visible.pathname.slice(data.path.length + 2);
    return visible.pathname.startsWith(`/${data.path}/`) &&
      (election.rest != null ||
        election.races.some(
          (race) => race.anchor === rest.replace(/-total$/u, ""),
        ))
      ? rest
      : null;
  });
  const chosen = $derived(
    data.kind === "election" && picked != null
      ? (opened?.value?.election.races.find(
          (race) => race.anchor === picked.replace(/-total$/u, ""),
        )?.name ?? null)
      : null,
  );

  const crumbs = $derived(
    data.kind === "contest"
      ? [
          ...data.head.breadcrumbs,
          ...(data.head.election.path == null
            ? []
            : [
                {
                  name: data.head.election.name,
                  path: data.head.election.path,
                },
              ]),
        ]
      : data.kind === "election"
        ? [
            ...data.head.breadcrumbs,
            ...(data.head.parent == null ? [] : [data.head.parent]),
            ...(chosen == null
              ? []
              : [{ name: data.head.name, path: data.path }]),
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
      return data.kind === "election"
        ? chosen == null
          ? data.head.name
          : m.election_race_title({ race: chosen, election: data.head.name })
        : data.polity.name;
    }
    const name =
      data.head.scope.noun == null
        ? data.head.name
        : `${data.head.scope.abroad == null ? data.head.scope.name : m.scope_abroad_title({ place: data.head.scope.name })}, ${data.head.name}`;
    const tail = /, [^,]+$/u.exec(data.head.election.name)?.[0];
    return /\b\d{4}\b/u.test(name)
      ? name
      : `${tail != null && name.endsWith(tail) ? name.slice(0, -tail.length) : name}, ${data.head.election.name}`;
  });
  const query = $derived(
    data.kind === "election" && data.scope != null
      ? `?in=${data.scope.path}`
      : "",
  );
  const canonical = $derived(`${page.url.origin}${visible.pathname}${query}`);
  const years = $derived(
    data.kind === "polity"
      ? [
          ...(data.polity.calendar.first == null
            ? []
            : [data.polity.calendar.first]),
          ...(data.polity.calendar.last == null
            ? []
            : [data.polity.calendar.last]),
          ...data.polity.years,
        ]
      : [],
  );
  const description = $derived(
    data.kind === "contest"
      ? m.meta_contest({ name: title, date: date(data.head.election.date) })
      : data.kind === "election"
        ? chosen == null
          ? m.meta_election({
              name: data.head.name,
              date: date(data.head.date),
            })
          : m.election_race_meta({
              race: chosen,
              election: data.head.name,
              date: date(data.head.date),
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
          ? data.head.scope.name
          : data.kind === "election"
            ? (data.head.breadcrumbs.at(-1)?.name ?? data.head.name)
            : data.kind === "day"
              ? data.local.name
              : data.polity.name,
    },
    temporalCoverage:
      data.kind === "contest"
        ? data.head.election.date
        : data.kind === "election"
          ? data.head.date
          : data.kind === "day"
            ? data.date
            : years.length === 0
              ? null
              : `${Math.min(...years)}/${Math.max(...years)}`,
    isBasedOn:
      data.kind === "contest"
        ? data.body instanceof Promise
          ? undefined
          : data.body.contest.sets.flatMap(({ source }) =>
              source.url == null ? [] : [source.url],
            )
        : undefined,
    distribution: [
      ["application/json", "json"],
      ["text/markdown", "md"],
    ].map(([type, suffix]) => ({
      "@type": "DataDownload",
      encodingFormat: type,
      contentUrl: `${page.url.origin}${visible.pathname}.${suffix}${query}`,
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
  <link
    rel="alternate"
    type="application/json"
    href="{page.url.origin}{visible.pathname}.json{query}"
  />
  <link
    rel="alternate"
    type="text/markdown"
    href="{page.url.origin}{visible.pathname}.md{query}"
  />
  {#if data.kind === "contest" && data.head.scope.isFine}
    <meta name="robots" content="noindex" />
  {/if}
  <svelte:element this={"script"} type="application/ld+json"
    >{jsonld}</svelte:element
  >
</svelte:head>

<ShareImage src="{page.url.origin}/og{page.url.pathname}.png" alt={title} />
<PreviewHost />

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
      <data.View
        id={data.id}
        talk={data.talk}
        unit={data.unit}
        head={data.head}
        body={data.body}
        primed={data.primed}
        ballot={data.ballot}
        standing={data.standing}
        crowned={data.crowned}
        match={data.match}
        live={data.live}
      />
    {:else if data.kind === "election"}
      {#key `${data.id}${query}`}
        <data.View
          id={data.id}
          path={data.path}
          {query}
          chosen={picked}
          scope={data.scope}
          head={data.head}
          body={data.body}
          primed={data.primed}
          sameDay={data.sameDay}
          sections={data.sections}
          live={data.live}
        />
      {/key}
    {:else if data.kind === "day"}
      <data.View {data} />
    {:else}
      {#key data.id}
        <data.View id={data.id} polity={data.polity} />
      {/key}
    {/if}
  </article>
</main>
