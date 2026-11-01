<script lang="ts">
  import CheckIcon from "@lucide/svelte/icons/check";
  import type { Summary } from "#lib/live/summary.js";
  import Flip from "#lib/components/live/dashboard/flip.svelte";
  import { count, percent } from "#lib/format.js";
  import { tone } from "#lib/live/format.js";
  import { lineup, useReach } from "#lib/live/lineup.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";
  import Also from "./also.svelte";
  import Seat from "./seat.svelte";

  const {
    summary,
    heading,
    held,
    isFlip,
    limit = 3,
  }: {
    summary: Summary;
    heading?: string;
    held: { short: string; color: string } | null;
    isFlip: boolean;
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
  const pick = $derived(summary.winner ?? summary.leader);
</script>

<div class="grid gap-2.5">
  {#if heading != null}
    <span class="grid grid-cols-[minmax(0,1fr)_auto] items-start gap-3">
      <span class="text-sm/snug font-semibold text-pretty">{heading}</span>
      <span class="flex items-center gap-1.5">
        {#if isFlip}
          <Flip
            party={pick?.party?.short ?? ""}
            color={tone(pick ?? undefined)}
          />
        {/if}
        <Seat {held} />
      </span>
    </span>
  {/if}
  {#if split.main.length > 0}
    <ol class="grid gap-1.5">
      {#each split.main as line (line.option.id)}
        {const share = $derived(line.share)}
        {const isWinner = $derived(winners.has(line.option.id))}
        <li class="grid gap-0.5">
          <span
            class="grid grid-cols-[minmax(0,1fr)_auto] items-baseline gap-x-3 text-[0.8125rem]"
          >
            <span class="flex items-baseline gap-1.5 min-inline-0">
              <span
                class="shrink-0 self-center rounded-full block-2 inline-2"
                style:background-color={tone(line.option)}
              ></span>
              <span
                class={cn("truncate", {
                  "font-bold": isWinner,
                  "font-medium": !isWinner,
                })}>{line.option.label}</span
              >
              {#if line.option.party != null}
                <span class="shrink-0 text-xs text-muted-foreground"
                  >{line.option.party.short}</span
                >
              {/if}
              {#if isWinner}
                <CheckIcon
                  class="shrink-0 self-center block-3.5 inline-3.5"
                  aria-label={m.night_state_won()}
                />
              {/if}
            </span>
            <span class="font-figures tabular-nums">
              {#if share != null}
                <span class="font-semibold">{percent(share)}</span>
                <span class="ms-1 text-xs text-muted-foreground"
                  >{count(line.votes)}</span
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
              class="relative overflow-hidden rounded-xs bg-muted block-1"
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
  <span
    class="flex items-center justify-between gap-3 text-xs text-muted-foreground"
  >
    <span class="font-figures tabular-nums"
      >{summary.counted == null
        ? isUnopposed
          ? m.night_state_unopposed()
          : m.night_nation_waiting()
        : m.night_tip_counted({ share: percent(summary.counted, 0) })}</span
    >
    {#if heading == null}
      <span class="flex items-center gap-1.5">
        {#if isFlip}
          <Flip
            party={pick?.party?.short ?? ""}
            color={tone(pick ?? undefined)}
          />
        {/if}
        <Seat {held} />
      </span>
    {/if}
  </span>
</div>
