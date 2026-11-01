<script lang="ts">
  import type { LiveOffice } from "@ea/analytics/live";
  import CheckIcon from "@lucide/svelte/icons/check";
  import type { Desk } from "#lib/live/board/desk.svelte.js";
  import type { Summary } from "#lib/live/summary.js";
  import { percent } from "#lib/format.js";
  import { short } from "#lib/live/board/labels.js";
  import { lead, surname, tone } from "#lib/live/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";

  const {
    desk,
    kind,
    office,
  }: { desk: Desk; kind: "closest" | "flips"; office?: LiveOffice } = $props();

  const rows = $derived.by(() => {
    const found: Array<Summary> = [];
    for (const summary of desk.feed.summaries.values()) {
      if (
        summary.total === 0 ||
        (office != null && summary.race.office !== office)
      ) {
        continue;
      }
      const ahead = summary.winner ?? summary.leader;
      if (kind === "closest" && summary.winner == null) {
        found.push(summary);
      }
      if (
        kind === "flips" &&
        ahead != null &&
        !ahead.isIncumbent &&
        summary.race.options.some((option) => option.isIncumbent)
      ) {
        found.push(summary);
      }
    }
    return found.toSorted((a, b) =>
      kind === "closest"
        ? a.margin - b.margin
        : Number(b.winner != null) - Number(a.winner != null) ||
          b.margin - a.margin,
    );
  });
</script>

{#if rows.length === 0}
  <p class="text-[0.75em] text-muted-foreground">
    {kind === "closest" ? m.board_closest_empty() : m.board_flips_empty()}
  </p>
{:else}
  <ol class="grid text-[0.8125em]">
    {#each rows as summary (summary.race.id)}
      {const option = $derived(summary.winner ?? summary.leader)}
      {const isPicked = $derived(desk.selected?.id === summary.race.id)}
      <li>
        <button
          type="button"
          class={cn(
            "grid grid-cols-[minmax(0,1fr)_auto_auto] items-center gap-x-2 border-be border-border/60 p-1  text-start transition-colors inline-full",
            { "bg-accent": isPicked, "hover:bg-muted": !isPicked },
          )}
          aria-pressed={isPicked}
          onclick={() => desk.select(summary.race.slug)}
        >
          <span class="flex items-center gap-1.5 min-inline-0">
            <span
              class="shrink-0 rounded-full block-2 inline-2"
              style:background-color={tone(option ?? undefined)}
            ></span>
            <span class="truncate font-semibold">{short(summary.race)}</span>
            <span class="truncate text-muted-foreground"
              >{surname(option ?? undefined)}</span
            >
            {#if summary.winner != null}
              <CheckIcon
                class="shrink-0 block-3 inline-3"
                aria-label={m.board_power_won()}
              />
            {/if}
          </span>
          <span class="font-figures font-semibold tabular-nums"
            >+{lead(summary.margin)}</span
          >
          <span
            class="text-end font-figures text-muted-foreground tabular-nums min-inline-[3.5ch]"
            >{summary.counted == null ? "" : percent(summary.counted, 0)}</span
          >
        </button>
      </li>
    {/each}
  </ol>
{/if}
