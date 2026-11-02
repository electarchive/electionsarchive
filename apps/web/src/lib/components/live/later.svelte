<script lang="ts">
  import { HATCH, stamp } from "#lib/live/format.js";
  import { m } from "#lib/paraglide/messages.js";

  const {
    text,
    closes,
    time,
  }: { text: string; closes: number | null; time: number } = $props();
</script>

<div
  class="grid gap-4 border-bs-2 border-foreground pbs-5 max-inline-[78ch] sm:grid-cols-[7rem_minmax(0,1fr)] sm:gap-6"
>
  <div
    class="rounded-xs ring-1 ring-border block-3 ring-inset sm:block-auto"
    style:background-image={HATCH}
    aria-hidden="true"
  ></div>
  <div class="grid content-start gap-2">
    <h2 class="heading text-lg!">{m.night_later_title()}</h2>
    <p class="text-[0.9375rem]/relaxed text-pretty">
      {text}
    </p>
    {#if closes != null && time < closes}
      <p class="font-figures text-sm text-muted-foreground tabular-nums">
        {m.night_later_before({ time: stamp(closes) })}
      </p>
    {/if}
  </div>
</div>
