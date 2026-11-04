<script lang="ts">
  import { dev } from "$app/env";
  import { page } from "$app/state";
  import { untrack } from "svelte";
  import Board from "#lib/components/live/board/board.svelte";
  import { Desk } from "#lib/live/board/desk.svelte.js";
  import { Feed } from "#lib/live/board/feed.svelte.js";
  import { localize } from "#lib/live/prefs.svelte.js";
  import { m } from "#lib/paraglide/messages.js";
  import type { PageProps } from "./$types";

  const { data }: PageProps = $props();

  const feed = new Feed(
    untrack(() => ({
      election: data.election,
      estimates: data.estimates,
      shown: data.shown,
    })),
    dev && page.url.searchParams.has("rehearsal"),
  );
  const desk = new Desk(
    feed,
    untrack(() => data.cartogram),
    untrack(() => data.control),
  );
  const requested = page.url.searchParams.get("race");
  if (requested != null && feed.bySlug.has(requested)) {
    desk.select(requested);
  }

  const title = $derived(m.board_seo_title({ title: data.election.title }));
  const description = $derived(
    m.board_seo_description({ title: data.election.title }),
  );
  const canonical = $derived(`${page.url.origin}${page.url.pathname}`);
  const image = $derived(
    `${page.url.origin}/og/live/${data.election.slug}.png${data.election.newest == null ? "" : `?v=${encodeURIComponent(data.election.newest)}`}`,
  );

  $effect(() => feed.start());

  $effect(localize);
</script>

<svelte:head>
  <title>{title} | {m.wordmark()}</title>
  <meta name="description" content={description} />
  <meta name="robots" content="noindex, follow" />
  <link rel="canonical" href={canonical} />
  <meta property="og:type" content="website" />
  <meta property="og:site_name" content={m.wordmark()} />
  <meta property="og:locale" content="en_US" />
  <meta property="og:title" content={title} />
  <meta property="og:description" content={description} />
  <meta property="og:url" content={canonical} />
  <meta property="og:image" content={image} />
  <meta property="og:image:secure_url" content={image} />
  <meta property="og:image:type" content="image/png" />
  <meta property="og:image:width" content="2400" />
  <meta property="og:image:height" content="1260" />
  <meta property="og:image:alt" content={title} />
  <meta name="twitter:card" content="summary_large_image" />
  <meta name="twitter:title" content={title} />
  <meta name="twitter:description" content={description} />
  <meta name="twitter:image" content={image} />
</svelte:head>

<Board {desk} />
