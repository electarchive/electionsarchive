<script lang="ts">
  import { resolve } from "$app/paths";
  import { page } from "$app/state";
  import SearchIcon from "@lucide/svelte/icons/search";
  import { MediaQuery } from "svelte/reactivity";
  import ShareImage from "#lib/components/share-image.svelte";
  import { date, dates } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { finder, summon } from "#lib/search.svelte.js";
  import { cn } from "#lib/utils.js";
  import type { PageData } from "./$types";
  import Lead from "./lead.svelte";
  import Strata from "./strata.svelte";

  const { data }: { data: PageData } = $props();

  const EXAMPLES = [
    { term: "1860 presidential", hint: m.home_try_lincoln() },
    { term: "1832", hint: m.home_try_reform() },
    { term: "EU referendum", hint: m.home_try_brexit() },
    { term: "Bundestag", hint: m.home_try_bundestag() },
    { term: "sheriff", hint: m.home_try_sheriff() },
    { term: "Swiss referendums", hint: m.home_try_swiss() },
  ];
  const DAY = 86_400_000;

  const isNarrow = new MediaQuery("max-width: 40rem");

  const jsonLd = $derived({
    "@context": "https://schema.org",
    "@graph": [
      {
        "@type": "Organization",
        "@id": `${page.url.origin}/#organization`,
        name: m.wordmark(),
        url: `${page.url.origin}/`,
        logo: `${page.url.origin}/apple-touch-icon.png`,
      },
      {
        "@type": "WebSite",
        "@id": `${page.url.origin}/#website`,
        name: m.wordmark(),
        url: `${page.url.origin}/`,
        description: m.home_meta_description(),
        inLanguage: "en-US",
        publisher: { "@id": `${page.url.origin}/#organization` },
        potentialAction: {
          "@type": "SearchAction",
          target: `${page.url.origin}/search?q={query}`,
          "query-input": "required name=query",
        },
      },
    ],
  });
  const upcoming = $derived(
    data.upcoming.map((election) => {
      const days = Math.round(
        (Date.parse(`${election.date}T00:00:00Z`) -
          Date.parse(`${data.today}T00:00:00Z`)) /
          DAY,
      );
      return {
        ...election,
        days,
        isLive:
          days <= 7 && /^us\/\d{4}-\d{2}-\d{2}-general$/u.test(election.path),
      };
    }),
  );
  const next = $derived(
    upcoming[0]?.days != null && upcoming[0].days <= 14 ? upcoming[0] : null,
  );
  const later = $derived(next == null ? upcoming : upcoming.slice(1));
  const decided = $derived(
    data.decided.map((election) => {
      const days = Math.round(
        (Date.parse(`${data.today}T00:00:00Z`) -
          Date.parse(`${election.date}T00:00:00Z`)) /
          DAY,
      );
      return {
        ...election,
        days,
        isPending: election.lead == null || election.lead.results.length === 0,
        isLive:
          days <= 7 && /^us\/\d{4}-\d{2}-\d{2}-general$/u.test(election.path),
      };
    }),
  );
  const held = $derived(
    decided[0] != null && decided[0].days <= 7 ? decided[0] : null,
  );
  const earlier = $derived(held == null ? decided : decided.slice(1));
  const places = $derived(
    data.polities
      .flatMap((polity) =>
        polity.path == null ? [] : [{ ...polity, path: polity.path }],
      )
      .toSorted((a, b) => a.name.localeCompare(b.name)),
  );
  const tools = $derived([
    {
      key: "twins",
      href: `${resolve("/[...path]", { path: "us/2024-11-05-general" })}.md`,
      title: m.home_tools_twins(),
      body: m.home_tools_twins_body(),
    },
    {
      key: "agents",
      href: resolve("/agents"),
      title: m.home_tools_agents(),
      body: m.home_tools_agents_body(),
    },
    {
      key: "coverage",
      href: resolve("/coverage"),
      title: m.home_tools_coverage(),
      body: m.home_tools_coverage_body(),
    },
    {
      key: "compare",
      href: resolve("/compare"),
      title: m.home_tools_compare(),
      body: m.home_tools_compare_body(),
    },
  ]);

  function since(days: number, value: string) {
    if (days <= 0) {
      return m.home_next_today();
    }
    if (days === 1) {
      return m.home_decided_yesterday();
    }
    return dates({
      weekday: "long",
      month: "long",
      day: "numeric",
      timeZone: "UTC",
    }).format(new Date(`${value}T00:00:00Z`));
  }

  function when(days: number, value: string) {
    if (days <= 0) {
      return m.home_next_today();
    }
    if (days === 1) {
      return m.home_next_tomorrow();
    }
    return dates({
      weekday: days < 7 ? "long" : undefined,
      month: "long",
      day: "numeric",
      timeZone: "UTC",
    }).format(new Date(`${value}T00:00:00Z`));
  }

  const past = $derived(
    data.past.map((election) => ({
      ...election,
      title: election.name
        .replace(/^\d{4} /u, "")
        .replace(/(?:, \p{L}+ \d{1,2}, \d{4}| \(\p{L}+ \d{1,2}\))$/u, ""),
    })),
  );

  function day(value: string) {
    return dates({ month: "long", day: "numeric", timeZone: "UTC" }).format(
      new Date(`${value}T00:00:00Z`),
    );
  }

  function seek(event: Event, term: string) {
    event.preventDefault();
    finder.value = term;
    finder.isOpen = true;
  }
