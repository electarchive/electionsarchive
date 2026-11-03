<script lang="ts">
  import { page } from "$app/state";
  import { count } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { getLocale } from "#lib/paraglide/runtime.js";
  import {
    categoryLedes,
    categoryTitles,
    figures,
    leadFigure,
    supplyLabels,
    years,
  } from "#lib/source-kinds.js";
  import type { PageData } from "./$types";

  const { data }: { data: PageData } = $props();

  type Source = PageData["categories"][number]["sources"][number];

  const regions = new Intl.DisplayNames([getLocale()], { type: "region" });

  const country = $derived(page.url.searchParams.get("country"));

  function region(code: string) {
    return regions.of(code) ?? code;
  }

  function within(source: Source) {
    return country == null || source.countries.includes(country);
  }

  function members(source: Source) {
    return country == null
      ? source.members
      : source.members.filter((member) => member.countries.includes(country));
  }

  function size(source: Source) {
    return source.isGroup ? members(source).length + source.feeds.length : 1;
  }

  const countries = $derived.by(() => {
    const seen = new Map<string, number>();
    for (const category of data.categories) {
      for (const source of category.sources) {
        for (const code of source.countries) {
          seen.set(code, (seen.get(code) ?? 0) + size(source));
        }
      }
    }
    return [...seen]
      .toSorted(
        (a, b) => b[1] - a[1] || region(a[0]).localeCompare(region(b[0])),
      )
      .map(([code]) => code);
  });

  const shown = $derived(
    data.categories
      .map((category) => {
        const sources = category.sources.filter((source) => within(source));
        let total = 0;
        for (const source of sources) {
          total += size(source);
        }
        return { ...category, sources, total };
      })
      .filter((category) => category.sources.length > 0),
  );

  const opened = $state<Record<string, boolean>>({});
</script>

<svelte:head>
  <title>{m.sources_title()} | {m.wordmark()}</title>
  <meta name="description" content={m.sources_page_description()} />
  <link rel="canonical" href="{page.url.origin}{page.url.pathname}" />
  <link rel="alternate" type="text/markdown" href="{page.url.pathname}.md" />
</svelte:head>

