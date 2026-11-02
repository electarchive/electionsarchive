<script lang="ts">
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";

  const {
    shape,
    label = m.preview_loading(),
  }: { shape: "cards" | "table" | "columns" | "tiles"; label?: string } =
    $props();

  const widths = ["72%", "58%", "84%", "64%", "48%", "76%", "60%", "68%"];
</script>

{#snippet bar(width: string, isTall = false)}
  <span
    class={cn("block rounded-xs bg-muted motion-safe:animate-pulse", {
      "block-4": isTall,
      "block-3": !isTall,
    })}
    style:inline-size={width}
  ></span>
{/snippet}

<section
  class="col-span-12 grid grid-cols-[minmax(0,1fr)] content-start gap-4 bg-card px-3.5 pbs-3.5 pbe-4"
  role="status"
  aria-busy="true"
  aria-label={label}
>
  <div class="grid gap-1.5">
    {@render bar("11rem", true)}
    {@render bar("min(100%, 26rem)")}
  </div>
  {#if shape === "cards"}
    <div class="grid gap-3 sm:grid-cols-3">
      {#each widths.slice(0, 3) as width, index (index)}
        <div
          class="grid content-start gap-2.5 rounded-sm border border-border p-3"
        >
          {@render bar(width, true)}
          {@render bar("40%")}
          <span
            class="block rounded-xs bg-muted block-2 inline-full motion-safe:animate-pulse"
          ></span>
        </div>
      {/each}
    </div>
  {:else if shape === "table"}
    <div class="grid">
      {#each widths.slice(0, 6) as width, index (index)}
        <div
          class="flex items-center justify-between gap-6 border-be border-border/60 py-2 last:border-be-0"
        >
          {@render bar(`min(${width}, 14rem)`)}
          {@render bar("3.5rem")}
        </div>
      {/each}
    </div>
  {:else if shape === "tiles"}
    <div class="grid gap-x-6 gap-y-4 sm:grid-cols-2 lg:grid-cols-4">
      {#each widths.slice(0, 4) as width, index (index)}
        <div class="grid content-start gap-2">
          {@render bar(width)}
          <span
            class="block rounded-xs bg-muted block-2 inline-full motion-safe:animate-pulse"
          ></span>
          {@render bar("35%")}
        </div>
      {/each}
    </div>
  {:else}
    <div class="columns-sm gap-x-8">
      {#each widths as width, index (index)}
        <div class="break-inside-avoid py-1.5">
          {@render bar(width)}
        </div>
      {/each}
    </div>
  {/if}
</section>
