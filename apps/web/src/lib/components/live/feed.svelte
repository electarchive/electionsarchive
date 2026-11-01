<script lang="ts">
  import type { LiveFrame, LiveOption } from "@ea/analytics/live";
  import ChevronDownIcon from "@lucide/svelte/icons/chevron-down";
  import { count } from "#lib/format.js";
  import { clock, surname, tone } from "#lib/live/format.js";
  import { sum } from "#lib/live/standing.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";

  const {
    options,
    pair,
    frames,
    at,
    onPick,
  }: {
    options: Array<LiveOption>;
    pair: [number, number];
    frames: Array<LiveFrame>;
    at: string | null;
    onPick: (at: string | null) => void;
  } = $props();

  const LIMIT = 8;

  let isAll = $state(false);

  const entries = $derived.by(() => {
    const list: Array<{
      at: string;
      added: number;
      net: number;
      total: number;
      isCorrection: boolean;
    }> = [];
    let previous: LiveFrame | null = null;
    for (const frame of frames) {
      const total = sum(frame.totals);
      const before = previous == null ? 0 : sum(previous.totals);
      const gainA =
        (frame.totals[pair[0]] ?? 0) - (previous?.totals[pair[0]] ?? 0);
      const gainB =
        (frame.totals[pair[1]] ?? 0) - (previous?.totals[pair[1]] ?? 0);
      list.push({
        at: frame.at,
        added: total - before,
        net: gainA - gainB,
        total,
        isCorrection: total < before,
      });
      previous = frame;
    }
    return list.toReversed();
  });
  const shown = $derived(isAll ? entries : entries.slice(0, LIMIT));
  const latest = $derived(entries[0]?.at ?? null);
</script>

<section class="grid content-start gap-4" aria-labelledby="feed-title">
  <h2 id="feed-title" class="flex items-end heading text-lg! block-8">
    {m.night_feed_title()}
  </h2>
  {#if entries.length === 0}
    <p
      class="border-bs border-foreground/80 pbs-3 text-[0.8125rem] text-muted-foreground"
    >
      {m.night_feed_none()}
    </p>
  {:else}
    <ol class="grid border-bs border-foreground/80">
      {#each shown as entry (entry.at)}
        {const isChosen = $derived(
          at === entry.at || (at == null && entry.at === latest),
        )}
        {const gainer = $derived(options[entry.net >= 0 ? pair[0] : pair[1]])}
        <li>
          <button
            type="button"
            class={cn(
              "grid grid-cols-[4.25rem_minmax(0,1fr)_auto] items-baseline gap-x-3 border-be border-border px-1.5 py-2 text-start text-[0.8125rem] inline-full hover:highlighter",
              { highlighter: isChosen },
            )}
            aria-current={isChosen ? "time" : undefined}
            onclick={() => onPick(entry.at === latest ? null : entry.at)}
          >
            <span
              class={cn("font-figures tabular-nums", {
                "font-semibold": isChosen,
                "text-muted-foreground": !isChosen,
              })}>{clock(entry.at)}</span
            >
            <span class="grid gap-0.5">
              {#if entry.isCorrection}
                <span class="font-semibold"
                  >{m.night_feed_correction({
                    count: count(Math.abs(entry.added)),
                  })}</span
                >
              {:else}
                <span>{m.night_feed_added({ count: count(entry.added) })}</span>
              {/if}
              {#if entry.net !== 0}
                <span
                  class="flex items-center gap-1.5 text-xs font-semibold"
                  style:color="color-mix(in oklab, {tone(gainer)} 82%, var(--foreground))"
                >
                  <span
                    class="shrink-0 rounded-full block-1.5 inline-1.5"
                    style:background-color={tone(gainer)}
                  ></span>
                  {m.night_feed_net({
                    name: surname(gainer),
                    count: count(Math.abs(entry.net)),
                  })}
                </span>
              {/if}
            </span>
            <span
              class="text-end font-figures text-muted-foreground tabular-nums"
              >{count(entry.total)}</span
            >
          </button>
        </li>
      {/each}
    </ol>
    {#if entries.length > LIMIT}
      <button
        type="button"
        class="-mbs-4 flex items-center justify-center gap-1.5 border-be border-border py-2 text-[0.8125rem] font-semibold text-muted-foreground hover:highlighter hover:text-foreground"
        aria-expanded={isAll}
        onclick={() => (isAll = !isAll)}
      >
        {isAll
          ? m.night_feed_fewer()
          : m.night_feed_all({ count: entries.length })}
        <ChevronDownIcon
          class={cn("transition-transform block-3.5 inline-3.5", {
            "rotate-180": isAll,
          })}
          aria-hidden="true"
        />
      </button>
    {/if}
  {/if}
</section>
