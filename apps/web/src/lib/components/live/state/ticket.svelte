<script lang="ts">
  import type { Entry } from "#lib/live/ballot.js";
  import BandLabel from "#lib/components/live/band-label.svelte";
  import Flip from "#lib/components/live/dashboard/flip.svelte";
  import Name from "#lib/components/name.svelte";
  import { count, percent } from "#lib/format.js";
  import { turn } from "#lib/live/ballot.js";
  import { paint } from "#lib/live/format.js";
  import { lineup, useReach } from "#lib/live/lineup.js";
  import { bandOf } from "#lib/live/summary.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";
  import Also from "./also.svelte";
  import Seat from "./seat.svelte";

  const { entry, href, title }: { entry: Entry; href: string; title: string } =
    $props();

  const summary = $derived(entry.summary);
  const race = $derived(summary.race);
  const band = $derived(bandOf(summary));
  const flip = $derived(turn(entry));
  const reach = useReach();
  const split = $derived(lineup(race, summary.totals, reach(), 4));
</script>

<a
  {href}
  class={cn(
    "group grid content-start gap-3 bg-background p-4 transition-colors duration-700 outline-none motion-reduce:transition-none sm:px-5",
    {
      "hover:highlighter focus-visible:highlighter": band == null,
      "band focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset":
        band != null,
    },
  )}
  style:--party={band?.fill}
>
  <span class="grid grid-cols-[minmax(0,1fr)_auto] items-start gap-3">
    <span class="heading text-lg! text-pretty">{title}</span>
    <span class="flex items-center gap-1.5 pbs-1">
      {#if flip != null}
        <Flip
          flip={flip.flip}
          from={entry.held}
          to={flip.to}
          isInverse={band != null}
        />
      {/if}
      <Seat held={entry.held} isPlain={band != null} />
    </span>
  </span>
  {#if band != null}
    <BandLabel
      {band}
      isMeasure={race.isMeasure ?? false}
      at={summary.projectedAt}
      class="text-xs"
    />
  {/if}
  <ol class="grid gap-2">
    {#each split.main as line (line.option.id)}
      {const share = $derived(line.share)}
      {const color = $derived(paint(line.option, band))}
      {const isDecided = $derived(band?.ids.includes(line.option.id) === true)}
      <li class="grid gap-1">
        <span
          class="grid grid-cols-[minmax(0,1fr)_auto] items-baseline gap-x-3 text-sm"
        >
          <span class="flex items-baseline gap-1.5 min-inline-0">
            <span
              class="shrink-0 translate-y-[-0.05em] self-center rounded-full block-2.5 inline-2.5"
              style:background-color={color}
            ></span>
            <Name
              label={line.option.label}
              person={line.option.person}
              hasSwatch={false}
              hasLink={false}
              class={cn("truncate", {
                "font-bold": isDecided,
                "font-semibold": !isDecided,
                "text-muted-foreground": band != null && !isDecided,
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
          </span>
          <span class="font-figures tabular-nums">
            {#if share != null}
              <span
                class={cn("font-bold", {
                  "text-muted-foreground": band != null && !isDecided,
                })}>{percent(share)}</span
              >
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
              style:background-color={color}
            ></span>
          </span>
        {/if}
      </li>
    {/each}
  </ol>
  <Also rest={split.rest} total={summary.total} hasLink={false} />
  <span
    class="flex items-center justify-between gap-3 text-xs text-muted-foreground"
  >
    <span class="font-figures tabular-nums"
      >{summary.counted == null
        ? entry.isUnopposed
          ? m.night_state_unopposed()
          : summary.later == null
            ? m.night_nation_waiting()
            : m.night_later()
        : m.night_tip_counted({ share: percent(summary.counted, 0) })}</span
    >
    <span class="font-semibold text-foreground group-hover:underline"
      >{m.night_state_results()}</span
    >
  </span>
</a>
