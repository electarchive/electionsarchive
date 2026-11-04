<script lang="ts">
  import CircleCheckIcon from "@lucide/svelte/icons/circle-check";
  import CircleDashedCheckIcon from "@lucide/svelte/icons/circle-dashed-check";
  import type { Summary } from "#lib/live/summary.js";
  import { m } from "#lib/paraglide/messages.js";

  const { summary }: { summary: Summary } = $props();
</script>

{#if summary.winner != null}
  <CircleCheckIcon
    class="shrink-0 block-[1.1em] inline-[1.1em]"
    aria-label={m.night_rating_won()}
  />
{:else if summary.projected != null}
  <CircleDashedCheckIcon
    class="shrink-0 block-[1.1em] inline-[1.1em]"
    aria-label={m.night_rating_projected()}
  />
{/if}
