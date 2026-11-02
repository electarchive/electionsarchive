<script lang="ts">
  import type { PageData } from "./$types";

  const { data }: { data: PageData } = $props();
</script>

{#key data.number}
  {#if data.workspace.template === "election"}
    {const Election = $derived((await import("./election.svelte")).default)}
    <Election {data} workspace={data.workspace} />
  {:else if data.workspace.template === "results"}
    {const Results = $derived((await import("./results.svelte")).default)}
    <Results {data} workspace={data.workspace} />
  {:else if data.workspace.template === "merge"}
    {const Merge = $derived((await import("./merge.svelte")).default)}
    <Merge {data} workspace={data.workspace} />
  {:else if data.workspace.template === "lineage"}
    {const Lineage = $derived((await import("./lineage.svelte")).default)}
    <Lineage {data} workspace={data.workspace} />
  {:else if data.workspace.template === "translate"}
    {const Translate = $derived((await import("./translate.svelte")).default)}
    <Translate {data} workspace={data.workspace} />
  {:else if data.workspace.template === "boundaries"}
    {const Boundaries = $derived((await import("./boundaries.svelte")).default)}
    <Boundaries {data} workspace={data.workspace} />
  {:else}
    {const Table = $derived((await import("./table.svelte")).default)}
    <Table {data} workspace={data.workspace} />
  {/if}
{/key}
