<script lang="ts">
  import type { LiveRace } from "@ea/analytics/live";
  import CheckIcon from "@lucide/svelte/icons/check";
  import type { Desk } from "#lib/live/board/desk.svelte.js";
  import { count, percent } from "#lib/format.js";
  import { compact, tone } from "#lib/live/format.js";
  import { ranked } from "#lib/live/standing.js";
  import { closing, describe } from "#lib/live/summary.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";

  const { desk, race }: { desk: Desk; race: LiveRace } = $props();

  const summary = $derived(desk.feed.summaries.get(race.id));
  const rows = $derived.by(() => {
    if (summary == null) {
      return [];
    }
    const { totals, total } = summary;
    return ranked(totals)
      .filter(
        (index) =>
          race.options[index]?.isWriteIn !== true || (totals[index] ?? 0) > 0,
      )
      .map((index) => {
        const option = race.options[index];
        const votes = totals[index] ?? 0;
        return {
          option,
          votes,
          share: total > 0 ? votes / total : 0,
          isWinner: option != null && summary.winner?.id === option.id,
        };
      });
  });
  const headline = $derived(
    summary == null
      ? ""
      : describe(summary, closing(desk.feed.election, race), desk.feed.clock),
  );
</script>

{#if summary != null}
  <div class="@container grid content-start gap-2">
    <div class="grid gap-1">
      <p class="truncate text-[0.75em] text-muted-foreground">{headline}</p>
      {#if summary.counted != null}
        <div class="flex items-center gap-2">
          <span
            class="relative flex-1 overflow-hidden rounded-full bg-muted block-1"
          >
            <span
              class="absolute inset-y-0 inset-s-0 bg-foreground transition-[inline-size] duration-700 motion-reduce:transition-none"
              style:inline-size="{summary.counted * 100}%"
            ></span>
          </span>
          <span
            class="font-figures text-[0.75em] text-muted-foreground tabular-nums"
            >{m.board_in({ share: percent(summary.counted, 0) })}</span
          >
        </div>
      {/if}
    </div>
    <ol class="grid gap-1.5">
      {#each rows as row (row.option?.id)}
        <li class="grid gap-0.5">
          <span
            class="grid grid-cols-[minmax(0,1fr)_auto_auto] items-baseline gap-x-2"
          >
            <span class="flex items-center gap-1.5 min-inline-0">
              <span
                class="shrink-0 rounded-full block-2 inline-2"
                style:background-color={tone(row.option)}
              ></span>
              <span
                class={cn("truncate", {
                  "font-semibold": row.isWinner || rows[0] === row,
                })}>{row.option?.label}</span
              >
              {#if row.option?.party != null}
                <span
                  class="hidden shrink-0 text-[0.75em] text-muted-foreground @[16rem]:inline"
                  >{row.option.party.short}</span
                >
              {/if}
              {#if row.isWinner}
                <CheckIcon
                  class="shrink-0 block-3.5 inline-3.5"
                  aria-label={m.board_power_won()}
                />
              {/if}
            </span>
            <span
              class="font-figures text-[0.8125em] text-muted-foreground tabular-nums"
              title={count(row.votes)}>{compact(row.votes)}</span
            >
            <span
              class="text-end font-figures font-semibold tabular-nums min-inline-[3.2em]"
              >{summary.total > 0 ? percent(row.share, 1) : "–"}</span
            >
          </span>
          <span class="relative overflow-hidden bg-muted block-1">
            <span
              class="absolute inset-y-0 inset-s-0 transition-[inline-size] duration-700 motion-reduce:transition-none"
              style:inline-size="{row.share * 100}%"
              style:background-color={tone(row.option)}
            ></span>
          </span>
        </li>
      {/each}
    </ol>
  </div>
{/if}
