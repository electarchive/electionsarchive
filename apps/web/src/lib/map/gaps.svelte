<script lang="ts">
  import type { Engine } from "#lib/map/engine.svelte.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";

  let {
    engine,
    overlay,
  }: { engine: Engine; overlay?: "top" | "under-controls" | "bottom" } =
    $props();
</script>

{#if engine.hasGaps}
  <p
    class={cn(
      "text-xs text-pretty text-muted-foreground",
      {
        "absolute inset-s-2 z-10 rounded-sm bg-background/90 px-2 py-1 max-inline-[min(22rem,calc(100%-4rem))]":
          overlay != null,
      },
      {
        "inset-bs-2": overlay === "top",
        "inset-bs-14": overlay === "under-controls",
        "inset-be-2": overlay === "bottom",
      },
    )}
    role="status"
    data-export-skip
  >
    {m.map_gaps()}
    <button
      class="underline underline-offset-2 hover:text-foreground"
      type="button"
      onclick={() => engine.retry()}
    >
      {m.map_gaps_retry()}
    </button>
  </p>
{/if}
