<script lang="ts">
  import { page } from "$app/state";
  import FeedList from "#lib/components/contrib/feed-list.svelte";
  import FilterNav from "#lib/components/contrib/filter-nav.svelte";
  import { tierLabel } from "#lib/contrib.js";
  import { m } from "#lib/paraglide/messages.js";
  import type { PageData } from "./$types";

  const { data }: { data: PageData } = $props();

  const query = (changes: Record<string, string | null>) => {
    const search = new URLSearchParams(page.url.search);
    for (const [key, value] of Object.entries(changes)) {
      if (value == null) {
        search.delete(key);
      } else {
        search.set(key, value);
      }
    }
    const text = search.toString();
    return text === "" ? "" : `?${text}`;
  };

  const feed = $derived(`/recent.atom${query({ before: null })}`);
</script>

<svelte:head>
  <title>{m.recent_title()}</title>
  <link
    rel="alternate"
    type="application/atom+xml"
    title={m.recent_feed_title()}
    href={feed}
  />
</svelte:head>

<main class="mx-auto px-4 pbs-6 pbe-24 max-inline-3xl max-sm:px-3">
  <header class="grid gap-3">
    <h1 class="title">{m.recent_heading()}</h1>
    <p class="max-inline-prose text-sm text-pretty text-muted-foreground">
      {m.recent_lede()}
    </p>
    <div class="grid gap-1.5">
      <FilterNav
        label={m.recent_filter_author()}
        name="author"
        options={[
          { value: null, label: m.recent_filter_all() },
          { value: "person", label: m.recent_author_person() },
          { value: "agent", label: m.recent_author_agent() },
          { value: "pipeline", label: m.recent_author_pipeline() },
        ]}
      />
      <FilterNav
        label={m.recent_filter_tier()}
        name="tier"
        options={[
          { value: null, label: m.recent_filter_all() },
          ...[1, 2, 3].map((tier) => ({
            value: String(tier),
            label: tierLabel[tier]?.() ?? String(tier),
          })),
        ]}
      />
    </div>
    <p class="flex flex-wrap items-baseline gap-x-4 text-sm">
      {#if data.polity != null}
        <span
          >{m.recent_in_polity({ polity: data.polity.name })}
          <a
            class="ms-2 text-muted-foreground underline underline-offset-2 hover:text-foreground"
            href="/recent{query({ polity: null, before: null })}"
            >{m.recent_clear_polity()}</a
          ></span
        >
      {/if}
      <a class="underline underline-offset-2" href={feed}
        >{m.recent_subscribe()}</a
      >
    </p>
  </header>

  {#if data.entries.length === 0}
    <p class="mbs-6 border-bs pbs-3 text-sm text-pretty text-muted-foreground">
      {m.recent_empty()}
    </p>
  {:else}
    <div class="mbs-6">
      <FeedList entries={data.entries} label={m.recent_heading()} />
    </div>
  {/if}

  {#if data.next != null}
    <p class="mbs-4 text-sm">
      <a
        class="underline underline-offset-2"
        href="/recent{query({ before: data.next })}">{m.recent_older()}</a
      >
    </p>
  {/if}
</main>
