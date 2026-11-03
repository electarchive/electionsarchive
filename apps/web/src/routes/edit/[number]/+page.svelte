<script lang="ts">
  import type { PageData } from "./$types";

  const { data }: { data: PageData } = $props();
</script>

{#key data.number}
  {#if data.workspace.template === "election"}
    {#if data.Election != null}
      <data.Election {data} workspace={data.workspace} />
    {/if}
  {:else if data.workspace.template === "results"}
    {#if data.Results != null}
      <data.Results {data} workspace={data.workspace} />
    {/if}
  {:else if data.workspace.template === "merge"}
    {#if data.Merge != null}
      <data.Merge {data} workspace={data.workspace} />
    {/if}
  {:else if data.workspace.template === "lineage"}
    {#if data.Lineage != null}
      <data.Lineage {data} workspace={data.workspace} />
    {/if}
  {:else if data.workspace.template === "translate"}
    {#if data.Translate != null}
      <data.Translate {data} workspace={data.workspace} />
    {/if}
  {:else if data.workspace.template === "boundaries"}
    {#if data.Boundaries != null}
      <data.Boundaries {data} workspace={data.workspace} />
    {/if}
  {:else if data.Table != null}
    <data.Table {data} workspace={data.workspace} />
  {/if}
{/key}
