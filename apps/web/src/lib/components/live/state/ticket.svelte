<script lang="ts">
  import CheckIcon from "@lucide/svelte/icons/check";
  import type { Entry } from "#lib/live/ballot.js";
  import Flip from "#lib/components/live/dashboard/flip.svelte";
  import { count, percent } from "#lib/format.js";
  import { tone } from "#lib/live/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";

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
  const lines = $derived(
    race.options
      .map((option, index) => ({ option, votes: summary.totals[index] ?? 0 }))
      .filter((line) => !line.option.isWriteIn || line.votes > 0)
      .toSorted(
        (a, b) =>
          b.votes - a.votes ||
          Number(b.option.isIncumbent) - Number(a.option.isIncumbent),
      )
      .slice(0, 4),
  );
</script>

<a
  {href}
  class={cn(
    "group grid content-start gap-3 bg-background p-4  outline-none hover:highlighter focus-visible:highlighter sm:px-5",
    { "shadow-[inset_3px_0_0_var(--foreground)]": isMapped },
  )}
>
  <span class="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
    <span class="heading text-lg! text-pretty">{title}</span>
    <span class="flex items-center gap-2 text-xs text-muted-foreground">
      {#if entry.isFlip}
        {const pick = $derived(summary.winner ?? summary.leader)}
        <Flip
          party={pick?.party?.short ?? ""}
          color={tone(pick ?? undefined)}
        />
      {/if}
      {#if entry.held != null}
        <span class="flex items-center gap-1">
          <span
            class="rounded-full block-2 inline-2"
            style:background-color={entry.held.color}
          ></span>
          {m.night_state_held({ party: entry.held.short })}
        </span>
      {:else}
        <span>{m.night_state_open_seat()}</span>
      {/if}
    </span>
  </span>
  <ol class="grid gap-2">
    {#each lines as line (line.option.id)}
      {const share = $derived(
        summary.total > 0 ? line.votes / summary.total : null,
      )}
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
            <span
              class={cn("truncate", {
                "font-bold": isWinner,
                "font-semibold": !isWinner,
              })}>{line.option.label}</span
            >
            {#if line.option.party != null}
              <span class="shrink-0 text-xs text-muted-foreground"
                >{line.option.party.short}</span
              >
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
      </li>
    {/each}
  </ol>
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
