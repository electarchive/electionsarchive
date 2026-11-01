<script lang="ts">
  import type { Line } from "#lib/live/lineup.js";
  import { percent } from "#lib/format.js";
  import { others } from "#lib/live/lineup.js";
  import { m } from "#lib/paraglide/messages.js";

  const { rest, total }: { rest: Array<Line>; total: number } = $props();

  const votes = $derived.by(() => {
    let sum = 0;
    for (const line of rest) {
      sum += line.votes;
    }
    return sum;
  });
</script>

{#if rest.length > 0}
  <p
    class="flex items-baseline justify-between gap-3 text-xs text-muted-foreground"
  >
    <span class="truncate">{m.night_state_also({ names: others(rest) })}</span>
    {#if total > 0}
      <span class="shrink-0 font-figures tabular-nums"
        >{percent(votes / total)}</span
      >
    {/if}
  </p>
{/if}
