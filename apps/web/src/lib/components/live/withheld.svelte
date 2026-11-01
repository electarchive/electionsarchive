<script lang="ts">
  import type { Snippet } from "svelte";
  import { m } from "#lib/paraglide/messages.js";
  import MethodLink from "./method-link.svelte";

  const { isOff, children }: { isOff: boolean; children: Snippet } = $props();
</script>

{#if isOff}
  <div class="grid gap-2">
    <div class="flex items-center gap-1">
      <p class="text-xs text-muted-foreground">{m.board_forecast_off()}</p>
      <MethodLink section="how" />
    </div>
    <div inert class="opacity-40 grayscale select-none">
      {@render children()}
    </div>
  </div>
{:else}
  {@render children()}
{/if}
