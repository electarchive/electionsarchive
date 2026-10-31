<script lang="ts">
  import type { Snippet } from "svelte";
  import { cn } from "#lib/utils.js";

  const {
    results,
    map = null,
    aside = null,
  }: {
    results: Snippet | null;
    map?: Snippet | null;
    aside?: Snippet | null;
  } = $props();

  const isSplit = $derived(results != null && map != null);
</script>

<div
  class={cn(
    "@container col-span-12 grid grid-cols-12 gap-px bg-border min-inline-0",
    { "@4xl:grid-rows-[auto_1fr]": isSplit && aside != null },
  )}
>
  {#if results != null}
    <div
      class={cn(
        "col-span-12 flex flex-col gap-px bg-border min-inline-0 *:last:grow",
        {
          "@4xl:col-span-5 @6xl:col-span-4": isSplit,
          "@4xl:row-span-2": isSplit && aside == null,
          "@4xl:col-span-8": !isSplit && aside != null,
        },
      )}
    >
      {@render results()}
    </div>
  {/if}
  {#if map != null}
    <div
      class={cn("col-span-12 bg-card px-3.5 pbs-3 pbe-3.5 min-inline-0", {
        "@4xl:col-span-7 @4xl:row-span-2 @6xl:col-span-8": results != null,
        "@4xl:col-span-8": results == null && aside != null,
      })}
    >
      <div class="grid gap-2 @4xl:sticky @4xl:inset-bs-3">
        {@render map()}
      </div>
    </div>
  {/if}
  {#if aside != null}
    <div
      class={cn("col-span-12 grid bg-card min-inline-0", {
        "@4xl:col-span-5 @6xl:col-span-4": isSplit,
        "@4xl:col-span-4": !isSplit && (results != null || map != null),
      })}
    >
      {@render aside()}
    </div>
  {/if}
</div>
