<script lang="ts">
  import PlaceMap from "#routes/[...path]/map.svelte";
  import { resolve } from "$app/paths";
  import ChevronLeftIcon from "@lucide/svelte/icons/chevron-left";
  import ChevronRightIcon from "@lucide/svelte/icons/chevron-right";
  import { createQuery } from "@tanstack/svelte-query";
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";
  import { orpc } from "#lib/rpc.js";

  const {
    elections,
    party,
    name,
  }: {
    elections: Array<{ id: string; date: string }>;
    party: string;
    name: string;
  } = $props();

  let index = $state(0);

  const chosen = $derived(elections[index] ?? elections[0]);
  const hub = createQuery(() =>
    orpc.elections.get.queryOptions({
      input: { id: chosen?.id ?? "" },
      enabled: chosen != null,
    }),
  );
  const found = $derived.by(() => {
    for (const race of hub.data?.races ?? []) {
      const shade =
        race.places?.legend.findIndex(
          (item) => item.party === party || item.key === `party:${party}`,
        ) ?? -1;
      if (race.map != null && race.places != null && shade >= 0) {
        return { race, map: race.map, places: race.places, shade };
      }
    }
    return null;
  });
  const noun = $derived(found?.places.levels[0]?.noun ?? "");
</script>

{#snippet step(
  to: number,
  text: (input: { label: string }) => string,
  Icon: typeof ChevronLeftIcon,
)}
  {const target = $derived(elections[to])}
  <button
    type="button"
    class="flex shrink-0 items-center justify-center rounded-sm border border-input bg-card keycap block-8 inline-8 hover:bg-secondary disabled:pointer-events-none disabled:opacity-40"
    disabled={target == null}
    aria-label={target == null
      ? undefined
      : text({ label: target.date.slice(0, 4) })}
    onclick={() => (index = to)}><Icon class="block-4 inline-4" /></button
  >
{/snippet}

{#if chosen != null}
  <section
    class="col-span-12 grid gap-3 bg-card px-3.5 pbs-3.5 pbe-3.5 min-inline-0"
    aria-labelledby="strength-title"
    aria-busy={hub.isPending}
  >
    <div class="flex flex-wrap items-start justify-between gap-x-6 gap-y-2">
      <div class="grid gap-0.5 min-inline-0">
        <h2 id="strength-title" class="heading">
          {found == null ? name : m.party_strength_title({ party: name, noun })}
        </h2>
        <p
          class="block-lh-2 text-[0.8125rem] text-pretty text-muted-foreground max-inline-[64ch]"
        >
          {#if found != null && hub.data != null}
            {m.party_strength_lede({
              noun,
              race: found.race.name,
              election: hub.data.name,
            })}
          {:else if !hub.isPending}
            {m.party_strength_missing()}
          {/if}
        </p>
      </div>
      {#if elections.length > 1}
        <div class="flex items-center gap-1.5">
          {@render step(index + 1, m.series_previous, ChevronLeftIcon)}
          {#if found?.race.path == null}
            <span
              class="grid place-items-center rounded-sm border border-input font-figures text-sm tabular-nums block-8 inline-16"
              >{chosen.date.slice(0, 4)}</span
            >
          {:else}
            <a
              class="grid place-items-center rounded-sm border border-input font-figures text-sm tabular-nums block-8 inline-16 hover:bg-secondary"
              href={resolve("/[...path]", { path: found.race.path })}
              aria-label={m.party_strength_open({
                year: chosen.date.slice(0, 4),
              })}>{chosen.date.slice(0, 4)}</a
            >
          {/if}
          {@render step(index - 1, m.series_next, ChevronRightIcon)}
        </div>
      {/if}
    </div>
    {#if found != null}
      {#if found.map.tiles != null}
        {#key chosen.id}
          <PlaceMap
            places={found.places}
            colors={found.places.legend.map((item) =>
              swatch(item.color, item.key),
            )}
            tiles={found.map.tiles}
            own={found.map.own}
            frame={found.map.frame}
            isApproximate={found.map.isApproximate}
            {noun}
            votes={m.col_votes()}
            names={found.places.legend.map((item) =>
              item.person == null ? (item.short ?? item.label) : item.label,
            )}
            levels={[]}
            span={null}
            shade={found.shade}
          />
        {/key}
      {:else}
        <p
          class="grid place-items-center border border-dashed border-border px-4 text-center text-sm text-pretty text-muted-foreground block-[min(24rem,50svh)]"
        >
          {m.map_rebuilding()}
        </p>
      {/if}
    {:else if hub.isPending}
      <div
        class="animate-pulse rounded-sm bg-muted/40 block-[min(32rem,60svh)] motion-reduce:animate-none"
      ></div>
    {/if}
  </section>
{/if}
