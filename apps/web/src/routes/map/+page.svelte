<script lang="ts">
  import { resolve } from "$app/paths";
  import { page } from "$app/state";
  import { count, percent } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";
  import type { PageData } from "./$types";

  const { data }: { data: PageData } = $props();

  function strip(lead: Awaited<PageData["leads"]>[number]) {
    if (lead == null || lead.results.length === 0) {
      return null;
    }
    const isSeated = lead.contested > 1;
    const total = isSeated ? lead.contested : 1;
    const top = lead.results[0];
    if (top == null) {
      return null;
    }
    return {
      parts: lead.results.map((row) => ({
        key: row.key,
        color: swatch(row.color, row.key),
        width: ((isSeated ? (row.seats ?? 0) : row.share) / total) * 100,
      })),
      label:
        isSeated && top.person == null ? (top.short ?? top.label) : top.label,
      value: isSeated
        ? m.lists_seats({ count: top.seats ?? 0 })
        : percent(top.share),
      color: swatch(top.color, top.key),
    };
  }
</script>

<svelte:head>
  <title>{m.map_landing_title()} | {m.wordmark()}</title>
  <link rel="canonical" href="{page.url.origin}{page.url.pathname}" />
</svelte:head>

<main class="pbe-16">
  <article class="grid overflow-clip border-be">
    <header class="grid gap-1 border-be bg-card px-3.5 pbs-4 pbe-3.5">
      <h1 class="title">{m.map_landing_title()}</h1>
      <p
        class="text-[0.9375rem] text-pretty text-muted-foreground max-inline-[64ch]"
      >
        {m.map_landing_lede()}
      </p>
    </header>
    <ul
      class="-me-px grid bg-card sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4"
    >
      {#each data.polities as polity, index (polity.path)}
        <li class="grid border-e border-be border-border">
          <a
            href={polity.isCounting
              ? resolve("/live/map/[...path]", { path: polity.latest.path })
              : resolve("/map/[...path]", { path: polity.latest.path })}
            class="group grid content-start gap-2.5 px-3.5 pbs-3 pbe-3.5 hover:bg-secondary focus-visible:bg-secondary"
          >
            <span class="flex items-baseline justify-between gap-3">
              <span class="truncate text-lg/tight font-[720]"
                >{polity.name}</span
              >
              <span
                class="shrink-0 font-figures text-xs text-muted-foreground tabular-nums"
                >{m.map_landing_elections({
                  count: count(polity.elections),
                })}</span
              >
            </span>
            <span
              class="line-clamp-1 text-[0.8125rem] text-muted-foreground group-hover:text-foreground"
              >{polity.latest.name}</span
            >
            {#if polity.isCounting}
              <span class="grid gap-1.5">
                <span
                  class="rounded-xs bg-secondary block-1.5"
                  aria-hidden="true"
                ></span>
                <span
                  class="flex items-center gap-1.5 text-[0.8125rem] font-semibold block-4"
                >
                  <span
                    class="shrink-0 rounded-full bg-destructive block-2 inline-2"
                    aria-hidden="true"
                  ></span>
                  {m.map_landing_counting()}
                </span>
              </span>
            {:else}
              {#await data.leads}
                <span class="grid gap-1.5" aria-hidden="true">
                  <span class="rounded-xs bg-secondary block-1.5"></span>
                  <span class="block-4"></span>
                </span>
              {:then leads}
                {const shown = $derived(strip(leads[index] ?? null))}
                <span class="grid gap-1.5">
                  <span
                    class="flex overflow-clip rounded-xs bg-secondary block-1.5"
                    aria-hidden="true"
                  >
                    {#each shown?.parts ?? [] as part (part.key)}
                      <span
                        class="block-full"
                        style:background={part.color}
                        style:inline-size="{part.width}%"
                      ></span>
                    {/each}
                  </span>
                  <span
                    class="flex items-baseline justify-between gap-3 text-[0.8125rem] block-4"
                  >
                    {#if shown != null}
                      <span class="flex items-baseline gap-1.5 min-inline-0">
                        <span
                          class="shrink-0 self-center rounded-xs block-2 inline-2"
                          style:background={shown.color}
                        ></span>
                        <span class="truncate font-semibold">{shown.label}</span
                        >
                      </span>
                      <span class="shrink-0 font-figures tabular-nums"
                        >{shown.value}</span
                      >
                    {/if}
                  </span>
                </span>
              {/await}
            {/if}
          </a>
        </li>
      {/each}
    </ul>
  </article>
</main>
