<script lang="ts">
  import type { LiveOption, LiveRace } from "@ea/analytics/live";
  import { prefersReducedMotion } from "svelte/motion";
  import type { Desk } from "#lib/live/board/desk.svelte.js";
  import { short } from "#lib/live/board/labels.js";
  import { clock, compact, lead, surname, tone } from "#lib/live/format.js";
  import { ranked, sum } from "#lib/live/standing.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";

  const { desk }: { desk: Desk } = $props();

  const LIMIT = 60;

  const entries = $derived.by(() => {
    const found: Array<{
      key: string;
      race: LiveRace;
      at: string;
      leader: LiveOption | undefined;
      margin: number;
      added: number;
    }> = [];
    for (const race of desk.feed.election.races) {
      let previous = 0;
      for (const frame of race.frames) {
        const total = sum(frame.totals);
        if (total > previous) {
          const [first, second] = ranked(frame.totals);
          found.push({
            key: `${race.id}:${frame.at}`,
            race,
            at: frame.at,
            leader: first == null ? undefined : race.options[first],
            margin:
              ((frame.totals[first ?? 0] ?? 0) -
                (frame.totals[second ?? 1] ?? 0)) /
              total,
            added: total - previous,
          });
        }
        previous = total;
      }
    }
    return found
      .toSorted((a, b) => b.at.localeCompare(a.at) || b.added - a.added)
      .slice(0, LIMIT);
  });

  function flash(_node: Element) {
    return {
      duration: prefersReducedMotion.current ? 0 : 1200,
      css: (t: number) =>
        `background-color: color-mix(in oklab, var(--accent) ${Math.round((1 - t) * 70)}%, transparent)`,
    };
  }
</script>

{#if entries.length === 0}
  <p class="text-[0.75em] text-muted-foreground">{m.board_ticker_empty()}</p>
{:else}
  <ol class="grid text-[0.8125em]">
    {#each entries as entry (entry.key)}
      {const isPicked = $derived(desk.selected?.id === entry.race.id)}
      <li in:flash>
        <button
          type="button"
          class={cn(
            "grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-x-2 border-be border-border/60 p-1  text-start inline-full",
            { "bg-accent": isPicked, "hover:bg-muted": !isPicked },
          )}
          onclick={() => desk.select(entry.race.slug)}
        >
          <span class="font-figures text-muted-foreground tabular-nums"
            >{clock(entry.at)}</span
          >
          <span class="flex items-center gap-1.5 min-inline-0">
            <span class="truncate font-semibold">{short(entry.race)}</span>
            <span
              class="shrink-0 rounded-full block-2 inline-2"
              style:background-color={tone(entry.leader)}
            ></span>
            <span class="truncate">{surname(entry.leader)}</span>
            <span class="font-figures text-muted-foreground tabular-nums"
              >+{lead(entry.margin)}</span
            >
          </span>
          <span class="font-figures font-semibold tabular-nums"
            >+{compact(entry.added)}</span
          >
        </button>
      </li>
    {/each}
  </ol>
{/if}
