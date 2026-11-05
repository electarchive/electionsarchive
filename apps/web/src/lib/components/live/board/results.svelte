<script lang="ts">
  import type { LiveOption, LiveRace } from "@ea/analytics/live";
  import type { Desk } from "#lib/live/board/desk.svelte.js";
  import BandLabel from "#lib/components/live/band-label.svelte";
  import { count, percent } from "#lib/format.js";
  import { compact, paint } from "#lib/live/format.js";
  import { prior, ranked } from "#lib/live/standing.js";
  import { bandOf, closing, describe, isUncounted } from "#lib/live/summary.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";

  const { desk, race }: { desk: Desk; race: LiveRace } = $props();

  const summary = $derived(desk.feed.summaries.get(race.id));
  const band = $derived(summary == null ? null : bandOf(summary));
  const seats = $derived(race.seats ?? 1);
  const isSilent = $derived(summary != null && isUncounted(summary));
  const last = $derived(isSilent ? null : prior(race));
  const rows = $derived.by(() => {
    if (summary == null) {
      return [];
    }
    const { totals, total } = summary;
    return ranked(totals).flatMap((index) => {
      const option = race.options[index];
      const votes = totals[index] ?? 0;
      return option == null || (option.isWriteIn && votes === 0)
        ? []
        : [
            {
              option,
              votes,
              share: total > 0 ? votes / total : 0,
              before: last?.shares[index] ?? null,
            },
          ];
    });
  });
  const headline = $derived(
    summary == null
      ? ""
      : describe(summary, closing(desk.feed.election, race), desk.feed.clock),
  );

  function isDecided(option: LiveOption) {
    return band?.ids.includes(option.id) === true;
  }
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
    <div
      class={cn(
        "grid gap-2 transition-colors duration-700 motion-reduce:transition-none",
        { "-mx-2 rounded-xs band p-2": band != null },
      )}
      style:--party={band?.fill}
    >
      {#if band != null}
        <BandLabel
          {band}
          isMeasure={race.isMeasure ?? false}
          at={summary.projectedAt}
          class="text-[0.75em]"
        />
      {/if}
      <ol class="grid gap-1.5">
        {#each rows as row, rank (row.option.id)}
          {const color = $derived(paint(row.option, band))}
          {const isDim = $derived(band != null && !isDecided(row.option))}
          {#if seats > 1 && rank === seats && rows.length > seats}
            <li
              class="flex items-center gap-2 text-[0.6875em] text-muted-foreground"
              aria-hidden="true"
            >
              <span class="flex-1 border-bs border-dashed border-current"
              ></span>
              {m.night_seats_line({ count: count(seats) })}
              <span class="flex-1 border-bs border-dashed border-current"
              ></span>
            </li>
          {/if}
          <li class="grid gap-0.5">
            <span
              class="grid grid-cols-[minmax(0,1fr)_auto_auto] items-baseline gap-x-2"
            >
              <span class="flex items-center gap-1.5 min-inline-0">
                <span
                  class="shrink-0 rounded-full block-2 inline-2"
                  style:background-color={color}
                ></span>
                <span
                  class={cn("truncate", {
                    "font-semibold":
                      band == null ? rank === 0 : isDecided(row.option),
                    "text-muted-foreground": isDim,
                  })}>{row.option.label}</span
                >
                {#if row.option.party != null}
                  <span
                    class="hidden shrink-0 text-[0.75em] text-muted-foreground @3xs:inline"
                    >{row.option.party.short}</span
                  >
                {/if}
                {#if row.option.isIncumbent}
                  <span
                    class="hidden shrink-0 text-[0.6875em] text-muted-foreground @[22rem]:inline"
                    >{m.results_incumbent()}</span
                  >
                {/if}
              </span>
              {#if !isSilent}
                <span
                  class="font-figures text-[0.8125em] text-muted-foreground tabular-nums"
                  title={count(row.votes)}>{compact(row.votes)}</span
                >
                <span
                  class={cn(
                    "text-end font-figures font-semibold tabular-nums min-inline-[3.2em]",
                    { "text-muted-foreground": isDim },
                  )}>{summary.total > 0 ? percent(row.share, 1) : "–"}</span
                >
              {/if}
            </span>
            <span class={cn("relative bg-muted block-1", { hidden: isSilent })}>
              <span
                class="absolute inset-y-0 inset-s-0 transition-[inline-size] duration-700 motion-reduce:transition-none"
                style:inline-size="{row.share * 100}%"
                style:background-color={color}
              ></span>
              {#if row.before != null && row.before > 0}
                <span
                  class="absolute -inset-y-0.5 rounded-full bg-foreground/70 inline-0.5"
                  style:inset-inline-start="calc({row.before * 100}% - 1px)"
                  title={m.night_tip_baseline({ margin: percent(row.before) })}
                ></span>
              {/if}
            </span>
          </li>
        {/each}
      </ol>
    </div>
  </div>
{/if}
