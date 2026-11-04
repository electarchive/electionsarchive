<script lang="ts">
  import CircleCheckIcon from "@lucide/svelte/icons/circle-check";
  import CircleDashedCheckIcon from "@lucide/svelte/icons/circle-dashed-check";
  import type { Entry } from "#lib/live/ballot.js";
  import Flip from "#lib/components/live/dashboard/flip.svelte";
  import { percent } from "#lib/format.js";
  import { turn } from "#lib/live/ballot.js";
  import { lead, paint, stamp, tone } from "#lib/live/format.js";
  import { lineup, useReach } from "#lib/live/lineup.js";
  import { highlight } from "#lib/live/local.js";
  import { bandOf } from "#lib/live/summary.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";

  const {
    entries,
    heading,
    label,
    href,
    tokens = [],
  }: {
    entries: Array<Entry>;
    heading: string;
    label: (entry: Entry) => string;
    href: (entry: Entry) => string;
    tokens?: Array<string>;
  } = $props();

  const parties = useReach();

  function lines(entry: Entry) {
    const { summary } = entry;
    return lineup(
      summary.race,
      summary.totals,
      parties(),
      Math.min(4, entry.contest.seats + 1),
    ).main;
  }
</script>

{#snippet marked(text: string)}
  {#each highlight(text, tokens) as segment, index (index)}
    {#if segment.isHit}
      <mark class="bg-accent text-inherit">{segment.text}</mark>
    {:else}
      {segment.text}
    {/if}
  {/each}
{/snippet}

<div class="grid content-start text-[0.8125rem]">
  <div
    class="grid grid-cols-[4.75rem_minmax(0,1fr)_3.25rem] gap-x-3 border-be border-foreground pbe-1 text-xs font-semibold sm:grid-cols-[6.5rem_minmax(0,1fr)_3.5rem_4.5rem]"
    aria-hidden="true"
  >
    <span>{heading}</span>
    <span>{m.night_state_candidates()}</span>
    <span class="text-end">{m.night_state_margin()}</span>
    <span class="hidden text-end sm:block">{m.night_state_counted()}</span>
  </div>
  <ol class="grid">
    {#each entries as entry (entry.contest.id)}
      {const summary = $derived(entry.summary)}
      {const pick = $derived(summary.winner ?? summary.leader)}
      {const band = $derived(bandOf(entry.summary, entry.contest.seats))}
      <li class="border-be border-border">
        <a
          href={href(entry)}
          class={cn(
            "grid grid-cols-[4.75rem_minmax(0,1fr)_3.25rem] items-start gap-x-3 py-1.5 outline-none hover:highlighter focus-visible:highlighter sm:grid-cols-[6.5rem_minmax(0,1fr)_3.5rem_4.5rem]",
            { "-mx-2 my-px rounded-xs band px-2": band != null },
          )}
          style:--party={band?.fill}
        >
          <span class="grid gap-0.5 min-inline-0">
            <span class="leading-snug font-semibold text-pretty"
              >{@render marked(label(entry))}</span
            >
            <span class="flex flex-wrap items-center gap-1">
              {const flip = $derived(turn(entry))}
              {#if flip != null}
                <Flip
                  flip={flip.flip}
                  from={entry.held}
                  to={flip.to}
                  isCompact={true}
                />
              {:else if entry.held != null}
                <span
                  class="flex items-center gap-1 text-[0.6875rem] text-muted-foreground"
                  title={m.night_state_held({ party: entry.held.name })}
                >
                  <span
                    class="rounded-[1px] block-1.5 inline-1.5"
                    style:background-color={entry.held.color}
                  ></span>{entry.held.short}</span
                >
              {/if}
            </span>
          </span>
          <ol class="grid gap-0.5 min-inline-0">
            {#each lines(entry) as line, rank (line.option.id)}
              {const isDecided = $derived(
                band?.ids.includes(line.option.id) === true,
              )}
              {const isStrong = $derived(
                band == null ? rank < entry.contest.seats : isDecided,
              )}
              <li
                class={cn(
                  "grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-2 font-figures tabular-nums",
                  {
                    "border-bs border-dashed border-foreground/40 pbs-0.5":
                      entry.contest.seats > 1 && rank === entry.contest.seats,
                  },
                )}
              >
                <span class="flex items-center gap-1.5 min-inline-0">
                  <span
                    class="shrink-0 rounded-full block-2 inline-2"
                    style:background-color={paint(line.option, band)}
                  ></span>
                  <span
                    class={cn("truncate font-sans", {
                      "font-semibold": isStrong,
                      "text-muted-foreground": !isStrong,
                    })}>{@render marked(line.option.label)}</span
                  >
                  {#if line.option.party != null}
                    <span
                      class="shrink-0 font-sans text-[0.6875rem] text-muted-foreground"
                      >{line.option.party.short}</span
                    >
                  {/if}
                  {#if isDecided && band?.isProjected === true}
                    <CircleDashedCheckIcon
                      class="shrink-0 block-3.5 inline-3.5"
                      aria-label={m.night_band_projected()}
                    >
                      <title
                        >{m.night_band_called({
                          time: stamp(summary.projectedAt),
                        })}</title
                      >
                    </CircleDashedCheckIcon>
                  {:else if isDecided}
                    <CircleCheckIcon
                      class="shrink-0 block-3.5 inline-3.5"
                      aria-label={m.night_state_won()}
                    />
                  {/if}
                </span>
                <span
                  class={cn({
                    "font-semibold": band == null ? rank === 0 : isDecided,
                    "text-muted-foreground":
                      band == null ? rank > 0 : !isDecided,
                  })}>{line.share == null ? "" : percent(line.share)}</span
                >
              </li>
            {:else}
              <li class="text-muted-foreground">–</li>
            {/each}
            {#if entry.isUnopposed && summary.total === 0}
              <li class="text-[0.6875rem] text-muted-foreground">
                {m.night_state_unopposed()}
              </li>
            {/if}
          </ol>
          <span
            class={cn("text-end font-figures tabular-nums", {
              "font-semibold": summary.later == null,
              "text-xs whitespace-nowrap text-muted-foreground":
                summary.later != null,
            })}
            style:color={band != null || pick == null || summary.total === 0
              ? undefined
              : `color-mix(in oklab, ${tone(pick)} 75%, var(--foreground))`}
            >{summary.later != null
              ? m.night_later()
              : pick == null || summary.total === 0
                ? "–"
                : `+${lead(summary.margin)}`}</span
          >
          <span
            class={cn("hidden items-center justify-end gap-1.5 sm:flex", {
              invisible: summary.later != null,
            })}
          >
            <span
              class="relative overflow-hidden rounded-xs bg-muted block-1.5 inline-8"
              aria-hidden="true"
            >
              <span
                class="absolute inset-y-0 inset-s-0 bg-foreground"
                style:inline-size="{(summary.counted ?? 0) * 100}%"
              ></span>
            </span>
            <span
              class="text-end font-figures text-muted-foreground tabular-nums inline-8"
              >{summary.counted == null
                ? "–"
                : percent(summary.counted, 0)}</span
            >
          </span>
        </a>
      </li>
    {/each}
  </ol>
</div>
