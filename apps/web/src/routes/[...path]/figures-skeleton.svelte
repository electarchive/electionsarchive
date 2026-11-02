<script lang="ts">
  import { m } from "#lib/paraglide/messages.js";
</script>

<div
  class="mbs-3 flex flex-wrap gap-x-6 gap-y-1.5 border-bs border-border px-3.5 pbs-2.5 pbe-2.5"
  role="status"
  aria-busy="true"
  aria-label={m.indices_title()}
>
  <span class="flex items-center gap-2 block-5">
    <span
      class="rounded-xs bg-muted block-3 inline-24 motion-safe:animate-pulse"
    ></span>
    <span
      class="rounded-xs bg-muted block-3 inline-32 motion-safe:animate-pulse"
    ></span>
  </span>
  <span class="flex items-center gap-2 block-5">
    <span
      class="rounded-xs bg-muted block-3 inline-28 motion-safe:animate-pulse"
    ></span>
    <span class="rounded-xs bg-muted block-3 inline-8 motion-safe:animate-pulse"
    ></span>
  </span>
  <span class="flex items-center gap-2 block-5">
    <span
      class="rounded-xs bg-muted block-3 inline-32 motion-safe:animate-pulse"
    ></span>
    <span class="rounded-xs bg-muted block-3 inline-8 motion-safe:animate-pulse"
    ></span>
  </span>
</div>
