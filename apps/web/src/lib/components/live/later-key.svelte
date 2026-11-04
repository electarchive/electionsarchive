<script lang="ts">
  import { DOTS } from "#lib/live/format.js";
  import { m } from "#lib/paraglide/messages.js";
</script>

<span class="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
  <span
    class="shrink-0 rounded-xs ring-1 ring-border block-3 inline-3 ring-inset"
    style:background-image={DOTS}
    aria-hidden="true"
  ></span>
  {m.night_later_key()}
</span>
