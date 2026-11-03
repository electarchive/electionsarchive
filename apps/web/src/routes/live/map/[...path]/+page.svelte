<script lang="ts">
  import { refreshAll } from "$app/navigation";
  import { page } from "$app/state";
  import Full from "#lib/components/live/full/full.svelte";
  import { localize } from "#lib/live/prefs.svelte.js";
  import { m } from "#lib/paraglide/messages.js";
  import { orpc } from "#lib/rpc.js";
  import type { PageProps } from "./$types";

  const { data }: PageProps = $props();

  let clock = $state(Date.now());
  let isOffline = $state(false);

  const election = $derived(data.election);
  const heading = $derived(m.livemap_title({ title: election.title }));
  const description = $derived(
    m.livemap_description({ title: election.title }),
  );
  const canonical = $derived(`${page.url.origin}${page.url.pathname}`);

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

  $effect(localize);
</script>

<svelte:head>
  <title>{heading} | {m.wordmark()}</title>
  <meta name="description" content={description} />
  <link rel="canonical" href={canonical} />
  <meta property="og:type" content="website" />
  <meta property="og:site_name" content={m.wordmark()} />
  <meta property="og:title" content={heading} />
  <meta property="og:description" content={description} />
  <meta property="og:url" content={canonical} />
</svelte:head>

<Full
  {election}
  estimates={data.estimates}
  nation={data.nation}
  control={data.control}
  districts={data.districts}
  geography={data.geography}
  around={data.around}
  nationPath={data.nationPath}
  names={data.names}
  frame={data.frame}
  boxes={data.boxes}
  {clock}
  {isOffline}
  forecasts={data.shown.forecasts}
/>
