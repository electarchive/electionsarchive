<script lang="ts">
  import CheckIcon from "@lucide/svelte/icons/check";
  import type { Entry } from "#lib/live/ballot.js";
  import Flip from "#lib/components/live/dashboard/flip.svelte";
  import Name from "#lib/components/name.svelte";
  import { count, percent } from "#lib/format.js";
  import { tone } from "#lib/live/format.js";
  import { lineup, useReach } from "#lib/live/lineup.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";
  import Also from "./also.svelte";
  import Seat from "./seat.svelte";

  const {
    entry,
    href,
    title,
    isMapped = false,
  }: {
    entry: Entry;
    href: string;
    title: string;
    isMapped?: boolean;
  } = $props();

  const summary = $derived(entry.summary);
  const race = $derived(summary.race);
  const winners = $derived(
    new Set(
      race.certified?.winners ??
        (summary.winner == null ? [] : [summary.winner.id]),
    ),
  );
  const reach = useReach();
  const split = $derived(lineup(race, summary.totals, reach(), 4));
</script>

<a
  {href}
  class={cn(
    "group grid content-start gap-3 bg-background p-4  outline-none hover:highlighter focus-visible:highlighter sm:px-5",
    { "shadow-[inset_3px_0_0_var(--foreground)]": isMapped },
  )}
>
  <span class="grid grid-cols-[minmax(0,1fr)_auto] items-start gap-3">
    <span class="heading text-lg! text-pretty">{title}</span>
    <span class="flex items-center gap-1.5 pbs-1">
      {#if entry.isFlip}
        {const pick = $derived(summary.winner ?? summary.leader)}
        <Flip
          party={pick?.party?.short ?? ""}
          color={tone(pick ?? undefined)}
        />
      {/if}
      <Seat held={entry.held} />
    </span>
  </span>
  <ol class="grid gap-2">
    {#each split.main as line (line.option.id)}
      {const share = $derived(line.share)}
      {const isWinner = $derived(winners.has(line.option.id))}
      <li class="grid gap-1">
        <span
          class="grid grid-cols-[minmax(0,1fr)_auto] items-baseline gap-x-3 text-sm"
        >
          <span class="flex items-baseline gap-1.5 min-inline-0">
            <span
              class="shrink-0 translate-y-[-0.05em] self-center rounded-full block-2.5 inline-2.5"
              style:background-color={tone(line.option)}
            ></span>
            <Name
              label={line.option.label}
              person={line.option.person}
              hasSwatch={false}
              hasLink={false}
              class={cn("truncate", {
                "font-bold": isWinner,
                "font-semibold": !isWinner,
              })}
            />
            {#if line.option.party != null}
              <Name
                label={line.option.party.short}
                party={line.option.party.id}
                color={line.option.party.color}
                hasSwatch={false}
                hasLink={false}
                class="shrink-0 text-xs text-muted-foreground"
              />
            {/if}
            {#if line.option.isIncumbent}
              <span
                class="shrink-0 rounded-xs border border-border px-1 text-[0.6875rem] text-muted-foreground"
                >{m.night_explorer_incumbent()}</span
              >
            {/if}
            {#if isWinner}
              <CheckIcon
                class="shrink-0 self-center block-4 inline-4"
                aria-label={m.night_state_won()}
              />
            {/if}
          </span>
          <span class="font-figures tabular-nums">
            {#if share != null}
              <span class="font-bold">{percent(share)}</span>
              <span class="ms-1.5 text-xs text-muted-foreground"
                >{count(line.votes)}</span
              >
            {/if}
          </span>
        </span>
        {#if share != null}
          <span
            class="relative overflow-hidden rounded-xs bg-muted block-1.5"
            aria-hidden="true"
          >
            <span
              class="absolute inset-y-0 inset-s-0 transition-[inline-size] duration-700 motion-reduce:transition-none"
              style:inline-size="{(share ?? 0) * 100}%"
              style:background-color={tone(line.option)}
            ></span>
          </span>
        {/if}
      </li>
    {/each}
  </ol>
  <Also rest={split.rest} total={summary.total} />
  <span
    class="flex items-center justify-between gap-3 text-xs text-muted-foreground"
  >
    <span class="font-figures tabular-nums"
      >{summary.counted == null
        ? entry.isUnopposed
          ? m.night_state_unopposed()
          : m.night_nation_waiting()
        : m.night_tip_counted({ share: percent(summary.counted, 0) })}</span
    >
    <span class="font-semibold text-foreground group-hover:underline"
      >{m.night_state_results()}</span
    >
  </span>
</a>