<main class="mx-auto grid gap-10 px-4 pbs-8 pbe-24 max-inline-6xl max-sm:px-3">
  <header class="grid gap-3 max-inline-[68ch]">
    <h1 class="title">{m.sources_title()}</h1>
    <p class="text-lg text-muted-foreground">
      {m.sources_page_lede({ count: count(data.total) })}
    </p>
  </header>

  <div class="grid gap-10 lg:grid-cols-[13rem_minmax(0,1fr)] lg:gap-12">
    <aside
      class="grid content-start gap-8 lg:sticky lg:inset-bs-6 lg:self-start lg:overflow-y-auto lg:max-block-[calc(100dvh-3rem)]"
    >
      <nav aria-labelledby="sources-index" class="grid gap-2">
        <h2 id="sources-index" class="text-xs text-muted-foreground">
          {m.sources_page_index()}
        </h2>
        <ol
          class="-mx-4 flex gap-x-4 gap-y-1 overflow-x-auto px-4 pbe-1 text-sm lg:mx-0 lg:grid lg:px-0 max-sm:-mx-3 max-sm:px-3"
        >
          {#each shown as category (category.id)}
            <li class="shrink-0">
              <a
                href="#{category.id}"
                class="flex items-baseline justify-between gap-3 hover:underline focus-visible:underline"
                >{categoryTitles[category.id]()}<span
                  class="font-figures text-[0.8125rem] text-muted-foreground tabular-nums"
                  >{count(category.total)}</span
                ></a
              >
            </li>
          {/each}
        </ol>
      </nav>

      <nav aria-labelledby="sources-country" class="grid gap-2">
        <h2 id="sources-country" class="text-xs text-muted-foreground">
          {m.sources_page_country()}
        </h2>
        <ul
          class="-mx-4 flex gap-1 overflow-x-auto px-4 pbe-1 text-[0.8125rem] lg:mx-0 lg:flex-wrap lg:px-0 max-sm:-mx-3 max-sm:px-3"
        >
          <li class="shrink-0">
            <a
              href={page.url.pathname}
              data-sveltekit-reset="false"
              data-sveltekit-replacestate
              aria-current={country == null ? "page" : undefined}
              class="block rounded-sm border px-2 py-1 hover:highlighter focus-visible:outline-2 focus-visible:outline-ring aria-[current=page]:border-primary aria-[current=page]:bg-primary aria-[current=page]:text-primary-foreground"
              >{m.sources_page_all()}</a
            >
          </li>
          {#each countries as code (code)}
            <li class="shrink-0">
              <a
                href="?country={code}"
                data-sveltekit-reset="false"
                data-sveltekit-replacestate
                aria-current={country === code ? "page" : undefined}
                class="block rounded-sm border px-2 py-1 whitespace-nowrap hover:highlighter focus-visible:outline-2 focus-visible:outline-ring aria-[current=page]:border-primary aria-[current=page]:bg-primary aria-[current=page]:text-primary-foreground"
                >{region(code)}</a
              >
            </li>
          {/each}
        </ul>
      </nav>
    </aside>

    <div class="grid content-start gap-14">
      {#each shown as category (category.id)}
        <section
          id={category.id}
          aria-labelledby="{category.id}-title"
          class="grid scroll-mbs-6 gap-4 border-bs-[3px] border-foreground pbs-3"
        >
          <div class="grid gap-1.5">
            <h2 id="{category.id}-title" class="heading">
              {categoryTitles[category.id]()}
            </h2>
            <p class="text-[0.9375rem] text-muted-foreground max-inline-[68ch]">
              {categoryLedes[category.id]()}
            </p>
          </div>

          <ol class="grid">
            {#each category.sources as source (source.id)}
              {const listed = $derived(members(source))}
              {const held = $derived(figures(source))}
              {const span = $derived(years(source))}
              <li
                id={source.id}
                class="grid scroll-mbs-6 gap-x-10 gap-y-3 border-bs py-5 md:grid-cols-[minmax(0,16rem)_minmax(0,1fr)]"
              >
                <div class="grid content-start gap-1.5">
                  <h3 class="text-[1.0625rem]/snug font-[680]">
                    {#if source.url != null}
                      <a
                        href={source.url}
                        rel="external noopener"
                        class="hover:underline hover:decoration-1 hover:underline-offset-4 focus-visible:outline-2 focus-visible:outline-ring"
                        >{source.name}</a
                      >
                    {:else}
                      {source.name}
                    {/if}
                    {#if source.isUnofficial}
                      <span
                        class="ms-1.5 inline-block rounded-sm border px-1.5 align-[0.1em] text-xs font-medium text-muted-foreground"
                        >{m.sources_page_unofficial()}</span
                      >
                    {/if}
                  </h3>
                  <p class="text-[0.8125rem] text-muted-foreground">
                    {source.countries.length === 0
                      ? m.sources_page_international()
                      : source.countries.map((code) => region(code)).join(", ")}
                  </p>
                  <ul class="flex flex-wrap gap-1 pbs-0.5">
                    {#each source.supplies as supply (supply)}
                      <li
                        class="rounded-sm bg-muted px-1.5 py-px text-xs text-foreground/80"
                      >
                        {supplyLabels[supply]()}
                      </li>
                    {/each}
                  </ul>
                  {#if span != null || held.length > 0}
                    <p
                      class="grid gap-0.5 pbs-1 font-figures text-[0.8125rem] text-muted-foreground tabular-nums"
                    >
                      {#if span != null}
                        <span class="text-foreground">{span}</span>
                      {/if}
                      {#each held as figure (figure)}
                        <span>{figure}</span>
                      {/each}
                    </p>
                  {/if}
                </div>

                <div class="grid content-start gap-3 max-inline-[64ch]">
                  {#if source.quote != null}
                    <figure class="grid gap-1.5">
                      <blockquote
                        lang={source.quote.lang}
                        cite={source.quote.url}
                        class="font-serif text-[1.0625rem]/[1.55] text-pretty"
                      >
                        <p>
                          <span aria-hidden="true" class="ms-[-0.45em]">“</span
                          >{source.quote.text}<span aria-hidden="true">”</span>
                        </p>
                      </blockquote>
                      <figcaption
                        class="text-[0.8125rem] text-muted-foreground"
                      >
                        <a
                          href={source.quote.url}
                          rel="external noopener"
                          class="underline decoration-border underline-offset-4 hover:text-foreground hover:decoration-current focus-visible:outline-2 focus-visible:outline-ring"
                          >{m.sources_page_quoted()}</a
                        >
                      </figcaption>
                    </figure>
                  {:else if source.about != null}
                    <p class="text-[0.9375rem]/relaxed text-pretty">
                      {source.about}
                    </p>
                  {/if}
                  {#if source.license != null}
                    <p class="text-[0.8125rem] text-muted-foreground">
                      {m.sources_page_terms({ license: source.license })}
                    </p>
                  {/if}
                  {#if listed.length > 0 || source.feeds.length > 0}
                    <details
                      class="text-sm"
                      ontoggle={(event) => {
                        opened[source.id] = event.currentTarget.open;
                      }}
                      {@attach (node: HTMLDetailsElement) => {
                        if (node.open) {
                          opened[source.id] = true;
                        }
                      }}
                    >
                      <summary
                        class="cursor-pointer text-[0.8125rem] font-medium inline-fit marker:text-muted-foreground hover:underline focus-visible:outline-2 focus-visible:outline-ring"
                      >
                        {source.feeds.length > 0
                          ? m.sources_page_feeds({
                              count: count(source.feeds.length),
                            })
                          : m.sources_page_members({
                              count: count(listed.length),
                            })}
                      </summary>
                      {#if opened[source.id] === true}
                        <ul
                          class="mbs-3 columns-1 gap-8 border-bs pbs-2 sm:columns-2"
                        >
                          {#each source.feeds as feed (feed.url)}
                            <li class="break-inside-avoid py-1">
                              <a
                                href={feed.url}
                                rel="external noopener"
                                class="underline decoration-border underline-offset-4 hover:text-foreground hover:decoration-current focus-visible:outline-2 focus-visible:outline-ring"
                                >{feed.name}</a
                              >
                            </li>
                          {/each}
                          {#each listed as member (member.name)}
                            {const detail = $derived(leadFigure(member))}
                            <li
                              class="flex break-inside-avoid items-baseline justify-between gap-3 py-1"
                            >
                              {#if member.url != null}
                                <a
                                  href={member.url}
                                  rel="external noopener"
                                  class="wrap-break-word underline decoration-border underline-offset-4 min-inline-0 hover:text-foreground hover:decoration-current focus-visible:outline-2 focus-visible:outline-ring"
                                  >{member.name}</a
                                >
                              {:else}
                                <span class="wrap-break-word min-inline-0"
                                  >{member.name}</span
                                >
                              {/if}
                              {#if detail != null}
                                <span
                                  class="shrink-0 font-figures text-xs text-muted-foreground tabular-nums"
                                  >{detail}</span
                                >
                              {/if}
                            </li>
                          {/each}
                        </ul>
                      {/if}
                    </details>
                  {/if}
                </div>
              </li>
            {/each}
          </ol>
        </section>
      {/each}
    </div>
  </div>
</main>
