<script lang="ts">
  import { count, percent } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";
  import { cn } from "#lib/utils.js";
  import type { PageServerData } from "./$types";

  const {
    year,
    winner,
    phrase,
    when,
    annulled,
    isScoped = false,
  }: {
    year: string;
    winner: Awaited<
      Extract<PageServerData, { kind: "contest" }>["body"]
    >["contest"]["series"][number]["winner"];
    phrase: string;
    when: string;
    annulled: boolean;
    isScoped?: boolean;
  } = $props();
</script>

<span
  class={cn(
    "shrink-0 self-start font-figures font-semibold tabular-nums inline-10",
    { "line-through": annulled },
  )}>{year}</span
>
<span class="flex flex-1 flex-wrap items-baseline gap-x-1.5 min-inline-0">
  {#if winner == null}
    <span class="text-pretty">{phrase}</span>
  {:else}
    <span class="flex items-baseline gap-1.5">
      {#if !isScoped}
        <span
          class="shrink-0 self-center rounded-xs block-2.5 inline-2.5"
          style:background-color={swatch(winner.color, winner.key)}
        ></span>
      {/if}
      <span class="text-pretty">{winner.label}</span>
    </span>
    {#if winner.total > 0}
      <span class="font-figures text-xs text-muted-foreground tabular-nums"
        >{winner.measure === "electoral_votes"
          ? m.series_electoral({
              votes: count(winner.votes),
              total: count(winner.total),
            })
          : percent(winner.share)}</span
      >
    {/if}
  {/if}
  {#if annulled}
    <span class="highlighter px-1 text-xs font-semibold"
      >{m.election_annulled()}</span
    >
  {/if}
</span>
<span
  class="shrink-0 self-start font-figures text-xs/5 text-muted-foreground tabular-nums"
  >{when}</span
>
