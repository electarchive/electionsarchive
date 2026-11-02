<script lang="ts">
  import type { Seat } from "#lib/live/nation.js";
  import { stamp } from "#lib/live/format.js";
  import { latestCall } from "#lib/live/nation.js";
  import { m } from "#lib/paraglide/messages.js";

  const { seats }: { seats: Array<Seat> } = $props();

  const latest = $derived(latestCall(seats));
</script>

{#if latest != null}
  <p class="flex items-baseline gap-2 text-xs text-muted-foreground">
    <span
      class="grid shrink-0 translate-y-0.5 place-items-center rounded-[1px] bg-foreground block-3 inline-3"
      aria-hidden="true"
      ><span class="rounded-full bg-background block-1 inline-1"></span></span
    >
    <span>{m.night_projected_key({ time: stamp(latest) })}</span>
  </p>
{/if}