</script>

<svelte:head>
  <title>{m.home_meta_title()}</title>
  <meta name="description" content={m.home_meta_description()} />
  <meta
    name="robots"
    content="index, follow, max-image-preview:large, max-snippet:-1"
  />
  <link rel="canonical" href="{page.url.origin}/" />
  <meta property="og:type" content="website" />
  <meta property="og:title" content={m.wordmark()} />
  <meta property="og:description" content={m.home_meta_description()} />
  <meta property="og:url" content="{page.url.origin}/" />
  <meta property="og:video" content="{page.url.origin}/og.mp4" />
  <meta property="og:video:secure_url" content="{page.url.origin}/og.mp4" />
  <meta property="og:video:type" content="video/mp4" />
  <meta property="og:video:width" content="1920" />
  <meta property="og:video:height" content="1008" />
  <svelte:element this={"script"} type="application/ld+json"
    >{JSON.stringify(jsonLd)
      .replaceAll("<", String.raw`\u003c`)
      .replaceAll("&", String.raw`\u0026`)}</svelte:element
  >
</svelte:head>

<ShareImage
  src="{page.url.origin}/og.jpg"
  alt={m.home_og_alt()}
  type="image/jpeg"
/>

<main class="grid pbe-16">
  <header
    class="grid gap-x-12 gap-y-7 border-be px-3.5 pbs-8 pbe-7 sm:pbs-12 lg:grid-cols-[minmax(0,7fr)_minmax(0,5fr)]"
  >
    <div class="grid content-start gap-5">
      <h1
        class="text-[clamp(2.25rem,1.3rem+3.4vw,4.25rem)] leading-[0.95] font-extrabold tracking-[-0.03em] text-balance max-inline-[16ch]"
      >
        {m.home_headline()}
      </h1>
      <p
        class="font-serif text-[1.0625rem]/relaxed text-pretty text-muted-foreground max-inline-[52ch]"
      >
        {m.home_lede()}
      </p>
      <form
        class="grid gap-2 max-inline-xl"
        role="search"
        action={resolve("/search")}
        method="get"
        onsubmit={(event) =>
          seek(event, String(new FormData(event.currentTarget).get("q") ?? ""))}
      >
        <label class="sr-only" for="home-search">{m.home_search_label()}</label>
        <span
          class="flex items-center gap-3 rounded-sm border border-foreground bg-card ps-3 pe-1.5 block-12 focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-ring"
        >
          <SearchIcon class="shrink-0 block-5 inline-5" aria-hidden="true" />
          <input
            id="home-search"
            class="grow bg-transparent text-base outline-none min-inline-0 placeholder:text-muted-foreground"
            type="search"
            name="q"
            autocomplete="off"
            aria-haspopup="dialog"
            onclick={(event) => {
              summon(event.currentTarget.value);
              event.currentTarget.value = "";
            }}
            oninput={(event) => {
              summon(event.currentTarget.value);
              event.currentTarget.value = "";
            }}
            placeholder={isNarrow.current
              ? m.home_search_cta_short()
              : m.home_search_cta()}
          />
          <kbd class="text-xs text-muted-foreground max-sm:hidden">⌘K</kbd>
          <button
            type="submit"
            class="rounded-xs bg-primary px-3 text-sm font-semibold text-primary-foreground block-9 hover:bg-primary/90"
            >{m.home_search_submit()}</button
          >
        </span>
        <div class="grid gap-2 pbs-3">
          <p class="text-[0.8125rem] text-muted-foreground">
            {m.home_search_try()}
          </p>
          <ul class="grid grid-cols-2 gap-1.5 sm:flex sm:flex-wrap">
            {#each EXAMPLES as example (example.term)}
              <li class="grid">
                <a
                  class="group grid gap-0.5 rounded-xs border border-border bg-card px-2.5 py-1.5 leading-tight transition-colors hover:border-foreground focus-visible:border-foreground"
                  href="{resolve('/search')}?q={encodeURIComponent(
                    example.term,
                  )}"
                  onclick={(event) => seek(event, example.term)}
                >
                  <span class="text-sm font-semibold">{example.term}</span>
                  <span
                    class="text-xs text-muted-foreground group-hover:text-foreground"
                    >{example.hint}</span
                  >
                </a>
              </li>
            {/each}
          </ul>
        </div>
      </form>
    </div>

    {#if upcoming.length > 0 || decided.length > 0}
      <div class="grid content-start gap-7 lg:border-s lg:ps-8">
        {#if decided.length > 0}
          <section
            class="grid content-start gap-3"
            aria-labelledby="decided-title"
          >
            <h2 id="decided-title" class="text-[0.8125rem] font-bold">
              {m.home_decided()}
            </h2>
            {#if held != null}
              <div class="grid gap-2 border-be pbe-4">
                <p
                  class="font-figures text-[clamp(1.75rem,1.4rem+1.2vw,2.5rem)] leading-none font-[780] tracking-tight"
                >
                  <time datetime={held.date}>{since(held.days, held.date)}</time
                  >
                </p>
                <a
                  class="justify-self-start text-lg/tight font-[720] text-balance underline decoration-transparent underline-offset-4 hover:decoration-foreground"
                  href={resolve("/[...path]", { path: held.path })}
                  >{held.name}</a
                >
                {#if held.isPending}
                  <p class="text-sm text-muted-foreground">
                    {m.home_decided_pending()}
                  </p>
                {:else}
                  {#if held.lead?.question == null && held.lead?.name != null && held.lead.name !== held.name}
                    <p class="line-clamp-1 text-sm text-muted-foreground">
                      {held.lead.name}
                    </p>
                  {/if}
                  <div class="pbs-1"><Lead race={held.lead} /></div>
                {/if}
                <p class="flex flex-wrap gap-x-4 gap-y-1 pbs-1 text-sm">
                  {#if held.isLive}
                    <a
                      class="inline-flex items-center gap-2 rounded-sm bg-primary px-3 font-semibold text-primary-foreground block-9 hover:bg-primary/90"
                      href={resolve("/live/[...path]", { path: held.path })}
                      ><span
                        class="rounded-full bg-marker block-2 inline-2"
                        aria-hidden="true"
                      ></span>{m.home_next_live()}</a
                    >
                  {/if}
                  <a
                    class="inline-flex items-center font-semibold underline decoration-border underline-offset-4 block-9 hover:decoration-foreground"
                    href={resolve("/[...path]", { path: held.path })}
                    >{m.home_next_open()}</a
                  >
                </p>
              </div>
            {/if}
            {#if earlier.length > 0}
              <ol
                class={cn("grid border-border/70", {
                  "border-bs": held == null,
                })}
              >
                {#each earlier as election (election.path)}
                  <li class="border-be border-border/70">
                    <a
                      class="group grid items-baseline gap-x-3 gap-y-1 py-3 sm:grid-cols-[7.5rem_minmax(0,1fr)]"
                      href={resolve("/[...path]", { path: election.path })}
                    >
                      <time
                        class="font-figures text-[0.8125rem] text-muted-foreground tabular-nums"
                        datetime={election.date}>{day(election.date)}</time
                      >
                      <span class="grid gap-2 min-inline-0">
                        <span
                          class="text-sm font-semibold text-pretty underline decoration-transparent underline-offset-4 group-hover:decoration-foreground"
                          >{election.name}</span
                        >
                        {#if election.isPending}
                          <span class="text-xs text-muted-foreground"
                            >{m.home_decided_pending()}</span
                          >
                        {:else}
                          {#if election.lead?.question == null && election.lead?.name != null && election.lead.name !== election.name}
                            <span
                              class="line-clamp-1 text-xs text-muted-foreground"
                              >{election.lead.name}</span
                            >
                          {/if}
                          <Lead race={election.lead} isCompact />
                        {/if}
                      </span>
                    </a>
                  </li>
                {/each}
              </ol>
            {/if}
          </section>
        {/if}
        {#if upcoming.length > 0}
          <section
            class="grid content-start gap-3"
            aria-labelledby="next-title"
          >
            <h2 id="next-title" class="text-[0.8125rem] font-bold">
              {m.home_next()}
            </h2>
            {#if next != null}
              <div class="grid gap-2 border-be pbe-4">
                <p
                  class="font-figures text-[clamp(1.75rem,1.4rem+1.2vw,2.5rem)] leading-none font-[780] tracking-tight"
                >
                  <time datetime={next.date}>{when(next.days, next.date)}</time>
                </p>
                <a
                  class="justify-self-start text-lg/tight font-[720] text-balance underline decoration-transparent underline-offset-4 hover:decoration-foreground"
                  href={resolve("/[...path]", { path: next.path })}
                  >{next.name}</a
                >
                <p class="flex flex-wrap gap-x-4 gap-y-1 pbs-1 text-sm">
                  {#if next.isLive}
                    <a
                      class="inline-flex items-center gap-2 rounded-sm bg-primary px-3 font-semibold text-primary-foreground block-9 hover:bg-primary/90"
                      href={resolve("/live/[...path]", { path: next.path })}
                      ><span
                        class="rounded-full bg-marker block-2 inline-2"
                        aria-hidden="true"
                      ></span>{m.home_next_live()}</a
                    >
                  {/if}
                  <a
                    class="inline-flex items-center font-semibold underline decoration-border underline-offset-4 block-9 hover:decoration-foreground"
                    href={resolve("/[...path]", { path: next.path })}
                    >{m.home_next_open()}</a
                  >
                  <a
                    class="inline-flex items-center gap-2 rounded-sm border border-input px-3 font-semibold block-9 hover:bg-accent"
                    href={resolve("/agents")}
                    ><svg
                      class="block-4 inline-4"
                      viewBox="0 0 16 16"
                      fill="none"
                      stroke="currentColor"
                      stroke-width="1.5"
                      stroke-linecap="round"
                      aria-hidden="true"
                      ><path d="M8 1.5v2.5" /><circle
                        cx="8"
                        cy="1.5"
                        r="0.75"
                        class="fill-marker stroke-none"
                      /><rect
                        x="2.5"
                        y="4"
                        width="11"
                        height="9"
                        rx="2.5"
                      /><path d="M6 8v1M10 8v1" /></svg
                    >{m.home_agents()}</a
                  >
                </p>
              </div>
            {/if}
            {#if later.length > 0}
              <ul class="grid gap-1.5 text-sm">
                {#each later as election (election.path)}
                  <li
                    class="grid grid-cols-[7.5rem_minmax(0,1fr)] items-baseline gap-x-3"
                  >
                    <time
                      class="font-figures text-[0.8125rem] text-muted-foreground tabular-nums"
                      datetime={election.date}
                      >{when(election.days, election.date)}</time
                    >
                    <a
                      class="font-semibold text-pretty underline decoration-transparent underline-offset-4 hover:decoration-foreground"
                      href={resolve("/[...path]", { path: election.path })}
                      >{election.name}</a
                    >
                  </li>
                {/each}
              </ul>
            {/if}
          </section>
        {/if}
      </div>
    {/if}
  </header>

  {#if data.strata.rows.length > 0}
    <section
      class="grid gap-5 border-be px-3.5 pbs-8 pbe-8"
      aria-labelledby="strata-title"
    >
      <div class="grid gap-1.5">
        <h2 id="strata-title" class="heading">{m.home_strata_title()}</h2>
        <p
          class="text-[0.8125rem] text-pretty text-muted-foreground max-inline-[72ch]"
        >
          {m.home_strata_lede()}
        </p>
      </div>
      <Strata strata={data.strata} today={data.today} />
    </section>
  {/if}

  {#if data.recent.length > 0}
    <section
      class="grid gap-1 border-be px-3.5 pbs-6 pbe-2"
      aria-labelledby="recent-title"
    >
      <h2 id="recent-title" class="heading">{m.home_recent()}</h2>
      <ol class="grid gap-x-10 md:grid-cols-2 xl:grid-cols-3">
        {#each data.recent as election (election.path)}
          <li class="border-be border-border/70">
            <a
              class="group grid gap-3 py-4"
              href={resolve("/[...path]", { path: election.path })}
            >
              <span class="grid gap-1">
                <span
                  class="text-lg/tight font-[740] tracking-[-0.012em] text-balance underline decoration-transparent underline-offset-4 group-hover:decoration-foreground"
                  >{election.name}</span
                >
                <time
                  class="font-figures text-[0.8125rem] text-muted-foreground"
                  datetime={election.date}>{date(election.date)}</time
                >
              </span>
              <Lead race={election.lead} />
            </a>
          </li>
        {/each}
      </ol>
    </section>
  {/if}

  <div class="grid border-be lg:grid-cols-[minmax(0,7fr)_minmax(0,5fr)]">
    {#if data.past.length > 0}
      <section
        class="grid content-start gap-3 px-3.5 pbs-6 pbe-6"
        aria-labelledby="past-title"
      >
        <h2 id="past-title" class="heading">{m.home_today()}</h2>
        <ol class="grid gap-x-6 sm:grid-cols-2">
          {#each past as election (election.path)}
            <li
              class="grid grid-cols-[3.75rem_minmax(0,1fr)] items-baseline gap-x-3 border-be border-border/60 py-2"
            >
              <span class="font-figures text-xl font-[760] tracking-[-0.02em]"
                >{election.date.slice(0, 4)}</span
              >
              <span class="grid gap-0.5">
                <a
                  class="justify-self-start text-[0.9375rem] font-semibold underline decoration-transparent underline-offset-4 hover:decoration-foreground"
                  href={resolve("/[...path]", { path: election.path })}
                  >{election.title}</a
                >
                <span class="text-xs text-muted-foreground"
                  >{[
                    election.polity != null &&
                    !election.title.includes(election.polity)
                      ? election.polity
                      : null,
                    dates({
                      month: "long",
                      day: "numeric",
                      timeZone: "UTC",
                    }).format(new Date(`${election.date}T00:00:00Z`)),
                  ]
                    .filter((part) => part != null)
                    .join(", ")}</span
                >
              </span>
            </li>
          {/each}
        </ol>
      </section>
    {/if}

    <section
      class="grid content-start gap-3 px-3.5 pbs-6 pbe-6 lg:border-s"
      aria-labelledby="tools-title"
    >
      <h2 id="tools-title" class="heading">{m.home_tools_title()}</h2>
      <ul class="grid gap-4 sm:grid-cols-2">
        {#each tools as tool (tool.key)}
          <li class="grid content-start gap-1">
            <a
              class="justify-self-start text-[0.9375rem] font-semibold underline decoration-border underline-offset-4 hover:decoration-foreground"
              href={tool.href}>{tool.title}</a
            >
            <p class="text-[0.8125rem] text-pretty text-muted-foreground">
              {tool.body}
            </p>
          </li>
        {/each}
      </ul>
    </section>
  </div>

  {#if places.length > 0}
    <section class="grid gap-3 px-3.5 pbs-6" aria-labelledby="places-title">
      <h2 id="places-title" class="heading">{m.home_places()}</h2>
      <ul class="columns-2 gap-x-6 text-sm sm:columns-3 lg:columns-5">
        {#each places as polity (polity.id)}
          <li
            class="flex break-inside-avoid flex-wrap items-baseline gap-x-2 py-0.5"
          >
            <a
              class="truncate font-semibold underline decoration-transparent underline-offset-4 hover:decoration-foreground"
              href={resolve("/[...path]", { path: polity.path })}
              >{polity.name}</a
            >
            <span
              class="font-figures text-xs whitespace-nowrap text-muted-foreground tabular-nums"
              >{m.home_since({ year: String(polity.first) })}</span
            >
          </li>
        {/each}
      </ul>
    </section>
  {/if}
</main>
