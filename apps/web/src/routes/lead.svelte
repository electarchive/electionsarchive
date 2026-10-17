<script lang="ts">
  import { createQuery } from "@tanstack/svelte-query";
  import { count, percent } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";
  import { orpc } from "#lib/rpc.js";
  import { cn } from "#lib/utils.js";

  const { id }: { id: string } = $props();

  const lead = createQuery(() =>
    orpc.elections.lead.queryOptions({ input: { id } }),
  );

  const race = $derived(lead.data ?? null);
  const isSeated = $derived(race != null && race.contested > 1);
  const isCrowded = $derived(
    race != null &&
      race.results.filter((row) =>
        isSeated ? (row.seats ?? 0) > 0 : row.share > 0.005,
      ).length > 2,
  );
</script>

<span class="grid gap-1.5 block-19">
  {#if race != null}
    <span class="flex items-end justify-between gap-4">
      {#each race.results.slice(0, 2) as row, at (row.key)}
        <span
          class={cn("grid min-inline-0", {
            "justify-items-end text-end": at === 1,
          })}
        >
          <span
            class="font-figures text-[1.75rem] leading-none font-[780] tracking-[-0.02em]"
            >{isSeated ? count(row.seats ?? 0) : percent(row.share)}</span
          >
          <span
            class="flex items-baseline gap-1.5 text-[0.875rem] font-semibold max-inline-full"
          >
            {#if isCrowded}
              <span
                class="shrink-0 self-center rounded-xs block-2.5 inline-2.5"
                style:background={swatch(row.color, row.key)}
              ></span>
            {/if}
            <span class="truncate"
              >{isSeated && row.person == null
                ? (row.short ?? row.label)
                : row.label}</span
            >
            {#if row.isLost === true}
              <span class="shrink-0 text-xs text-muted-foreground"
                >{m.outcome_lost()}</span
              >
            {/if}
          </span>
        </span>
      {/each}
    </span>
    <span class="relative flex overflow-hidden bg-muted block-3">
      {#each race.results as row (row.key)}
        <span
          style:inline-size="{(isSeated
            ? (row.seats ?? 0) / race.contested
            : row.share) * 100}%"
          style:background={swatch(row.color, row.key)}
        ></span>
      {/each}
      {#if isSeated}
        <span
          class="absolute -inset-y-px bg-foreground inline-0.5"
          style:inset-inline-start="{((Math.floor(race.contested / 2) + 1) /
            race.contested) *
            100}%"
        ></span>
      {/if}
    </span>
    {#if isSeated}
      <span class="sr-only"
        >{m.polity_seats({
          seats: count(race.results[0]?.seats ?? 0),
          size: count(race.contested),
        })}</span
      >
    {/if}
  {:else if lead.isPending}
    <span class="animate-pulse bg-muted block-3"></span>
  {/if}
</span>
