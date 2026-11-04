<script lang="ts">
  import type { LiveOffice } from "@ea/analytics/live";
  import { unroll } from "svelte-islands";
  import type { Desk } from "#lib/live/board/desk.svelte.js";
  import type { Summary } from "#lib/live/summary.js";
  import Flip from "#lib/components/live/dashboard/flip.svelte";
  import Swing from "#lib/components/live/dashboard/swing.svelte";
  import { percent } from "#lib/format.js";
  import { ahead, called, short } from "#lib/live/board/labels.js";
  import { swing } from "#lib/live/flips.js";
  import { lead, surname, tone } from "#lib/live/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";
  import Mark from "./mark.svelte";

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
      const option = ahead(summary);
      if (kind === "closest" && called(summary) == null) {
        found.push(summary);
      }
      const changing = desk.shifts.get(summary.race.id);
      if (
        kind === "flips" &&
        option != null &&
        changing != null &&
        changing.flip !== "held"
      ) {
        found.push(summary);
      }
    }
    return found.toSorted((a, b) =>
      kind === "closest"
        ? a.margin - b.margin
        : Number(called(b) != null) - Number(called(a) != null) ||
          b.margin - a.margin,
    );
  });
  const shelf = unroll(40);
  const changing = $derived(
    kind === "flips"
      ? swing(rows.map((summary) => desk.shifts.get(summary.race.id) ?? null))
      : null,
  );
</script>

{#if rows.length === 0}
  <p class="text-[0.75em] text-muted-foreground">
    {kind === "closest" ? m.board_closest_empty() : m.board_flips_empty()}
  </p>
{:else}
  {#if changing != null}
    <div class="pbe-1 text-[0.8125em]">
      <Swing
        swing={changing}
        party={(id: string) => desk.party(id)}
        isSmall={true}
      />
    </div>
  {/if}
  <ol class="grid text-[0.8125em]">
    {#each rows.slice(0, shelf.limit) as summary (summary.race.id)}
      {const option = $derived(ahead(summary))}
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
            <Mark {summary} />
            {const found = $derived(desk.shifts.get(summary.race.id))}
            {#if kind === "flips" && found != null && found.flip !== "held"}
              <Flip
                flip={found.flip}
                from={desk.party(found.from)}
                to={desk.party(found.to)}
                isCompact={true}
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
    {#if shelf.limit < rows.length}
      <li {@attach shelf.tail(rows.length)}></li>
    {/if}
  </ol>
{/if}
