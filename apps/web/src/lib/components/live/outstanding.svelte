<script lang="ts">
  import type { LiveEstimate, LiveOption } from "@ea/analytics/live";
  import type { Standing } from "#lib/live/standing.js";
  import { isHover } from "#lib/explain.svelte.js";
  import { count, percent } from "#lib/format.js";
  import {
    compact,
    lead,
    placeName,
    places,
    surname,
    tone,
  } from "#lib/live/format.js";
  import { ranked, sum, toward } from "#lib/live/standing.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";
  import MethodLink from "./method-link.svelte";

  let {
    options,
    now,
    estimate,
    href,
    selected = $bindable(null),
    hovered = $bindable(null),
  }: {
    options: Array<LiveOption>;
    now: Standing;
    estimate: LiveEstimate | null;
    href?: (unitId: string) => string | null;
    selected?: string | null;
    hovered?: string | null;
  } = $props();

  const a = $derived(now.pair[0]);
  const b = $derived(now.pair[1]);

  const flip = $derived(toward(estimate, options[a]));

  const rows = $derived.by(() => {
    if (estimate == null) {
      return [];
    }
    return estimate.units
      .flatMap((unit) => {
        const row = now.units.get(unit.unitId);
        if (row == null) {
          return [];
        }
        const left = Math.round(
          Math.max(0, unit.expectedTotal.mid - row.total),
        );
        if (left === 0) {
          return [];
        }
        return [
          {
            row,
            left,
            expected: Math.max(row.total, unit.expectedTotal.mid),
            lean: unit.remainingMargin.mid * flip,
          },
        ];
      })
      .toSorted((x, y) => y.left - x.left);
  });
  const top = $derived(rows.slice(0, 8));
  const widest = $derived(Math.max(1, ...top.map((entry) => entry.expected)));
  const left = $derived(
    estimate == null
      ? { low: 0, mid: 0, high: 0 }
      : {
          low: Math.max(0, Math.round(estimate.remaining.votes.low)),
          mid: Math.max(0, Math.round(estimate.remaining.votes.mid)),
          high: Math.max(0, Math.round(estimate.remaining.votes.high)),
        },
  );

  const path = $derived.by(() => {
    if (estimate == null || left.high < 1 || now.total === 0) {
      return null;
    }
    const votesA = now.totals[a] ?? 0;
    const votesB = now.totals[b] ?? 0;
    const trailer = votesA >= votesB ? b : a;
    const gap = Math.abs(votesA - votesB);
    const lean = estimate.remaining.margin.mid * flip;
    const trailerShare = trailer === a ? (1 + lean) / 2 : (1 - lean) / 2;
    const isMost = left.mid < 1 || gap >= left.mid;
    const pool = isMost ? left.high : left.mid;
    const needed = (pool + gap) / (2 * pool);
    return { trailer, needed, trailerShare, pool, isMost, isOut: needed > 1 };
  });

  const late = $derived.by(() => {
    if (estimate?.late == null) {
      return null;
    }
    const added = sum(estimate.late.totals);
    if (added < 1) {
      return null;
    }
    const [first, second] = ranked(estimate.late.totals);
    if (first == null || second == null) {
      return null;
    }
    return {
      added,
      first,
      second,
      firstShare: (estimate.late.totals[first] ?? 0) / added,
      secondShare: (estimate.late.totals[second] ?? 0) / added,
    };
  });
</script>

