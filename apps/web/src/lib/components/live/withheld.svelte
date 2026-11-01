<script lang="ts">
  import type { Snippet } from "svelte";
  import { m } from "#lib/paraglide/messages.js";

  const { isOff, children }: { isOff: boolean; children: Snippet } = $props();
</script>

{#if isOff}
  <div class="grid gap-2">
    <p class="text-xs text-muted-foreground">{m.board_forecast_off()}</p>
    <div inert class="opacity-40 grayscale select-none">
      {@render children()}
    </div>
  </div>
{:else}
  {@render children()}
{/if}
