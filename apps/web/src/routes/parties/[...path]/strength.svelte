<script lang="ts">
  import PlaceMap from "#routes/[...path]/map.svelte";
  import { createQuery } from "@tanstack/svelte-query";
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";
  import { orpc } from "#lib/rpc.js";

  const {
    election,
    party,
    name,
  }: { election: string; party: string; name: string } = $props();

  const hub = createQuery(() =>
    orpc.elections.get.queryOptions({ input: { id: election } }),
  );
  const found = $derived.by(() => {
    for (const race of hub.data?.races ?? []) {
      const index =
        race.places?.legend.findIndex(
          (item) => item.party === party || item.key === `party:${party}`,
        ) ?? -1;
      if (race.map != null && race.places != null && index >= 0) {
        return { race, map: race.map, places: race.places, index };
      }
    }
    return null;
  });
</script>

{#if hub.isPending}
  <section
    class="col-span-12 grid gap-3 bg-card px-3.5 pbs-3.5 pbe-3"
    aria-busy="true"
  >
    <div
      class="animate-pulse rounded-sm bg-muted block-7 inline-72 motion-reduce:animate-none"
    ></div>
    <div
      class="animate-pulse rounded-sm bg-muted block-[min(32rem,60svh)] motion-reduce:animate-none"
    ></div>
  </section>
{:else if found != null}
  {const noun = $derived(found.places.levels[0]?.noun ?? "")}
  <section
    class="col-span-12 grid gap-3 bg-card px-3.5 pbs-3.5 pbe-3.5 min-inline-0"
    aria-labelledby="strength-title"
  >
    <div class="grid gap-0.5">
      <h2
        id="strength-title"
        class="font-serif text-xl/tight font-[640] tracking-[-0.005em]"
      >
        {m.party_strength_title({ party: name, noun })}
      </h2>
      <p class="text-[0.8125rem] text-muted-foreground max-inline-[64ch]">
        {m.party_strength_lede({ noun, race: found.race.name })}
      </p>
    </div>
    <PlaceMap
      places={found.places}
      colors={found.places.legend.map((item) => swatch(item.color, item.key))}
      tiles={found.map.tiles}
      own={found.map.own}
      frames={found.map.frames}
      isApproximate={found.map.isApproximate}
      {noun}
      votes={m.col_votes()}
      names={found.places.legend.map((item) =>
        item.person == null ? (item.short ?? item.label) : item.label,
      )}
      levels={[]}
      span={null}
      shade={found.index}
    />
  </section>
{/if}
