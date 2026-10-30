<script lang="ts">
  import type { PageData } from "./$types";
  import Results from "./results.svelte";
  import Table from "./table.svelte";

  const { data }: { data: PageData } = $props();
</script>

{#key data.number}
  {#if data.workspace.template === "results"}
    <Results {data} workspace={data.workspace} />
  {:else}
    <Table {data} workspace={data.workspace} />
  {/if}
{/key}
