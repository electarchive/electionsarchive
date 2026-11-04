<script lang="ts">
  import CheckIcon from "@lucide/svelte/icons/check";
  import type { Summary } from "#lib/live/summary.js";
  import Flip from "#lib/components/live/dashboard/flip.svelte";
  import Name from "#lib/components/name.svelte";
  import { percent } from "#lib/format.js";
  import {
    compact,
    initial,
    lead,
    stamp,
    surname,
    tone,
  } from "#lib/live/format.js";
  import { lineup, useReach } from "#lib/live/lineup.js";
  import { odds } from "#lib/live/nation.js";
  import { sum } from "#lib/live/standing.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";
  import Also from "./also.svelte";

  const {
    summary,
    heading,
    held,
    isFlip,
    closes = null,
    limit = 3,
  }: {
    summary: Summary;
    heading?: string;
    held: { short: string; color: string } | null;
    isFlip: boolean;
    closes?: number | null;
    limit?: number;
  } = $props();

  const race = $derived(summary.race);
  const winners = $derived(
    new Set(
      race.certified?.winners ??
        (summary.winner == null ? [] : [summary.winner.id]),
    ),
  );
  const reach = useReach();
  const split = $derived(
    lineup(
      race,
      summary.totals,
      reach(),
      Math.max(limit, (race.seats ?? 1) + 1),
    ),
  );
  const isUnopposed = $derived(
    !race.isMeasure &&
      race.options.filter((option) => !option.isWriteIn).length <=
        (race.seats ?? 1),
  );
  const pick = $derived(summary.winner ?? summary.projected ?? summary.leader);
  const status = $derived.by(() => {
    if (summary.winner != null && (race.seats ?? 1) === 1) {
      return m.night_head_won({
        name: surname(summary.winner),
        margin: lead(summary.margin),
      });
    }
    if (summary.projected != null) {
      return m.night_card_projected({ time: stamp(summary.projectedAt) });
    }
    if (summary.total > 0) {
      return summary.counted == null
        ? null
        : m.night_tip_counted({ share: percent(summary.counted, 0) });
    }
    if (isUnopposed) {
      return m.night_state_unopposed();
    }
    if (summary.later != null) {
      return m.night_later();
    }
    if (closes == null) {
      return null;
    }
    return Date.now() < closes
      ? m.night_head_before({ time: stamp(closes) })
      : m.night_head_waiting();
  });
  const context = $derived.by(() => {
    if (summary.projected != null) {
      return summary.counted == null
        ? null
        : m.night_tip_counted({ share: percent(summary.counted, 0) });
    }
    if (summary.winner != null || isUnopposed) {
      return null;
    }
    if (summary.favorite != null && summary.chance != null) {
      return m.night_nation_chance({
        name: surname(summary.favorite),
        chance: odds(summary.chance),
      });
    }
    const prior = race.options.map((_, index) =>
      sum(race.baseline.map((row) => (row.shares[index] ?? 0) * row.turnout)),
    );
    const total = sum(prior);
    const order = prior
      .map((votes, index) => ({ votes, option: race.options[index] }))
      .toSorted((a, b) => b.votes - a.votes);
    const first = order[0];
    if (total <= 0 || first?.option?.party == null) {
      return null;
    }
    return m.night_tip_baseline({
      margin: `${initial(first.option)}+${lead((first.votes - (order[1]?.votes ?? 0)) / total)}`,
    });
  });
</script>

<div class="grid grid-cols-[minmax(0,1fr)] gap-2">
  <span class="flex items-baseline justify-between gap-3">
    <span class="text-[0.8125rem]/snug font-semibold text-pretty"
      >{heading ?? ""}</span
    >
    <span class="flex shrink-0 items-center gap-1.5 self-center">
      {#if isFlip}
        <Flip
          party={pick?.party?.short ?? ""}
          color={tone(pick ?? undefined)}
        />
      {/if}
      <span
        class="flex items-center gap-1 text-xs whitespace-nowrap text-muted-foreground"
      >
        {#if held == null}
          <span
            class="rounded-full border border-dashed border-muted-foreground block-2 inline-2"
          ></span>
          {m.night_state_open_seat()}
        {:else}
          <span
            class="rounded-full block-2 inline-2"
            style:background-color={held.color}
          ></span>
          {m.night_state_held_seat({ party: held.short })}
        {/if}
      </span>
    </span>
  </span>
  {#if split.main.length > 0}
    <ol class="grid gap-1.5">
      {#each split.main as line (line.option.id)}
        {const share = $derived(line.share)}
        {const isWinner = $derived(winners.has(line.option.id))}
        {const isProjected = $derived(summary.projected?.id === line.option.id)}
        <li class="grid gap-0.5">
          <span
            class="grid grid-cols-[minmax(0,1fr)_auto] items-baseline gap-x-3 text-[0.8125rem]/snug"
          >
            <span class="flex items-baseline gap-1.5 min-inline-0">
              <span
                class="shrink-0 self-center rounded-full block-2 inline-2"
                style:background-color={tone(line.option)}
              ></span>
              <Name
                label={line.option.label}
                person={line.option.person}
                hasSwatch={false}
                class={cn("truncate", {
                  "font-semibold": isWinner || isProjected,
                })}
              />
              {#if line.option.party != null}
                <Name
                  label={initial(line.option)}
                  party={line.option.party.id}
                  color={line.option.party.color}
                  hasSwatch={false}
                  class="shrink-0 text-xs text-muted-foreground"
                />
              {/if}
              {#if isWinner}
                <CheckIcon
                  class="shrink-0 self-center block-3.5 inline-3.5"
                  aria-label={m.night_state_won()}
                />
              {:else if isProjected}
                <span
                  class="shrink-0 self-center rounded-full bg-foreground block-1.5 inline-1.5"
                  role="img"
                  aria-label={m.night_rating_projected()}
                ></span>
              {/if}
            </span>
            <span class="font-figures whitespace-nowrap tabular-nums">
              {#if share != null}
                <span class="font-semibold">{percent(share)}</span>
                <span class="ms-1 text-xs text-muted-foreground"
                  >{compact(line.votes)}</span
                >
              {:else if line.option.isIncumbent}
                <span class="text-xs text-muted-foreground"
                  >{m.night_explorer_incumbent()}</span
                >
              {/if}
            </span>
          </span>
          {#if summary.total > 0}
            <span
              class="relative overflow-hidden rounded-full bg-muted block-0.75"
              aria-hidden="true"
            >
              <span
                class="absolute inset-y-0 inset-s-0"
                style:inline-size="{(share ?? 0) * 100}%"
                style:background-color={tone(line.option)}
              ></span>
            </span>
          {/if}
        </li>
      {/each}
    </ol>
  {/if}
  <Also rest={split.rest} total={summary.total} />
  {#if status != null || context != null}
    <span
      class="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5 text-xs text-muted-foreground"
    >
      <span class="flex items-center gap-1.5 text-pretty">
        {#if summary.projected != null}
          <span
            class="shrink-0 rounded-full bg-foreground block-1.5 inline-1.5"
            aria-hidden="true"
          ></span>
        {/if}
        <span class="font-figures tabular-nums">{status ?? ""}</span>
      </span>
      {#if context != null}
        <span class="font-figures tabular-nums">{context}</span>
      {/if}
    </span>
  {/if}
</div>
