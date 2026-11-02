<script lang="ts">
  import { page } from "$app/state";
  import Atlas from "#lib/atlas/atlas.svelte";
  import { sheetUrl } from "#lib/atlas/data.js";
  import type { PageData } from "./$types";

  const { data }: { data: PageData } = $props();

  const preload = $derived(
    sheetUrl(data.contest, {
      unit: decodeURI(page.url.pathname).slice(`/map/${data.path}`.length + 1),
      vs: page.url.searchParams.get("vs"),
      group: page.url.searchParams.get("group"),
      whole: data.whole,
    }),
  );
</script>

<svelte:head>
  <link rel="preload" as="fetch" crossorigin="anonymous" href={preload} />
</svelte:head>

{#key data.contest}
  <Atlas
    contest={data.contest}
    path={data.path}
    whole={data.whole}
    shell={data.shell}
    scheme={data.path.split("/")[0] ?? ""}
  />
{/key}
