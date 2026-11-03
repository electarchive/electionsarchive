<script lang="ts">
  import type { Line } from "#lib/live/lineup.js";
  import Name from "#lib/components/name.svelte";
  import { percent } from "#lib/format.js";
  import { others } from "#lib/live/lineup.js";
  import { m } from "#lib/paraglide/messages.js";

  const {
    rest,
    total,
    hasLink = true,
  }: { rest: Array<Line>; total: number; hasLink?: boolean } = $props();

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
    <span class="flex items-baseline gap-1.5 min-inline-0">
      {#if rest.length === 1}
        <Name
          label={rest[0]?.option.label ?? ""}
          person={rest[0]?.option.person ?? null}
          hasSwatch={false}
          {hasLink}
          class="truncate"
        />
      {:else}
        <span class="shrink-0 text-foreground"
          >{m.night_others({ count: rest.length })}</span
        >
        <span class="truncate">{others(rest)}</span>
      {/if}
    </span>
    {#if total > 0}
      <span class="shrink-0 font-figures tabular-nums"
        >{percent(votes / total)}</span
      >
    {/if}
  </p>
{/if}
