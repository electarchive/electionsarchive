<script lang="ts">
  import type { PageData } from "./$types";
  import Election from "./election.svelte";
  import Merge from "./merge.svelte";
  import Results from "./results.svelte";
  import Table from "./table.svelte";

  const { data }: { data: PageData } = $props();
</script>

{#key data.number}
  {#if data.workspace.template === "election"}
    <Election {data} workspace={data.workspace} />
  {:else if data.workspace.template === "results"}
    <Results {data} workspace={data.workspace} />
  {:else if data.workspace.template === "merge"}
    <Merge {data} workspace={data.workspace} />
  {:else}
    <Table {data} workspace={data.workspace} />
  {/if}
{/key}