<section class="grid gap-3">
  <header class="grid gap-1">
    <div class="flex items-center gap-1.5">
      <h3 class="heading text-lg!">{m.night_left_title()}</h3>
      <MethodLink section="turnout" />
    </div>
    {#if estimate != null && left.high >= 1}
      <p class="text-sm text-muted-foreground">
        {m.night_left_summary({
          count: compact(left.mid),
          low: compact(left.low),
          high: compact(left.high),
        })}
      </p>
    {/if}
  </header>
  {#if path != null}
    {const name = $derived(surname(options[path.trailer]))}
    {const pace = $derived(Math.max(0, Math.min(1, path.trailerShare)))}
    {const digits = $derived(places([path.needed - pace]))}
    <p class="font-serif text-[1.0625rem] leading-snug text-pretty">
      {#if path.isOut}
        {m.night_left_out({ name, count: compact(path.pool) })}
      {:else if path.isMost}
        {m.night_left_needs_most({
          name,
          count: compact(path.pool),
          needed: percent(path.needed, digits),
          pace: percent(pace, digits),
        })}
      {:else}
        {m.night_left_needs({
          name,
          needed: percent(path.needed, digits),
          pace: percent(pace, digits),
        })}
      {/if}
    </p>
    {#if !path.isOut}
      <div
        class="relative overflow-hidden rounded-xs bg-muted block-3"
        aria-hidden="true"
      >
        <span
          class="absolute inset-y-0 inset-s-0 transition-[inline-size] duration-700 ease-out motion-reduce:transition-none"
          style:inline-size="{Math.min(1, path.trailerShare) * 100}%"
          style:background-color={tone(options[path.trailer])}
        ></span>
        <span
          class="absolute -inset-y-0.5 bg-foreground inline-0.5"
          style:inset-inline-start="{Math.min(1, path.needed) * 100}%"
        ></span>
      </div>
    {/if}
  {/if}
  {#if late != null}
    <div class="grid gap-1 text-sm text-pretty text-muted-foreground">
      <p>
        {m.night_left_late({
          count: count(late.added),
          first: surname(options[late.first]),
          firstShare: percent(late.firstShare, 1),
          second: surname(options[late.second]),
          secondShare: percent(late.secondShare, 1),
        })}
      </p>
      <p>{m.night_left_unknown()}</p>
    </div>
  {/if}
  {#if top.length === 0}
    <p
      class="rounded-xs border border-dashed border-border p-4 text-sm text-pretty text-muted-foreground"
    >
      {m.night_left_empty()}
    </p>
  {:else}
    <ul class="grid gap-1">
      {#each top as entry (entry.row.unit.id)}
        {const leanTone = $derived(tone(options[entry.lean >= 0 ? a : b]))}
        {const link = $derived(href?.(entry.row.unit.id) ?? null)}
        <li
          onpointerenter={(event) => {
            if (isHover(event)) hovered = entry.row.unit.id;
          }}
          onpointerleave={() => (hovered = null)}
        >
          <svelte:element
            this={link == null ? "div" : "a"}
            href={link ?? undefined}
            class={cn(
              "grid grid-cols-[minmax(6rem,8rem)_1fr_auto] items-center gap-3 rounded-xs p-1 text-start text-[0.8125rem] inline-full hover:highlighter",
              {
                highlighter:
                  hovered === entry.row.unit.id ||
                  selected === entry.row.unit.id,
              },
            )}
          >
            <span class="truncate font-semibold"
              >{placeName(entry.row.unit.name)}</span
            >
            <span
              class="relative flex overflow-hidden rounded-xs block-3"
              style:inline-size="{(entry.expected / widest) * 100}%"
            >
              <span
                class="bg-foreground/70 transition-[inline-size] duration-700 block-full motion-reduce:transition-none"
                style:inline-size="{(entry.row.total / entry.expected) * 100}%"
              ></span>
              <span
                class="grow block-full"
                style:background-color="color-mix(in oklab, {leanTone} 40%, var(--background))"
              ></span>
            </span>
            <span class="font-figures text-muted-foreground tabular-nums">
              {m.night_left_row({
                count: compact(entry.left),
                lean:
                  entry.lean === 0
                    ? m.outcome_tied()
                    : `${surname(options[entry.lean > 0 ? a : b])} +${Math.abs(entry.lean) < 0.005 ? lead(entry.lean) : lead(entry.lean, 0)}`,
              })}
            </span>
          </svelte:element>
        </li>
      {/each}
    </ul>
    <p class="flex flex-wrap gap-x-3 text-xs text-muted-foreground">
      <span class="flex items-center gap-1.5"
        ><span class="bg-foreground/70 block-2 inline-4"
        ></span>{m.night_left_counted()}</span
      >
      <span class="flex items-center gap-1.5"
        ><span class="bg-muted-foreground/40 block-2 inline-4"
        ></span>{m.night_left_remaining()}</span
      >
    </p>
  {/if}
</section>
