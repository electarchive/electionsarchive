<script lang="ts">
  import type { LiveOffice } from "@ea/analytics/live";
  import type { Desk } from "#lib/live/board/desk.svelte.js";
  import { ahead, called, short, strength } from "#lib/live/board/labels.js";
  import { surname } from "#lib/live/format.js";
  import { edge } from "#lib/live/summary.js";
  import { cn } from "#lib/utils.js";

  const { desk, office = "senate" }: { desk: Desk; office?: LiveOffice } =
    $props();

  const isHouse = $derived(office === "house");
  const tiles = $derived(
    [...desk.feed.summaries.values()]
      .filter((summary) => summary.race.office === office)
      .toSorted(
        (a, b) =>
          a.race.state.localeCompare(b.race.state) ||
          Number(a.race.district ?? 0) - Number(b.race.district ?? 0) ||
          a.race.slug.localeCompare(b.race.slug),
      ),
  );

  function describe(summary: (typeof tiles)[number]) {
    const option = ahead(summary);
    return option == null
      ? short(summary.race)
      : `${short(summary.race)}: ${surname(option)} ${edge(summary)}`;
  }
</script>

<ul
  class={cn("grid gap-0.5", {
    "grid-cols-[repeat(auto-fill,minmax(0.6875rem,1fr))]": isHouse,
    "grid-cols-[repeat(auto-fill,minmax(2.25rem,1fr))]": !isHouse,
  })}
>
  {#each tiles as summary (summary.race.id)}
    {const isPicked = $derived(desk.selected?.id === summary.race.id)}
    {const isWon = $derived(called(summary) != null)}
    {const mark = $derived(desk.hatch(summary))}
    <li>
      <button
        type="button"
        class={cn(
          "grid place-content-center rounded-xs font-semibold uppercase outline-offset-1 transition-[background-color] duration-500 inline-full motion-reduce:transition-none",
          { "aspect-square": isHouse, "text-[0.6875em] block-6": !isHouse },
          { "text-white": isWon, "text-foreground": !isWon },
          { "outline-2 outline-foreground": isPicked },
        )}
        style:background-color={mark?.fill ?? strength(summary)}
        style:background-image={mark?.stripes}
        title={describe(summary)}
        aria-label={describe(summary)}
        aria-pressed={isPicked}
        onclick={() => desk.select(summary.race.slug)}
      >
        {#if !isHouse}{summary.race.state}{/if}
      </button>
    </li>
  {/each}
</ul>
