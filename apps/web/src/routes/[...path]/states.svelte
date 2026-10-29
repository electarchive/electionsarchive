<script lang="ts">
  import { createQuery } from "@tanstack/svelte-query";
  import { m } from "#lib/paraglide/messages.js";
  import { swatch } from "#lib/party.js";
  import { orpc } from "#lib/rpc.js";
  import PlaceMap from "./map.svelte";

  const { id }: { id: string } = $props();

  const states = createQuery(() =>
    orpc.elections.states.queryOptions({ input: { id } }),
  );
</script>

{#if states.data != null}
  {const drawn = $derived(states.data)}
  <section class="col-span-12 grid gap-2 bg-card px-3.5 py-3">
    <h2 class="text-[0.8125rem] font-bold">{m.states_title()}</h2>
    {#key drawn.tiles}
      <PlaceMap
        places={drawn.places}
        isTally
        colors={drawn.places.legend.map((item) => swatch(item.color, item.key))}
        tiles={drawn.tiles}
        own={null}
        frame={drawn.frame}
        approximate={[]}
        noun={drawn.places.levels[0]?.noun ?? ""}
        votes={m.col_seats()}
        names={drawn.places.legend.map((item) => item.short ?? item.label)}
        levels={[]}
        span={null}
      />
    {/key}
  </section>
{/if}
