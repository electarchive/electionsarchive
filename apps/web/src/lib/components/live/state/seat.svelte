<script lang="ts">
  import { m } from "#lib/paraglide/messages.js";

  const {
    held,
    isPlain = false,
  }: { held: { short: string; color: string } | null; isPlain?: boolean } =
    $props();
</script>

{#if held == null}
  <span
    class="inline-flex shrink-0 items-center rounded-full border border-dashed border-border px-2 py-0.5 text-[0.6875rem]/none font-medium whitespace-nowrap text-muted-foreground"
    >{m.night_state_open_seat()}</span
  >
{:else if isPlain}
  <span
    class="inline-flex shrink-0 items-center gap-1.5 rounded-full border border-border px-2 py-0.5 text-[0.6875rem]/none font-medium whitespace-nowrap"
    ><span
      class="rounded-full ring-1 ring-foreground block-1.5 inline-1.5"
      style:background-color={held.color}
    ></span>{m.night_state_held_seat({ party: held.short })}</span
  >
{:else}
  <span
    class="inline-flex shrink-0 items-center gap-1.5 rounded-full border px-2 py-0.5 text-[0.6875rem]/none font-medium whitespace-nowrap"
    style:border-color="color-mix(in oklab, {held.color} 50%, transparent)"
    style:background-color="color-mix(in oklab, {held.color} 10%, transparent)"
    style:color="color-mix(in oklab, {held.color} 45%, var(--foreground))"
    ><span
      class="rounded-full block-1.5 inline-1.5"
      style:background-color={held.color}
    ></span>{m.night_state_held_seat({ party: held.short })}</span
  >
{/if}
