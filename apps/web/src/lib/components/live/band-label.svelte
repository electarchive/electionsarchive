<script lang="ts">
  import CircleCheckIcon from "@lucide/svelte/icons/circle-check";
  import CircleDashedCheckIcon from "@lucide/svelte/icons/circle-dashed-check";
  import type { Band } from "#lib/live/format.js";
  import { stamp } from "#lib/live/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";

  const {
    band,
    isMeasure,
    at,
    class: className,
  }: {
    band: Band;
    isMeasure: boolean;
    at: string | null;
    class?: string;
  } = $props();

  const badge = $derived.by(() => {
    if (band.isProjected) {
      return isMeasure
        ? m.night_band_projected_answer()
        : m.night_band_projected();
    }
    if (isMeasure) {
      return m.night_band_certified_answer();
    }
    return band.ids.length > 1
      ? m.night_band_certified_seats()
      : m.night_band_certified();
  });
</script>

<p class={cn("flex flex-wrap items-center gap-x-3 gap-y-1.5", className)}>
  <span
    class={cn(
      "inline-flex items-center gap-1.5 rounded-full py-0.5 ps-1.5 pe-2.5 font-semibold",
      {
        "border border-dashed border-current": band.isProjected,
        "bg-foreground text-background": !band.isProjected,
      },
    )}
  >
    {#if band.isProjected}
      <CircleDashedCheckIcon class="block-4 inline-4" aria-hidden="true" />
    {:else}
      <CircleCheckIcon class="block-4 inline-4" aria-hidden="true" />
    {/if}
    {badge}
  </span>
  {#if band.isProjected}
    <span class="text-muted-foreground"
      >{m.night_band_called({ time: stamp(at) })}</span
    >
  {/if}
</p>
