<script lang="ts">
  import { createQuery } from "@tanstack/svelte-query";
  import { count, percent } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";
  import { orpc } from "#lib/rpc.js";

  const { id }: { id: string } = $props();

  const lead = createQuery(() =>
    orpc.elections.lead.queryOptions({ input: { id } }),
  );

  const race = $derived(lead.data ?? null);
  const isSeated = $derived(race != null && race.contested > 1);
</script>

<span class="grid gap-1.5 block-17">
  {#if race != null}
    <span class="flex overflow-hidden rounded-xs bg-muted block-1.5">
      {#each race.results as row (row.key)}
        <span
          style:inline-size="{(isSeated
            ? (row.seats ?? 0) / race.contested
            : row.share) * 100}%"
          style:background={swatch(row.color, row.key)}
        ></span>
      {/each}
    </span>
    <span class="grid gap-0.5 text-[0.8125rem]">
      {#each race.results.slice(0, 2) as row (row.key)}
        <span class="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3">
          <span class="flex items-center gap-1.5 min-inline-0">
            <span
              class="shrink-0 rounded-xs block-2 inline-2"
              style:background={swatch(row.color, row.key)}
            ></span>
            <span class="truncate"
              >{isSeated && row.person == null
                ? (row.short ?? row.label)
                : row.label}</span
            >
          </span>
          <span class="font-figures text-xs font-semibold tabular-nums"
            >{isSeated
              ? m.polity_seats({
                  seats: count(row.seats ?? 0),
                  size: count(race.contested),
                })
              : percent(row.share)}</span
          >
        </span>
      {/each}
    </span>
  {:else if lead.isPending}
    <span class="animate-pulse rounded-xs bg-muted block-1.5"></span>
  {/if}
</span>
