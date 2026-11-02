<script lang="ts">
  import { m } from "#lib/paraglide/messages.js";
</script>

<section
  class="relative col-span-12 grid grid-cols-[minmax(0,1fr)] gap-3 bg-card px-3.5 pbs-3.5 pbe-3"
  aria-busy="true"
  aria-label={m.indices_series_title()}
>
  <div class="grid gap-0.5">
    <p class="heading">{m.indices_series_title()}</p>
    <p class="text-[0.8125rem] text-muted-foreground max-inline-[64ch]">
      {m.indices_series_lede()}
    </p>
  </div>
  <div
    class="rounded-xs bg-muted block-4 inline-48 motion-safe:animate-pulse"
  ></div>
  <div class="rounded-sm bg-muted block-55 motion-safe:animate-pulse"></div>
</section>
