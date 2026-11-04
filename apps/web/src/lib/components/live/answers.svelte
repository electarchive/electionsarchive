<script lang="ts">
  import type { LiveEstimate, LiveOption } from "@ea/analytics/live";
  import type { Band } from "#lib/live/format.js";
  import type { Standing } from "#lib/live/standing.js";
  import { count, percent } from "#lib/format.js";
  import { lead, paint } from "#lib/live/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";
  import Counter from "./counter.svelte";

  const {
    options,
    now,
    estimate,
    band = null,
    isWaiting,
    isLater = false,
  }: {
    options: Array<LiveOption>;
    now: Standing;
    estimate: LiveEstimate | null;
    band?: Band | null;
    isWaiting: boolean;
    isLater?: boolean;
  } = $props();

  const order = $derived(
    isWaiting
      ? options.flatMap((option, index) => (option.isWriteIn ? [] : [index]))
      : now.order.filter(
          (index) =>
            options[index]?.isWriteIn !== true || (now.totals[index] ?? 0) > 0,
        ),
  );
  const share = (index: number) =>
    now.total > 0 ? (now.totals[index] ?? 0) / now.total : 0;
  const first = $derived(isWaiting ? null : (order[0] ?? null));
  const gap = $derived.by(() => {
    const second = order[1];
    if (first == null || second == null) {
      return null;
    }
    const votes = (now.totals[first] ?? 0) - (now.totals[second] ?? 0);
    return votes > 0 ? { votes, margin: votes / now.total } : null;
  });
  const shareIn = $derived(
    estimate?.status === "estimating" || estimate?.status === "settled"
      ? estimate.shareIn
      : null,
  );
</script>

<section class="grid gap-4" aria-labelledby="answers-title">
  <h2 id="answers-title" class="sr-only">{m.night_answers_title()}</h2>
  <div
    class="flex overflow-hidden rounded-xs bg-muted block-3"
    role="img"
    aria-label={m.night_answers_bar()}
  >
    {#if now.total > 0}
      {#each order as index (options[index]?.id ?? index)}
        <span
          class="border-e border-background transition-[inline-size] duration-700 ease-out block-full last:border-e-0 motion-reduce:transition-none"
          style:inline-size="{share(index) * 100}%"
          style:background-color={paint(options[index], band)}
        ></span>
      {/each}
    {/if}
  </div>
  <ol class="grid border-bs border-foreground/80">
    {#each order as index (options[index]?.id ?? index)}
      {const option = $derived(options[index])}
      {const color = $derived(paint(option, band))}
      {const isDecided = $derived(
        band != null && option != null && band.ids.includes(option.id),
      )}
      {const isFirst = $derived(band == null && index === first)}
      {const isStrong = $derived(isFirst || isDecided)}
      <li
        class="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-5 gap-y-1.5 border-be border-border py-3 sm:grid-cols-[minmax(0,1fr)_minmax(8rem,16rem)_minmax(6.5rem,auto)]"
      >
        <span class="grid gap-1 border-s-[3px] ps-3" style:border-color={color}>
          <span class="flex flex-wrap items-center gap-x-2 gap-y-1">
            <span
              class={cn("text-[0.9375rem]/snug tracking-tight text-pretty", {
                "font-[720]": isStrong,
                "font-medium": !isStrong,
                "text-muted-foreground": band != null && !isDecided,
              })}>{option?.label ?? ""}</span
            >
            {#if isFirst && gap != null}
              <span
                class="shrink-0 rounded-full border px-1.5 py-px text-[0.6875rem] font-semibold"
                style:border-color={color}
                style:color="color-mix(in oklab, {color} 75%, var(--foreground))"
                >{m.night_answers_leading()}</span
              >
            {/if}
          </span>
          {#if index === first && gap != null}
            <span
              class="font-figures text-xs text-muted-foreground tabular-nums"
              >{m.night_answers_lead({
                margin: lead(gap.margin),
                votes: count(gap.votes),
              })}</span
            >
          {/if}
        </span>
        <span
          class="mis-3.75 sm:mis-0 relative col-span-2 overflow-hidden rounded-full bg-muted block-1.5 sm:col-span-1 sm:col-start-2 sm:row-start-1"
          aria-hidden="true"
        >
          <span
            class="absolute inset-y-0 inset-s-0 rounded-full transition-[inline-size] duration-700 ease-out motion-reduce:transition-none"
            style:inline-size="{share(index) * 100}%"
            style:background-color={color}
          ></span>
        </span>
        <span
          class="col-start-2 row-start-1 grid justify-items-end gap-0.5 text-end sm:col-start-3"
        >
          {#if isWaiting}
            <span
              class="font-figures text-xl leading-none font-[780] text-muted-foreground/50"
              aria-hidden="true">–</span
            >
          {:else}
            <Counter
              value={share(index)}
              format={(value: number) => percent(value)}
              class={cn("text-xl leading-none tracking-tight", {
                "font-[780]": isStrong,
                "font-semibold": !isStrong,
                "text-muted-foreground": band != null && !isDecided,
              })}
            />
            <Counter
              value={now.totals[index] ?? 0}
              format={(value: number) =>
                m.night_votes({ count: count(Math.round(value)) })}
              class="text-xs text-muted-foreground"
            />
          {/if}
        </span>
      </li>
    {/each}
  </ol>
  {#if !isLater}
    <div
      class="grid grid-cols-[auto_minmax(4rem,1fr)_auto] items-center gap-x-3 gap-y-0.5 text-[0.8125rem] sm:justify-self-end sm:inline-full sm:max-inline-xl"
    >
      <span class="font-semibold">{m.night_counted()}</span>
      <div
        class="relative overflow-hidden rounded-full bg-muted block-1.5"
        aria-hidden="true"
      >
        {#if shareIn != null}
          <span
            class="absolute inset-y-0 inset-s-0 rounded-full bg-foreground transition-[inline-size] duration-700 ease-out motion-reduce:transition-none"
            style:inline-size="{Math.min(1, shareIn.mid) * 100}%"
          ></span>
        {:else if !isWaiting}
          <span class="absolute inset-0 bg-border"></span>
        {/if}
      </div>
      <span class="font-figures text-muted-foreground tabular-nums">
        {#if isWaiting}
          {m.night_counted_none()}
        {:else if shareIn != null}
          {m.night_counted_expected({
            share: percent(Math.min(0.99, shareIn.mid), 0),
          })}
        {:else}
          {m.night_votes({ count: count(now.total) })}
        {/if}
      </span>
      {#if now.reporting != null && now.expected != null && now.expected > 0}
        <span class="col-start-3 text-end text-xs text-muted-foreground">
          {m.night_precincts({
            reporting: count(now.reporting),
            expected: count(now.expected),
          })}
        </span>
      {/if}
    </div>
  {/if}
</section>
