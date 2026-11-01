<script lang="ts">
  import type { LiveOffice } from "@ea/analytics/live";
  import type { Desk } from "#lib/live/board/desk.svelte.js";
  import { surname, tone } from "#lib/live/format.js";
  import { party, tally } from "#lib/live/summary.js";
  import { m } from "#lib/paraglide/messages.js";

  const { desk }: { desk: Desk } = $props();

  const OFFICES: Array<{ office: LiveOffice; name: () => string }> = [
    { office: "senate", name: m.board_page_senate },
    { office: "house", name: m.board_page_house },
    { office: "governor", name: m.board_page_governor },
  ];

  const summaries = $derived([...desk.feed.summaries.values()]);
  const rows = $derived.by(() => {
    const found = [];
    for (const entry of OFFICES) {
      const seats = summaries.filter(
        (summary) => summary.race.office === entry.office,
      ).length;
      if (seats > 0) {
        found.push({
          ...entry,
          seats,
          balance: tally(summaries, entry.office),
        });
      }
    }
    return found;
  });
</script>

{#if rows.length > 0}
  <ul class="flex items-center gap-4">
    {#each rows as row (row.office)}
      <li class="grid gap-0.5 leading-none">
        <span class="flex items-baseline gap-1.5 text-[0.6875rem]">
          <span class="font-semibold">{row.name()}</span>
          {#each row.balance.groups.slice(0, 2) as group (party(group.option))}
            <span
              class="font-figures text-[0.8125rem] font-[760] tabular-nums"
              style:color={tone(group.option)}
              title={group.option.party?.name ?? group.option.label}
              ><span class="sr-only"
                >{group.option.party?.short ?? surname(group.option)}</span
              >
              {group.won + group.leading}</span
            >
          {:else}
            <span class="font-figures text-muted-foreground tabular-nums"
              >{m.board_power_seats({ count: row.seats })}</span
            >
          {/each}
        </span>
        <span
          class="flex overflow-hidden rounded-[1px] bg-accent block-1 inline-full"
          aria-hidden="true"
        >
          {#each row.balance.groups as group (party(group.option))}
            <span
              class="transition-[inline-size] duration-700 motion-reduce:transition-none"
              style:inline-size="{(group.won / row.seats) * 100}%"
              style:background-color={tone(group.option)}
            ></span>
            <span
              class="transition-[inline-size] duration-700 motion-reduce:transition-none"
              style:inline-size="{(group.leading / row.seats) * 100}%"
              style:background-color="color-mix(in oklab, {tone(group.option)} 45%,
              var(--background))"
            ></span>
          {/each}
        </span>
      </li>
    {/each}
  </ul>
{/if}
