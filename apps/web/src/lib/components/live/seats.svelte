<script lang="ts">
  import type { LiveOption } from "@ea/analytics/live";
  import type { Standing } from "#lib/live/standing.js";
  import Name from "#lib/components/name.svelte";
  import { count } from "#lib/format.js";
  import { tone } from "#lib/live/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { cn } from "#lib/utils.js";

  const {
    options,
    now,
    seats,
    winners,
  }: {
    options: Array<LiveOption>;
    now: Standing;
    seats: number;
    winners: Array<string>;
  } = $props();

  const top = $derived(Math.max(1, ...now.totals));
  const rows = $derived(
    now.order.flatMap((at, rank) => {
      const option = options[at];
      return option == null
        ? []
        : [
            {
              option,
              rank,
              votes: now.totals[at] ?? 0,
              isInside:
                winners.length > 0
                  ? winners.includes(option.id)
                  : now.total > 0 && rank < seats,
            },
          ];
    }),
  );
</script>

<section class="grid gap-3" aria-labelledby="seats-title">
  <h2 id="seats-title" class="text-[0.8125rem] font-semibold">
    {m.night_seats_title()}
  </h2>
  <ol class="grid">
    {#each rows as row (row.option.id)}
      {#if row.rank === seats}
        <li
          class="flex items-center gap-2 pbs-2 pbe-1 text-xs text-muted-foreground"
          aria-hidden="true"
        >
          <span class="flex-1 border-bs border-dashed border-foreground/40"
          ></span>
          {m.night_seats_line({ count: count(seats) })}
          <span class="flex-1 border-bs border-dashed border-foreground/40"
          ></span>
        </li>
      {/if}
      <li
        class="grid grid-cols-[1.75rem_minmax(0,1fr)_minmax(4rem,12rem)_5.5rem] items-center gap-x-3 font-figures text-sm tabular-nums block-8"
      >
        <span class="text-end text-xs text-muted-foreground"
          >{row.rank + 1}</span
        >
        <span class="flex items-baseline gap-2 min-inline-0">
          <Name
            label={row.option.label}
            person={row.option.person}
            hasSwatch={false}
            hasLink={false}
            class={cn("truncate font-sans", {
              "font-semibold": row.isInside,
              "text-muted-foreground": !row.isInside,
            })}
          />
          {#if row.option.party != null}
            <span class="shrink-0 text-xs text-muted-foreground"
              >{row.option.party.short}</span
            >
          {/if}
        </span>
        <span
          class="relative overflow-hidden rounded-xs bg-muted block-1.5"
          aria-hidden="true"
        >
          <span
            class="absolute inset-y-0 inset-s-0 transition-[inline-size] duration-700 motion-reduce:transition-none"
            style:inline-size="{(row.votes / top) * 100}%"
            style:background-color={tone(row.option)}
            style:opacity={row.isInside ? 1 : 0.45}
          ></span>
        </span>
        <span class={cn("text-end", { "font-semibold": row.isInside })}
          >{count(row.votes)}</span
        >
      </li>
    {/each}
  </ol>
</section>
