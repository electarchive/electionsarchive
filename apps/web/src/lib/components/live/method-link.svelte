<script lang="ts">
  import { resolve } from "$app/paths";
  import InfoIcon from "@lucide/svelte/icons/info";
  import { m } from "#lib/paraglide/messages.js";

  const {
    section,
  }: {
    section:
      | "how"
      | "turnout"
      | "margin"
      | "count-order"
      | "ranges"
      | "testing"
      | "limits";
  } = $props();
</script>

<a
  href="{resolve('/live/methodology')}#{section}"
  class="inline-grid shrink-0 place-content-center rounded-full text-muted-foreground block-6 inline-6 hover:bg-muted hover:text-foreground focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-foreground"
  aria-label={m.night_method_link()}
  title={m.night_method_link()}
>
  <InfoIcon class="block-3.5 inline-3.5" aria-hidden="true" />
</a>
